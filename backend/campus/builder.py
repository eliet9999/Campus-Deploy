import contextlib
import io
import json
import tarfile
import tempfile
import threading
import time

import docker
from docker.types import LogConfig

from .safety import Rejected, extract_artifact

# This command is constant. No URL, project name, build string or user input is interpolated.
COMMAND = ['sh', '-c', 'while [ ! -f /work/.campus-input-ready ]; do sleep 0.1; done; node --version; npm --version; npm ci --no-audit --no-fund && npm run build; result=$?; printf "%s" "$result" > /tmp/campus-exit; exec sleep 720']
NODE_COMMAND = ['sh', '-c', COMMAND[2].replace('npm run build;', 'npm run build --if-present;')]


class BuildFailed(Rejected):
    def __init__(self, message, exit_code=None):
        super().__init__(message)
        self.exit_code = exit_code


def source_tar(source, target):
    with tarfile.open(fileobj=target, mode='w') as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                info = tarfile.TarInfo(path.relative_to(source).as_posix())
                info.size = path.stat().st_size
                info.uid = info.gid = 1000
                info.mode = 0o600
                with path.open('rb') as data:
                    archive.addfile(info, data)
        ready = tarfile.TarInfo('.campus-input-ready')
        ready.uid = ready.gid = 1000
        ready.mode = 0o600
        archive.addfile(ready, io.BytesIO(b''))
    target.seek(0)


def build(worker, deployment, source, staging):
    cfg, db, dep = worker.cfg, worker.db, deployment['id']
    client = docker.from_env(timeout=30)
    container = None
    work_volume = None
    try:
        image = client.images.get(cfg.image)  # Operators pre-pull the exact image in start/setup.
        snapshot = json.loads(deployment['settings'])
        snapshot.update({'image_id': image.id, 'image_digests': image.attrs.get('RepoDigests', []),
                         'timeout_seconds': cfg.timeout,
                         'user': '1000:1000', 'network': 'bridge', 'pids_limit': 256,
                         'work_tmpfs_bytes': 1024**3, 'read_only_root': True})
        with db.tx() as con:
            worker.guard(con)
            con.execute('UPDATE deployments SET settings=? WHERE id=?', (json.dumps(snapshot), dep))
        node_server = deployment['preset'] == 'NODE_SERVER'
        command = NODE_COMMAND if node_server else COMMAND
        db.log(dep, f'Docker image: {image.id}\nDigests: {snapshot["image_digests"]}\n$ node --version; npm --version; npm ci --no-audit --no-fund && npm run build' + (' --if-present' if node_server else '') + '\n')
        labels = {'campus.instance': cfg.instance, 'campus.kind': 'build', 'campus.project': deployment['project_id'], 'campus.deployment': dep}
        work_volume = client.volumes.create(name=f'campus-work-{dep}', driver='local',
            driver_opts={'type': 'tmpfs', 'device': 'tmpfs', 'o': 'size=1073741824,uid=1000,gid=1000,mode=0700,nosuid,nodev'},
            labels={**labels, 'campus.kind': 'build-work'})
        container = client.containers.create(
            image.id, command, name=f'campus-build-{dep}', user='1000:1000', working_dir='/work',
            environment={'HOME': '/tmp', 'NPM_CONFIG_CACHE': '/tmp/npm-cache', 'CI': 'true', 'GIT_TERMINAL_PROMPT': '0', 'GIT_LFS_SKIP_SMUDGE': '1'},
            network_mode='bridge', read_only=True, cap_drop=['ALL'], security_opt=['no-new-privileges:true'],
            nano_cpus=1_000_000_000, mem_limit=2 * 1024**3, memswap_limit=2 * 1024**3,
            pids_limit=256, init=True,
            volumes={work_volume.name: {'bind': '/work', 'mode': 'rw'}},
            tmpfs={'/tmp': 'rw,nosuid,nodev,size=268435456,uid=1000,gid=1000,mode=0700'},
            labels=labels,
            log_config=LogConfig(type=LogConfig.types.JSON, config={'max-size': '2m', 'max-file': '1'}),
        )
        started = time.monotonic()
        container.start()
        with tempfile.TemporaryFile() as archive:
            source_tar(source, archive)
            if not container.put_archive('/work', archive):
                raise BuildFailed('Docker source archive 전송 실패')

        log_errors = []
        def stream_logs():
            try:
                for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=True):
                    db.log(dep, chunk.decode('utf-8', errors='replace'))
            except Exception as exc:
                log_errors.append(exc)

        reader = threading.Thread(target=stream_logs, daemon=True)
        reader.start()
        code = None
        while True:
            worker.check(dep)
            container.reload()
            if not container.attrs['State']['Running']:
                code = container.attrs['State']['ExitCode']
                raise BuildFailed('빌드 컨테이너가 예상보다 먼저 종료되었습니다 (OOM/외부 종료 확인).', code)
            if time.monotonic() - started > cfg.timeout:
                raise BuildFailed(f'빌드 timeout: {cfg.timeout}초 제한 초과', 124)
            result = container.exec_run(['cat', '/tmp/campus-exit'], user='1000:1000')
            if result.exit_code == 0:
                code = int(result.output.strip())
                break
            time.sleep(.5)
        with db.tx() as con:
            worker.guard(con)
            con.execute('UPDATE deployments SET exit_code=? WHERE id=?', (code, dep))
        if code != 0:
            reason = ' (메모리 한도 초과)' if container.attrs['State'].get('OOMKilled') else ''
            raise BuildFailed(f'npm 빌드 실패: exit {code}{reason}. 실제 로그를 확인하세요.', code)
        if log_errors:
            raise BuildFailed('빌드 로그 수집 실패: ' + str(log_errors[0]), code)
        # The runner stays alive while dist is copied; stopping would discard tmpfs.
        stream, _ = container.get_archive('/work' if node_server else '/work/dist')
        with tempfile.TemporaryFile() as archive:
            total = 0
            for chunk in stream:
                worker.check(dep)
                total += len(chunk)
                limit = cfg.runtime_archive_limit + cfg.runtime_file_limit * 2048 if node_server else cfg.artifact_limit + cfg.file_limit * 2048
                if total > limit:
                    raise BuildFailed('Docker archive 전송 크기 한도 초과')
                archive.write(chunk)
            archive.seek(0)
            if node_server:
                from .runtime_image import create_image
                return create_image(worker, deployment, image.id, archive, client)
            return extract_artifact(archive, staging, cfg)
    finally:
        if container:
            container.remove(force=True, v=True)
            if 'reader' in locals():
                reader.join(timeout=5)
        if work_volume:
            work_volume.remove()
        if container or work_volume:
            db.log(dep, '[cleanup] 소유 빌드 컨테이너·tmpfs 볼륨 제거 완료\n')
        client.close()
