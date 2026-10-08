import contextlib
import datetime
import re
import time
import threading
import json

import docker
import httpx
from docker.types import LogConfig

from .safety import Rejected
from .spring_plan import SPRING_PRESETS, service_plan, health_path


def runtime_name(dep):
    if not re.fullmatch('[a-f0-9]{32}', dep):
        raise Rejected('잘못된 deployment ID')
    return 'runtime-' + dep


def labels(cfg, row, kind='runtime'):
    return {'campus.instance': cfg.instance, 'campus.kind': kind,
            'campus.project': row['project_id'], 'campus.deployment': row['id'], 'campus.source-sha': row['sha']}


class Runtime:
    def __init__(self, worker):
        self.worker, self.cfg, self.db = worker, worker.cfg, worker.db
        self.log_lock = threading.Lock()

    def state(self, dep, state, error=None):
        with self.db.tx() as con:
            self.worker.guard(con)
            con.execute('UPDATE deployments SET runtime_state=?,runtime_error=? WHERE id=?', (state, error, dep))

    def get(self, client, row):
        try:
            container = client.containers.get(runtime_name(row['id']))
        except docker.errors.NotFound:
            return None
        if any(container.labels.get(k) != v for k, v in labels(self.cfg, row).items()):
            raise Rejected('runtime 컨테이너 이름 충돌: 소유권이 일치하지 않습니다.')
        if container.image.id != row['runtime_image']:
            raise Rejected('runtime 이미지가 저장된 배포 이미지와 다릅니다.')
        return container

    def capture(self, row, container):
        with self.log_lock:
            self._capture(row, container)

    def _capture(self, row, container):
        previous = self.db.one('SELECT runtime_log_container,runtime_log_cursor,runtime_log_bytes FROM deployments WHERE id=?', (row['id'],))
        if previous['runtime_log_bytes'] >= self.cfg.log_limit:
            return
        cursor = previous['runtime_log_cursor'] if previous['runtime_log_container'] == container.id else None
        options = {'timestamps': True, 'tail': 1000}
        if cursor:
            options['since'] = int(datetime.datetime.fromisoformat(cursor[:19] + '+00:00').timestamp())
        raw = container.logs(stdout=True, stderr=True, **options).decode('utf-8', errors='replace')
        lines, last = [], cursor
        for line in raw.splitlines(keepends=True):
            stamp, _, _ = line.partition(' ')
            if cursor and stamp <= cursor:
                continue
            lines.append(line)
            last = stamp
        if lines:
            self.db.log(row['id'], ''.join(lines), 'runtime')
        with self.db.tx() as con:
            self.worker.guard(con)
            con.execute('UPDATE deployments SET runtime_log_container=?,runtime_log_cursor=? WHERE id=?', (container.id, last, row['id']))

    def start(self, row, recreate=False):
        self.worker.check(row['id'])
        with contextlib.closing(docker.from_env(timeout=30)) as client:
            network = client.networks.get(self.cfg.runtime_network)
            if network.attrs.get('Labels', {}).get('campus.instance') != self.cfg.instance or not network.attrs.get('Internal'):
                raise Rejected('소유된 internal runtime network가 필요합니다. compose를 실행하세요.')
            image = client.images.get(row['runtime_image'])
            if any(image.labels.get(k) != v for k, v in labels(self.cfg, row, 'runtime-image').items()):
                raise Rejected('보존 이미지 소유 정보 불일치')
            spring = row['preset'] in SPRING_PRESETS
            service_network, uploads, service_env = None, None, {}
            if spring:
                from .services import Services
                self.state(row['id'], 'STARTING')
                service_network, uploads, service_env = Services(self.worker).ensure(client, row)
            container = self.get(client, row)
            if container and (recreate or not container.attrs['State']['Running']):
                self.capture(row, container)
                self.worker.check(row['id'])
                container.remove(force=True, v=True)
                container = None
            if container is None:
                self.state(row['id'], 'STARTING')
                self.worker.check(row['id'])
                container = client.containers.create(image.id, ['java', '-jar', '/app/app.jar'] if spring else ['npm', 'start'], name=runtime_name(row['id']),
                    user='1000:1000', working_dir='/app', environment={'PORT': '8080', 'NODE_ENV': 'production',
                        'HOME': '/tmp', 'NPM_CONFIG_CACHE': '/tmp/npm-cache', **service_env},
                    network=self.cfg.runtime_network, read_only=True, cap_drop=['ALL'],
                    security_opt=['no-new-privileges:true'], nano_cpus=500_000_000,
                    mem_limit=(1024 if spring else 512) * 1024**2, memswap_limit=(1024 if spring else 512) * 1024**2, pids_limit=128, init=True,
                    volumes={uploads: {'bind': service_plan(row)['storage']['mountPath'], 'mode': 'rw'}} if uploads else None,
                    tmpfs={'/tmp': 'rw,nosuid,nodev,noexec,size=67108864,uid=1000,gid=1000,mode=0700'},
                    labels=labels(self.cfg, row), restart_policy={'Name': 'no'},
                    log_config=LogConfig(type='json-file', config={'max-size': '2m', 'max-file': '1'}))
                if service_network:
                    service_network.connect(container)
                container.start()
                self.db.log(row['id'], '[runtime] 보존 이미지에서 시작; PORT=8080; host port 없음\n', 'runtime')
            return container.id

    def health(self, row):
        spring = row['preset'] in SPRING_PRESETS
        timeout = self.cfg.spring_health_timeout if spring else self.cfg.health_timeout
        settings = json.loads(row['settings'])
        path = health_path(settings.get('health_path', '/'))
        reachable = row['preset'] == 'SPRING_BOOT_JAR' and settings.get('health_policy') == 'http-reachable'
        deadline, last = time.monotonic() + timeout, '응답 없음'
        self.state(row['id'], 'HEALTH_CHECK')
        host = f'd-{row["id"]}.{self.cfg.domain}' + (':' + self.cfg.port if self.cfg.port else '')
        with contextlib.closing(docker.from_env(timeout=10)) as client, httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as http:
            while time.monotonic() < deadline:
                self.worker.check(row['id'])
                container = self.get(client, row)
                if not container:
                    raise Rejected('runtime 컨테이너가 없습니다.')
                self.capture(row, container)
                if not container.attrs['State']['Running']:
                    raise Rejected(f'runtime 종료: exit {container.attrs["State"]["ExitCode"]}; runtime 로그를 확인하세요.')
                try:
                    # Stream only response headers: an infinite body must not stall the worker.
                    with http.stream('GET', self.cfg.gateway + path, headers={'Host': host}) as response:
                        last = {502: 'UPSTREAM_CONNECTION_FAILURE (HTTP 502)', 504: 'UPSTREAM_TIMEOUT (HTTP 504)'}.get(response.status_code, f'HTTP {response.status_code}')
                        if 200 <= response.status_code < 300 or (reachable and response.status_code == 404):
                            container.reload()
                            if container.attrs['State']['Running']:
                                self.state(row['id'], 'RUNNING')
                                warning = 'HTTP 서버 연결만 확인했습니다. API 기능 검증은 별도이며 manifest health.path 지정 시 2xx를 요구합니다.' if reachable else ''
                                self.db.log(row['id'], '[health] GET ' + path + ' → ' + last + '; policy=' + ('http-reachable' if reachable else 'strict-2xx') + '\n' + warning + '\n', 'runtime')
                                return {'path': path, 'status': response.status_code, 'policy': 'http-reachable' if reachable else 'strict-2xx', 'warning': warning, 'checked': time.time()}
                except httpx.TimeoutException:
                    last = 'GATEWAY_TIMEOUT'
                except httpx.ConnectError:
                    last = 'GATEWAY_CONNECTION_FAILURE'
                except httpx.HTTPError as exc:
                    last = type(exc).__name__
                time.sleep(.5)
        policy = '2xx 또는 기본 /의 404 (HTTP 연결 확인)' if reachable else '2xx'
        raise Rejected(f'HEALTH_DEADLINE: HTTP health check 실패 ({timeout}초, 마지막 원인: {last}). 서버가 0.0.0.0:8080에서 {path}에 {policy}를 응답해야 합니다. 런타임 로그를 확인하세요.')

    def remove(self, row, images=False):
        self.worker.check()
        with contextlib.closing(docker.from_env(timeout=30)) as client:
            container = self.get(client, row)
            if container:
                self.capture(row, container)
                self.worker.check()
                container.remove(force=True, v=True)
            if images:
                owned = labels(self.cfg, row, 'runtime-image')
                # Docker may retain intermediate configs before its last source-SHA LABEL.
                # Deployment/instance/project/kind still prove ownership of these images.
                del owned['campus.source-sha']
                self.remove_images(client, owned, row['sha'])
        self.state(row['id'], 'STOPPED')

    def remove_images(self, client, owned, source_sha=None):
        filters = {'label': [f'{k}={v}' for k, v in owned.items()]}
        deadline = time.monotonic() + 30
        while True:
            images = client.images.list(all=True, filters=filters)
            if not images:
                return
            progress = False
            for image in images:
                self.worker.check()
                if any(image.labels.get(k) != v for k, v in owned.items()):
                    raise Rejected('이미지 정리 소유권 불일치')
                if source_sha and image.labels.get('campus.source-sha') not in (None, source_sha):
                    raise Rejected('이미지 정리 소스 SHA 불일치')
                try:
                    client.images.remove(image.id, force=False, noprune=True)
                    progress = True
                except docker.errors.ImageNotFound:
                    progress = True
                except docker.errors.APIError as exc:
                    if exc.status_code != 409:
                        raise
                    # A child image may be removed later in this pass; retry its parent.
            if not progress or time.monotonic() > deadline:
                raise Rejected('소유 이미지가 아직 참조되고 있어 삭제를 완료하지 못했습니다. 다른 프로젝트 이미지는 제거하지 않습니다.')

    def remove_project_images(self, pid):
        from .services import project_id
        owned = {'campus.instance': self.cfg.instance, 'campus.project': project_id(pid), 'campus.kind': 'runtime-image'}
        with contextlib.closing(docker.from_env(timeout=30)) as client:
            self.remove_images(client, owned)

    def retain(self, pid):
        project = self.db.one('SELECT production_id FROM projects WHERE id=?', (pid,))
        rows = self.db.all("SELECT * FROM deployments WHERE project_id=? AND preset IN ('NODE_SERVER','SPRING_BOOT','SPRING_BOOT_JAR','SPRING_BOOT_VITE') AND status='READY' ORDER BY created DESC", (pid,))
        keep = {project['production_id']}
        if rows:
            keep.add(rows[0]['id'])
        for row in rows:
            if row['id'] not in keep and row['runtime_state'] != 'STOPPED':
                try:
                    self.remove(row)
                except Exception as exc:
                    from .worker import LeaseLost
                    if isinstance(exc, LeaseLost):
                        raise
                    self.db.log(row['id'], '[retention] 정리 재시도 필요: ' + str(exc)[:500] + '\n', 'runtime')

    def log_loop(self):
        # Log polling remains available while the serial worker runs another build.
        while not self.worker.stop.wait(2):
            try:
                self.worker.check()
                rows = self.db.all("SELECT d.* FROM deployments d JOIN projects p ON p.id=d.project_id WHERE d.preset IN ('NODE_SERVER','SPRING_BOOT','SPRING_BOOT_JAR','SPRING_BOOT_VITE') AND d.runtime_state IN ('STARTING','HEALTH_CHECK','RUNNING','UNAVAILABLE') AND p.deleted=0")
                with contextlib.closing(docker.from_env(timeout=5)) as client:
                    for row in rows:
                        try:
                            container = self.get(client, row)
                            if container:
                                self.capture(row, container)
                        except docker.errors.DockerException:
                            pass  # Concurrent teardown or daemon restart; retry next poll.
            except Exception:
                if self.worker.lost.is_set():
                    return

    def reconcile(self, startup=False):
        rows = self.db.all("SELECT d.*,p.production_id FROM deployments d JOIN projects p ON p.id=d.project_id WHERE d.preset IN ('NODE_SERVER','SPRING_BOOT','SPRING_BOOT_JAR','SPRING_BOOT_VITE') AND d.status='READY' AND p.deleting=0 AND p.deleted=0")
        for row in rows:
            try:
                with contextlib.closing(docker.from_env(timeout=10)) as client:
                    container = self.get(client, row)
                    if container:
                        self.capture(row, container)
                    running = bool(container and container.attrs['State']['Running'])
                    if running and row['preset'] in SPRING_PRESETS:
                        from .services import Services
                        running = Services(self.worker).available(client, row)
                if row['production_id'] == row['id'] and (startup or not running or row['runtime_state'] != 'RUNNING'):
                    self.start(row)
                    self.health(row)
                elif not running and row['runtime_state'] != 'STOPPED':
                    self.state(row['id'], 'UNAVAILABLE', 'Preview runtime이 없습니다. 운영 반영 시 보존 이미지에서 다시 시작합니다.')
                elif startup and running:
                    self.health(row)
            except Exception as exc:
                from .worker import LeaseLost, Canceled
                if isinstance(exc, (LeaseLost, Canceled)):
                    raise
                self.state(row['id'], 'UNAVAILABLE', str(exc)[:1000])
                if startup or row['runtime_state'] != 'UNAVAILABLE':
                    self.db.log(row['id'], '[recovery] ' + str(exc)[:1000] + '\n', 'runtime')
        for pid in {row['project_id'] for row in rows}:
            self.retain(pid)
