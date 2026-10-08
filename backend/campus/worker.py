import contextlib
import json
import re
import shutil
import threading
import time
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .config import Settings
from .db import DB, uid
from .spring_plan import SPRING_PRESETS, VITE_PRESETS, source_allowed
from .safety import Rejected, extract_zip, detect, blocked, collect_static, remove_owned


class Canceled(Exception):
    pass


class LeaseLost(Exception):
    pass


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'img') and a.get('src'):
            self.assets.append(a['src'])
        if tag == 'link' and a.get('rel') in ('stylesheet', 'modulepreload') and a.get('href'):
            self.assets.append(a['href'])


def probe_artifact(cfg, dep):
    host = f'd-{dep}.{cfg.domain}' + (':' + cfg.port if cfg.port else '')
    checks = []
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        response = client.get(cfg.gateway + '/', headers={'Host': host, 'Accept': 'text/html'})
        if response.status_code != 200 or 'text/html' not in response.headers.get('content-type', ''):
            raise Rejected(f'게이트웨이 HTML 검사 실패: HTTP {response.status_code}')
        parser = AssetParser()
        parser.feed(response.text)
        checks.append({'path': '/', 'status': response.status_code, 'type': response.headers['content-type']})
        for asset in dict.fromkeys(parser.assets):
            parsed = urlsplit(urljoin('http://' + host + '/', asset))
            if parsed.netloc != host or parsed.scheme not in ('http', 'https'):
                continue
            path = parsed.path + ('?' + parsed.query if parsed.query else '')
            r = client.get(cfg.gateway + path, headers={'Host': host})
            content_type = r.headers.get('content-type', '')
            if r.status_code != 200:
                raise Rejected(f'정적 자원 검사 실패: {path} HTTP {r.status_code}')
            if parsed.path.endswith(('.js', '.mjs')) and 'javascript' not in content_type:
                raise Rejected(f'JavaScript MIME 검사 실패: {path}')
            if parsed.path.endswith('.css') and 'text/css' not in content_type:
                raise Rejected(f'CSS MIME 검사 실패: {path}')
            checks.append({'path': path, 'status': r.status_code, 'type': content_type})
    return checks


class Worker:
    def __init__(self, settings=None):
        self.cfg = settings or Settings()
        self.db = DB(self.cfg)
        self.db.initialize()
        self.owner = uid()
        self.stop = threading.Event()
        self.lost = threading.Event()
        from .runtime import Runtime
        self.runtime = Runtime(self)

    def guard(self, con):
        row = con.execute('SELECT * FROM worker_lease WHERE slot=1').fetchone()
        if row['owner'] != self.owner or row['expires'] < time.time() or self.lost.is_set():
            raise LeaseLost('worker lease lost')

    def acquire(self):
        with self.db.tx() as con:
            lease = con.execute('SELECT * FROM worker_lease WHERE slot=1').fetchone()
            if lease['expires'] > time.time() and lease['owner'] != self.owner:
                return False
            con.execute('UPDATE worker_lease SET owner=?,expires=? WHERE slot=1', (self.owner, time.time() + self.cfg.lease_seconds))
        return True

    def heartbeat(self):
        while not self.stop.wait(2):
            try:
                with self.db.tx() as con:
                    row = con.execute('UPDATE worker_lease SET expires=? WHERE slot=1 AND owner=? AND expires>?',
                                      (time.time() + self.cfg.lease_seconds, self.owner, time.time()))
                    if row.rowcount != 1:
                        self.lost.set()
                        return
            except Exception:
                self.lost.set()
                return

    def cleanup_containers(self, dep=None, project=None):
        # No broad prune: select only this installation's owned builder resources.
        import docker
        client = docker.from_env(timeout=15)
        labels = [f'campus.instance={self.cfg.instance}', 'campus.kind=build']
        if dep:
            labels.append('campus.deployment=' + dep)
        if project:
            labels.append('campus.project=' + project)
        try:
            for container in client.containers.list(all=True, filters={'label': labels}):
                container.remove(force=True, v=True)
            volume_labels = [label if label != 'campus.kind=build' else 'campus.kind=build-work' for label in labels]
            for volume in client.volumes.list(filters={'label': volume_labels}):
                volume.remove()
        finally:
            client.close()

    def recover(self):
        # Also collect orphaned resources from failures during cleanup after a job finished.
        if self.db.one("SELECT 1 FROM deployments WHERE preset IN ('VITE_STATIC','NODE_SERVER','SPRING_BOOT','SPRING_BOOT_JAR','SPRING_BOOT_VITE') LIMIT 1"):
            self.cleanup_containers()
        stale = self.db.all("SELECT * FROM jobs WHERE status='RUNNING'")
        for job in stale:
            if job['kind'] == 'DEPLOY':
                dep = self.db.one('SELECT * FROM deployments WHERE id=?', (job['subject'],))
                if dep and dep['preset'] in ('VITE_STATIC', 'NODE_SERVER', 'SPRING_BOOT', 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE'):
                    self.cleanup_containers(dep=dep['id'])
                if dep and dep['status'] not in ('READY', 'FAILED', 'CANCELED'):
                    self.fail(dep['id'], 'FAILED', '워커 재시작: 중단된 작업을 복구·정리했습니다. 운영 배포는 보존됩니다.')
                    self.clean_deployment(dep['id'])
                if dep and dep['preset'] in ('NODE_SERVER', 'SPRING_BOOT', 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE') and dep['status'] != 'READY':
                    self.runtime.remove(dep, images=True)
            elif job['kind'] == 'SWITCH':
                with self.db.tx() as con:
                    self.guard(con)
                    con.execute("UPDATE operations SET status='FAILED',finished=?,error='워커 재시작 중 전환 중단. 기존 운영 포인터를 유지합니다.' WHERE id=? AND status!='DONE'", (time.time(), job['subject']))
            elif job['kind'] == 'INSPECT':
                with self.db.tx() as con:
                    self.guard(con)
                    con.execute("UPDATE sources SET status='FAILED',error='워커 재시작 중 소스 검사 중단. 다시 업로드/조회하세요.' WHERE id=? AND status!='READY'", (job['subject'],))
                remove_owned(self.cfg.data, self.cfg.data / 'work' / job['subject'])
                source = self.db.one('SELECT status FROM sources WHERE id=?', (job['subject'],))
                if source['status'] != 'READY':
                    remove_owned(self.cfg.data, self.cfg.data / 'sources' / job['subject'])
                    remove_owned(self.cfg.data, self.cfg.data / 'uploads' / (job['subject'] + '.zip'))
            with self.db.tx() as con:
                self.guard(con)
                con.execute('UPDATE jobs SET status=?,finished=? WHERE id=?', ('QUEUED' if job['kind'] == 'DELETE' else 'DONE', time.time(), job['id']))

        # A stop can land after the terminal status was committed but before
        # finally removed work/staging. Preserve READY artifacts and sources.
        for dep in self.db.all("SELECT id FROM deployments WHERE status IN ('READY','FAILED','CANCELED')"):
            self.clean_deployment(dep['id'])

    def claim(self):
        with self.db.tx() as con:
            self.guard(con)
            row = con.execute("SELECT * FROM jobs WHERE status='QUEUED' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            con.execute("UPDATE jobs SET status='RUNNING',owner=? WHERE id=? AND status='QUEUED'", (self.owner, row['id']))
            return dict(row)

    def check(self, dep=None):
        with self.db.tx() as con:
            self.guard(con)
            if dep:
                row = con.execute('SELECT d.cancel,p.deleting FROM deployments d JOIN projects p ON p.id=d.project_id WHERE d.id=?', (dep,)).fetchone()
                if not row or row['cancel'] or row['deleting']:
                    raise Canceled('사용자가 요청한 취소/삭제')

    def stage(self, dep, state):
        self.check(dep)
        with self.db.tx() as con:
            self.guard(con)
            row = con.execute('SELECT stages,cancel FROM deployments WHERE id=?', (dep,)).fetchone()
            if row['cancel']:
                raise Canceled()
            stages = json.loads(row['stages'])
            stages[state] = time.time()
            con.execute('UPDATE deployments SET status=?,stages=? WHERE id=?', (state, json.dumps(stages), dep))
        self.db.log(dep, f'[{state}] {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}\n')

    def fail(self, dep, status, error, exit_code=None):
        with self.db.tx() as con:
            self.guard(con)
            row = con.execute('SELECT stages FROM deployments WHERE id=?', (dep,)).fetchone()
            stages = json.loads(row['stages'])
            stages[status] = time.time()
            con.execute('UPDATE deployments SET status=?,error=?,finished=?,exit_code=COALESCE(?,exit_code),stages=?,artifact=NULL WHERE id=? AND status!=\'READY\'',
                        (status, str(error)[:2000], time.time(), exit_code, json.dumps(stages), dep))
        self.db.log(dep, f'[{status}] {str(error)[:2000]}\n')

    def inspect(self, sid):
        source = self.db.one('SELECT * FROM sources WHERE id=?', (sid,))
        archive = self.cfg.data / 'uploads' / (sid + '.zip')
        work = self.cfg.data / 'work' / sid
        dest = self.cfg.data / 'sources' / sid
        try:
            if shutil.disk_usage(self.cfg.data).free < self.cfg.min_free + self.cfg.extract_limit:
                raise Rejected('소스 검사를 위한 디스크 여유 공간이 부족합니다.')
            with self.db.tx() as con:
                self.guard(con)
                con.execute("UPDATE sources SET status='FETCHING' WHERE id=?", (sid,))
            sha, branch = source['sha'], None
            if source['kind'] == 'GIT':
                repo = source['locator'].removeprefix('https://github.com/')
                with httpx.Client(timeout=30, trust_env=False, follow_redirects=False, headers={'User-Agent': 'Campus-Deploy-P0', 'Accept': 'application/vnd.github+json'}) as client:
                    info = client.get('https://api.github.com/repos/' + repo)
                    info.raise_for_status()
                    body = info.json()
                    if body.get('private') or body.get('disabled'):
                        raise Rejected('접근 가능한 공개 저장소만 지원합니다.')
                    branch = body['default_branch']
                    commit = client.get('https://api.github.com/repos/' + repo + '/commits/' + quote(branch, safe=''))
                    commit.raise_for_status()
                    sha = commit.json()['sha']
                    if not re.fullmatch('[a-f0-9]{40}', sha):
                        raise Rejected('잘못된 Git commit SHA')
                    size, started = 0, time.monotonic()
                    with client.stream('GET', 'https://codeload.github.com/' + repo + '/zip/' + sha) as response:
                        response.raise_for_status()
                        with archive.open('xb') as out:
                            for chunk in response.iter_bytes(65536):
                                self.check()
                                size += len(chunk)
                                if size > self.cfg.upload_limit or time.monotonic() - started > 120:
                                    raise Rejected('Git 다운로드 크기/시간 제한 초과')
                                out.write(chunk)
            root = extract_zip(archive, work, self.cfg)
            preset = detect(root)
            facts, plan = None, None
            if preset in SPRING_PRESETS:
                from .spring_plan import inspect
                facts, plan = inspect(root)
            self.check()
            # Keep only source inputs safe to pass to a builder; no .env, .npmrc, .git or node_modules.
            dest.mkdir()
            for path in root.rglob('*'):
                rel = path.relative_to(root).as_posix()
                if path.is_file() and source_allowed(rel):
                    if path.stat().st_size < 1024 and path.read_bytes().startswith(b'version https://git-lfs.github.com/spec/v1'):
                        raise Rejected('Git LFS 포인터는 지원하지 않습니다.')
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
            with self.db.tx() as con:
                self.guard(con)
                con.execute("UPDATE sources SET status='READY',preset=?,sha=?,branch=?,analysis=?,plan=? WHERE id=?", (preset, sha, branch, json.dumps(facts), json.dumps(plan), sid))
        except LeaseLost:
            raise
        except Exception as exc:
            remove_owned(self.cfg.data, dest)
            with self.db.tx() as con:
                self.guard(con)
                con.execute("UPDATE sources SET status='FAILED',error=? WHERE id=?", (str(exc)[:2000], sid))
        finally:
            if not self.lost.is_set():
                remove_owned(self.cfg.data, work)
                remove_owned(self.cfg.data, archive)

    def clean_deployment(self, dep):
        self.check()
        for area in ('work', 'artifacts/.staging'):
            remove_owned(self.cfg.data, self.cfg.data / area / dep)
        row = self.db.one('SELECT status FROM deployments WHERE id=?', (dep,))
        if not row or row['status'] != 'READY':
            remove_owned(self.cfg.data, self.cfg.data / 'artifacts' / dep)

    def deploy(self, dep):
        row = self.db.one('SELECT * FROM deployments WHERE id=?', (dep,))
        source = self.cfg.data / 'sources' / row['source_id']
        staging = self.cfg.data / 'artifacts' / '.staging' / dep
        published = self.cfg.data / 'artifacts' / dep
        try:
            self.stage(dep, 'FETCHING')
            if not source.is_dir():
                raise Rejected('검사된 소스가 없습니다. 다시 업로드하세요.')
            self.db.log(dep, f"검사된 소스: {row['sha']}\n프리셋: {row['preset']}\n")
            if shutil.disk_usage(self.cfg.data).free < self.cfg.min_free + self.cfg.artifact_limit:
                raise Rejected('디스크 여유 공간 부족')
            self.stage(dep, 'BUILDING')
            if row['preset'] == 'STATIC':
                self.db.log(dep, 'STATIC: npm 빌드 불필요. 검증된 정적 파일을 수집합니다.\n')
                validation = collect_static(source, staging, self.cfg)
                with self.db.tx() as con:
                    self.guard(con)
                    con.execute('UPDATE deployments SET exit_code=0 WHERE id=?', (dep,))
            elif row['preset'] in SPRING_PRESETS:
                from .spring import build_spring
                validation = build_spring(self, row, source, staging)
            else:
                from .builder import build
                validation = build(self, row, source, staging)
            if row['preset'] in VITE_PRESETS:
                self.check(dep)
                if published.exists():
                    raise Rejected('불변 산출물 경로가 이미 존재합니다.')
                staging.rename(published)
                with self.db.tx() as con:
                    self.guard(con)
                    con.execute('UPDATE deployments SET artifact=? WHERE id=?', (dep, dep))
            if row['preset'] in ('NODE_SERVER', 'SPRING_BOOT', 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE'):
                row = self.db.one('SELECT * FROM deployments WHERE id=?', (dep,))
                self.stage(dep, 'STARTING')
                self.runtime.start(row)
                self.stage(dep, 'HEALTH_CHECK')
                validation['http_checks'] = [self.runtime.health(row)]
                if row['preset'] in VITE_PRESETS:
                    validation['http_checks'] += probe_artifact(self.cfg, dep)
            else:
                self.stage(dep, 'PUBLISHING')
                self.check(dep)
                if published.exists():
                    raise Rejected('불변 산출물 경로가 이미 존재합니다.')
                staging.rename(published)
                with self.db.tx() as con:
                    self.guard(con)
                    con.execute('UPDATE deployments SET artifact=? WHERE id=?', (dep, dep))
                validation['http_checks'] = probe_artifact(self.cfg, dep)
            self.check(dep)
            with self.db.tx() as con:
                self.guard(con)
                current = con.execute('SELECT d.cancel,p.deleting,p.production_id FROM deployments d JOIN projects p ON p.id=d.project_id WHERE d.id=?', (dep,)).fetchone()
                if current['cancel'] or current['deleting']:
                    raise Canceled()
                stages = json.loads(con.execute('SELECT stages FROM deployments WHERE id=?', (dep,)).fetchone()['stages'])
                stages['READY'] = time.time()
                con.execute("UPDATE deployments SET status='READY',finished=?,validation=?,stages=? WHERE id=?", (time.time(), json.dumps(validation), json.dumps(stages), dep))
                if not current['production_id']:
                    con.execute('UPDATE projects SET production_id=? WHERE id=?', (dep, row['project_id']))
                    con.execute('INSERT INTO transitions(project_id,previous_id,deployment_id,kind,created) VALUES(?,NULL,?,?,?)', (row['project_id'], dep, 'promote', time.time()))
            self.db.log(dep, '[READY] HTTP 검사 통과. 배포 준비 완료.\n')
            if row['preset'] in ('NODE_SERVER', 'SPRING_BOOT', 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE'):
                self.runtime.retain(row['project_id'])
        except LeaseLost:
            raise
        except Canceled:
            self.fail(dep, 'CANCELED', '사용자가 취소 또는 프로젝트 삭제를 요청했습니다.')
        except Exception as exc:
            self.fail(dep, 'FAILED', str(exc), getattr(exc, 'exit_code', None))
        finally:
            if not self.lost.is_set():
                self.clean_deployment(dep)
                current = self.db.one('SELECT * FROM deployments WHERE id=?', (dep,))
                if current['preset'] in ('NODE_SERVER', 'SPRING_BOOT', 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE') and current['status'] != 'READY':
                    self.runtime.remove(current, images=True)

    def switch(self, operation):
        op = self.db.one('SELECT * FROM operations WHERE id=?', (operation,))
        row = self.db.one('SELECT * FROM deployments WHERE id=?', (op['deployment_id'],))
        healthy = False
        try:
            self.check(row['id'])
            with self.db.tx() as con:
                self.guard(con)
                con.execute("UPDATE operations SET status='HEALTH_CHECK' WHERE id=?", (operation,))
            project = self.db.one('SELECT * FROM projects WHERE id=?', (op['project_id'],))
            if project['production_id'] != op['previous_id']:
                raise Rejected('운영 버전이 다른 요청으로 변경되어 이 전환을 취소했습니다.')
            # Rollback deliberately reconstructs the runtime from its saved image.
            # Never destroy the current production when rolling back to itself.
            self.runtime.start(row, recreate=op['kind'] == 'rollback' and project['production_id'] != row['id'])
            self.runtime.health(row)
            healthy = True
            with self.db.tx() as con:
                self.guard(con)
                current = con.execute('SELECT production_id,deleting FROM projects WHERE id=?', (op['project_id'],)).fetchone()
                if current['deleting'] or current['production_id'] != op['previous_id']:
                    raise Rejected('운영 상태가 변경되어 전환을 취소했습니다.')
                if current['production_id'] != row['id']:
                    con.execute('UPDATE projects SET production_id=? WHERE id=?', (row['id'], op['project_id']))
                    con.execute('INSERT INTO transitions(project_id,previous_id,deployment_id,kind,created) VALUES(?,?,?,?,?)',
                        (op['project_id'], current['production_id'], row['id'], op['kind'], time.time()))
                con.execute("UPDATE operations SET status='DONE',finished=? WHERE id=?", (time.time(), operation))
            self.runtime.retain(op['project_id'])
        except LeaseLost:
            raise
        except Exception as exc:
            with self.db.tx() as con:
                self.guard(con)
                con.execute("UPDATE operations SET status='FAILED',finished=?,error=? WHERE id=? AND status!='DONE'", (time.time(), str(exc)[:1000], operation))
            if not healthy:
                self.runtime.state(row['id'], 'UNAVAILABLE', str(exc)[:1000])
            self.db.log(row['id'], '[switch failed] ' + str(exc)[:1000] + '\n', 'runtime')

    def delete(self, pid):
        self.check()
        rows = self.db.all('SELECT * FROM deployments WHERE project_id=?', (pid,))
        if any(r['preset'] in ('VITE_STATIC', 'NODE_SERVER', 'SPRING_BOOT', 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE') for r in rows):
            self.cleanup_containers(project=pid)
        for row in rows:
            if row['preset'] in ('NODE_SERVER', 'SPRING_BOOT', 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE'):
                self.runtime.remove(row, images=True)
            for area in ('artifacts', 'artifacts/.staging', 'work'):
                remove_owned(self.cfg.data, self.cfg.data / area / row['id'])
        if any(r['preset'] in ('NODE_SERVER', *SPRING_PRESETS) for r in rows):
            self.runtime.remove_project_images(pid)
        if any(r['preset'] in SPRING_PRESETS for r in rows):
            import docker
            from .services import Services
            with contextlib.closing(docker.from_env(timeout=30)) as client:
                Services(self).delete(client, pid)
        source_ids = {r['source_id'] for r in rows}
        for sid in source_ids:
            referenced = self.db.one('''SELECT 1 AS found FROM deployments d JOIN projects p ON p.id=d.project_id
              WHERE d.source_id=? AND p.id!=? AND p.deleted=0 LIMIT 1''', (sid, pid))
            if not referenced:
                remove_owned(self.cfg.data, self.cfg.data / 'sources' / sid)
        with self.db.tx() as con:
            self.guard(con)
            con.execute('UPDATE deployments SET artifact=NULL WHERE project_id=?', (pid,))
            con.execute("UPDATE projects SET deleted=1,cleanup='라우트·산출물·소스·소유 작업 자원 정리 완료 (이력 보존)' WHERE id=?", (pid,))

    def tick(self):
        job = self.claim()
        if not job:
            return False
        try:
            {'INSPECT': self.inspect, 'DEPLOY': self.deploy, 'DELETE': self.delete, 'SWITCH': self.switch}[job['kind']](job['subject'])
        except LeaseLost:
            raise
        except Exception as exc:
            if job['kind'] == 'DELETE':
                with self.db.tx() as con:
                    self.guard(con)
                    con.execute('UPDATE projects SET cleanup=? WHERE id=?', ('정리 실패; 워커 재시작 시 재시도: ' + str(exc)[:500], job['subject']))
                raise  # Leave RUNNING for recovery, never falsely report cleanup success.
            raise
        with self.db.tx() as con:
            self.guard(con)
            con.execute("UPDATE jobs SET status='DONE',finished=? WHERE id=? AND owner=?", (time.time(), job['id'], self.owner))
        return True

    def run(self):
        while not self.acquire():
            time.sleep(2)
        thread = threading.Thread(target=self.heartbeat, daemon=True)
        thread.start()
        log_thread = threading.Thread(target=self.runtime.log_loop, daemon=True)
        log_thread.start()
        try:
            self.recover()
            self.runtime.reconcile(startup=True)
            checked = time.monotonic()
            while not self.stop.is_set():
                if not self.tick():
                    if time.monotonic() - checked >= 5:
                        self.runtime.reconcile()
                        checked = time.monotonic()
                    self.stop.wait(0.5)
        finally:
            self.stop.set()
            thread.join(timeout=3)
            log_thread.join(timeout=6)
            with contextlib.suppress(Exception):
                with self.db.tx() as con:
                    con.execute('UPDATE worker_lease SET expires=0 WHERE owner=?', (self.owner,))


if __name__ == '__main__':
    Worker().run()
