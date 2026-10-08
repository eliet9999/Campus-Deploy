"""Owned, persistent per-project MySQL / Redis / uploads resources.

Secrets live in the worker data volume, outside the gateway's metadata mount.
No submitted compose/Dockerfile, host paths or public database ports are used.
"""
import base64
import json
import re
import secrets
import time

import docker

from .safety import Rejected
from .spring_plan import service_plan, VITE_PRESETS


def project_id(value):
    if not re.fullmatch('[a-f0-9]{32}', value):
        raise Rejected('잘못된 project ID')
    return value


class Services:
    def __init__(self, worker):
        self.worker, self.cfg = worker, worker.cfg

    def labels(self, pid, kind):
        return {'campus.instance': self.cfg.instance, 'campus.project': project_id(pid), 'campus.kind': kind}

    def name(self, pid, kind):
        return f'{self.cfg.instance}-{project_id(pid)}-{kind}'

    def owned(self, obj, pid, kind):
        attrs = obj.attrs
        found = attrs.get('Labels') or attrs.get('Config', {}).get('Labels', {})
        if any(found.get(k) != v for k, v in self.labels(pid, kind).items()):
            raise Rejected('서비스 자원 소유권 충돌: ' + kind)
        return obj

    def credentials(self, pid, client, enabled=None):
        enabled = enabled if enabled is not None else ('mysql', 'redis')
        path = self.cfg.data / 'services' / (project_id(pid) + '.json')
        if not path.exists():
            for kind in ('mysql-data', 'redis-data', 'uploads-data'):
                try:
                    self.owned(client.volumes.get(self.name(pid, kind)), pid, kind)
                except docker.errors.NotFound:
                    pass
                else:
                    raise Rejected('기존 DB/저장소 볼륨의 서비스 자격증명 파일이 없습니다. 백업에서 복원하세요. 새 암호로 덮어쓰지 않습니다.')
            values = {'mysql': secrets.token_hex(32), 'root': secrets.token_hex(32), 'redis': secrets.token_hex(32),
                      'jwt': base64.b64encode(secrets.token_bytes(32)).decode()}
            # The single lease-holding worker creates credentials once. Never replace on restart.
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(values), 'utf-8')
            temporary.chmod(0o600)
            temporary.replace(path)
        values = json.loads(path.read_text('utf-8'))
        changed = False
        for name in enabled:
            if name in ('mysql', 'redis') and name + '_image' not in values:
                values[name + '_image'] = client.images.get(getattr(self.cfg, name + '_image')).id
                changed = True
        if changed:
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(values), 'utf-8')
            temporary.chmod(0o600)
            temporary.replace(path)
        return values

    def volume(self, client, row, kind):
        pid = row['project_id']
        name = self.name(pid, kind)
        try:
            volume = self.owned(client.volumes.get(name), pid, kind)
        except docker.errors.NotFound:
            volume = client.volumes.create(name, labels=self.labels(pid, kind))
        # Only a fixed operator image/command gets CHOWN, with no network or host binds.
        # Repeating this after an interrupted initialization changes only the volume root.
        helper = client.containers.create(client.images.get(self.cfg.java_image).id,
            ['sh', '-c', 'chown 1000:1000 /owned && chmod 700 /owned'],
            user='0:0', network_mode='none', read_only=True, cap_drop=['ALL'], cap_add=['CHOWN','FOWNER'],
            security_opt=['no-new-privileges:true'], mem_limit=64*1024**2, pids_limit=32,
            mounts=[docker.types.Mount('/owned', name, type='volume', no_copy=True)],
            labels={**self.labels(pid, 'build'), 'campus.deployment': row['id']})
        try:
            self.worker.check(row['id'])
            helper.start()
            if helper.wait(timeout=20)['StatusCode'] != 0:
                raise Rejected('서비스 저장소 초기화 실패')
        finally:
            helper.remove(force=True, v=True)
        return volume.name

    def network(self, client, pid):
        try:
            network = self.owned(client.networks.get(self.name(pid, 'data-net')), pid, 'data-net')
        except docker.errors.NotFound:
            network = client.networks.create(self.name(pid, 'data-net'), internal=True, labels=self.labels(pid, 'data-net'))
        if not network.attrs.get('Internal'):
            raise Rejected('DB 전용 network는 internal이어야 합니다.')
        return network

    def service(self, client, row, kind, image, volume, env, command, destination, memory):
        pid, name = row['project_id'], self.name(row['project_id'], kind)
        try:
            container = self.owned(client.containers.get(name), pid, kind)
            if container.image.id != image:
                raise Rejected('영속 서비스 이미지 불일치; 자동 DB 업그레이드는 하지 않습니다.')
        except docker.errors.NotFound:
            container = client.containers.create(image, command, name=name, user='1000:1000',
                environment=env, network=self.name(pid, 'data-net'), read_only=True,
                cap_drop=['ALL'], security_opt=['no-new-privileges:true'], init=True,
                nano_cpus=500_000_000, mem_limit=memory, memswap_limit=memory, pids_limit=128,
                mounts=[docker.types.Mount(destination, volume, type='volume', no_copy=True)],
                tmpfs={'/tmp': 'rw,nosuid,nodev,size=67108864,uid=1000,gid=1000,mode=0700',
                       '/var/run/mysqld': 'rw,nosuid,nodev,size=16777216,uid=1000,gid=1000,mode=0700'},
                labels=self.labels(pid, kind), restart_policy={'Name': 'no'},
                log_config=docker.types.LogConfig(type='json-file', config={'max-size': '2m', 'max-file': '1'}))
        self.worker.check(row['id'])
        if not container.attrs['State']['Running']:
            container.start()
        return container

    def ready(self, container, kind):
        container.reload()
        if not container.attrs['State']['Running']:
            return False
        command = (['sh', '-c', 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --protocol=tcp -h 127.0.0.1 -u campus -D campus -N -e "SELECT 1"']
                   if kind == 'mysql' else ['sh', '-c', 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping'])
        result = container.exec_run(command, user='1000:1000')
        return result.exit_code == 0 and result.output.strip() == (b'1' if kind == 'mysql' else b'PONG')

    def ensure(self, client, row):
        pid = row['project_id']
        options = service_plan(row)
        enabled = [k for k, v in options.items() if v['enabled']]
        environment = {'SERVER_PORT': '8080', 'SERVER_ADDRESS': '0.0.0.0', 'SERVER_FORWARD_HEADERS_STRATEGY': 'framework',
                       'HOME': '/tmp', 'JAVA_TOOL_OPTIONS': '-XX:MaxRAMPercentage=65 -XX:ActiveProcessorCount=2'}
        if not enabled:
            return None, None, environment
        credentials = self.credentials(pid, client, enabled)
        network = self.network(client, pid) if options['mysql']['enabled'] or options['redis']['enabled'] else None
        volumes = {kind: self.volume(client, row, kind + '-data') for kind in ('mysql', 'redis') if options[kind]['enabled']}
        if options['storage']['enabled']:
            volumes['uploads'] = self.volume(client, row, 'uploads-data')
        containers = {}
        if options['mysql']['enabled']:
            containers['mysql'] = self.service(client, row, 'mysql', credentials['mysql_image'], volumes['mysql'],
            {'MYSQL_DATABASE': 'campus', 'MYSQL_USER': 'campus', 'MYSQL_PASSWORD': credentials['mysql'],
             'MYSQL_ROOT_PASSWORD': credentials['root']},
            ['mysqld', '--innodb-buffer-pool-size=128M', '--max-connections=40', '--mysqlx=OFF'], '/var/lib/mysql', 768*1024**2)
        if options['redis']['enabled']:
            containers['redis'] = self.service(client, row, 'redis', credentials['redis_image'], volumes['redis'],
            {'REDIS_PASSWORD': credentials['redis']},
            ['sh', '-c', 'exec redis-server --appendonly yes --maxmemory 96mb --maxmemory-policy noeviction --requirepass "$REDIS_PASSWORD"'], '/data', 192*1024**2)
        deadline = time.monotonic() + 120
        while not all(self.ready(container, kind) for kind, container in containers.items()):
            self.worker.check(row['id'])
            if time.monotonic() > deadline:
                raise Rejected('MySQL/Redis 준비 timeout. 기존 영속 데이터는 보존됩니다.')
            time.sleep(1)
        self.worker.db.log(row['id'], '[services] ' + ', '.join(enabled) + ' 준비 완료; 프로젝트 전용 network/영속 볼륨, 공개 포트 없음\n', 'runtime')
        production = self.worker.db.one('SELECT production_id FROM projects WHERE id=?', (pid,))
        # Subsequent builds cannot automatically migrate an existing production schema.
        ddl = 'update' if row['preset'] in VITE_PRESETS and not production['production_id'] else 'validate'
        if options['mysql']['enabled']:
            environment.update({
            'SPRING_DATASOURCE_URL': f'jdbc:mysql://{self.name(pid,"mysql")}:3306/campus?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC',
            'SPRING_DATASOURCE_USERNAME': 'campus', 'SPRING_DATASOURCE_PASSWORD': credentials['mysql'],
            'SPRING_JPA_HIBERNATE_DDL_AUTO': ddl, 'SPRING_JPA_SHOW_SQL': 'false'})
        if options['redis']['enabled']:
            environment.update({
            'SPRING_DATA_REDIS_HOST': self.name(pid, 'redis'), 'SPRING_DATA_REDIS_PORT': '6379',
            'SPRING_DATA_REDIS_PASSWORD': credentials['redis']})
        if row['preset'] in VITE_PRESETS:
            environment['CAMPUS_JWT_SECRET'] = credentials['jwt']
        return network, volumes.get('uploads'), environment

    def available(self, client, row):
        for kind in ('mysql', 'redis'):
            if not service_plan(row)[kind]['enabled']:
                continue
            try:
                container = self.owned(client.containers.get(self.name(row['project_id'], kind)), row['project_id'], kind)
                if not self.ready(container, kind):
                    return False
            except docker.errors.NotFound:
                return False
        return True

    def delete(self, client, pid):
        self.worker.check()
        for kind in ('mysql', 'redis'):
            try:
                self.owned(client.containers.get(self.name(pid, kind)), pid, kind).remove(force=True, v=True)
            except docker.errors.NotFound:
                pass
        for kind in ('mysql-data', 'redis-data', 'uploads-data'):
            try:
                self.owned(client.volumes.get(self.name(pid, kind)), pid, kind).remove()
            except docker.errors.NotFound:
                pass
        try:
            self.owned(client.networks.get(self.name(pid, 'data-net')), pid, 'data-net').remove()
        except docker.errors.NotFound:
            pass
        (self.cfg.data / 'services' / (project_id(pid) + '.json')).unlink(missing_ok=True)
