"""Package an untrusted builder result without extracting it onto the host.

The only Dockerfile interpreted by Docker is generated here and contains no RUN.
User install/build scripts have already run in the restricted builder.
"""
import io
import json
import posixpath
import re
import tarfile
import tempfile

from .safety import Rejected


def image_context(archive, output, base, cfg, spring=False, image_labels=None):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', base):
        raise Rejected('고정 Node base image ID가 필요합니다.')
    seen, links, entries, total = set(), set(), [], 0
    targets = []
    # Two passes reject link ancestors regardless of archive member order.
    with tarfile.open(fileobj=archive, mode='r:') as source:
        for item in source:
            name = item.name.rstrip('/')
            parts = name.split('/')
            if parts[0] != 'work' or any(p in ('', '.', '..') for p in parts) or '\\' in name or ':' in name or any(ord(c) < 32 for c in name):
                raise Rejected('runtime archive 경로가 /work를 벗어났습니다.')
            if name in seen or len(seen) >= cfg.runtime_file_limit or len(name) > 1024:
                raise Rejected('runtime archive 중복/파일 수/경로 길이 제한')
            seen.add(name)
            if not (item.isdir() or item.isfile() or item.issym()):
                raise Rejected('runtime archive hardlink/특수 파일은 지원하지 않습니다.')
            if item.issym():
                target = item.linkname
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
                if target.startswith('/') or '\\' in target or ':' in target or not resolved.startswith('work/'):
                    raise Rejected('runtime archive 외부 symlink 금지')
                # npm .bin uses leading ../ segments. Reject mid-path traversal,
                # where a prior symlink could change what '..' resolves against.
                rest = target.split('/')
                while rest and rest[0] == '..':
                    rest.pop(0)
                if any(p in ('', '.', '..') for p in rest):
                    raise Rejected('runtime archive 모호한 symlink 경로')
                targets.append(resolved)
                links.add(name)
            total += item.size
            if total > cfg.runtime_archive_limit:
                raise Rejected('runtime image 입력 크기 제한 초과')
            entries.append(item)
        for target in targets:
            if target not in seen or target in links:
                raise Rejected('runtime archive dangling/chained symlink 금지')
        for name in seen | set(targets):
            parent = posixpath.dirname(name)
            while parent:
                if parent in links:
                    raise Rejected('runtime archive symlink 하위 경로 금지')
                parent = posixpath.dirname(parent)
        if spring and ('work/app.jar' not in seen or any(p != 'work/app.jar' and p != 'work' for p in seen)):
            raise Rejected('Spring runtime archive에는 app.jar 하나만 있어야 합니다.')
        if spring and (links or not source.getmember('work/app.jar').isfile()):
            raise Rejected('Spring app.jar는 일반 파일이어야 합니다.')
        if not spring and 'work/package.json' not in seen:
            raise Rejected('runtime package.json이 없습니다.')
        with tarfile.open(fileobj=output, mode='w') as dest:
            dockerfile = (f'FROM {base}\nWORKDIR /app\nCOPY --chown=1000:1000 app/ /app/\n'
                          'USER 1000:1000\nENV PORT=8080 NODE_ENV=production HOME=/tmp NPM_CONFIG_CACHE=/tmp/npm-cache\n'
                          'EXPOSE 8080\nCMD ["npm","start"]\n').encode()
            if spring:
                dockerfile = (f'FROM {base}\nWORKDIR /app\nCOPY --chown=1000:1000 app/ /app/\n'
                              'USER 1000:1000\nENV HOME=/tmp SERVER_PORT=8080\n'
                              'EXPOSE 8080\nCMD ["java","-jar","/app/app.jar"]\n').encode()
            if image_labels:
                # One LABEL instruction prevents Docker from retaining partial ownership configs.
                dockerfile += ('LABEL ' + ' '.join(k + '=' + json.dumps(v) for k, v in image_labels.items()) + '\n').encode()
            info = tarfile.TarInfo('Dockerfile')
            info.size = len(dockerfile)
            dest.addfile(info, io.BytesIO(dockerfile))
            for item in entries:
                if item.name.rstrip('/') in ('work', 'work/.campus-input-ready'):
                    continue
                clean = tarfile.TarInfo('app/' + item.name.removeprefix('work/').rstrip('/'))
                clean.type, clean.size, clean.linkname = item.type, item.size, item.linkname
                clean.uid = clean.gid = 1000
                clean.mode = 0o755 if item.isdir() or item.mode & 0o111 else 0o644
                dest.addfile(clean, source.extractfile(item) if item.isfile() else None)
    output.seek(0)
    return {'files': len(seen), 'bytes': total}


def create_image(worker, row, base, archive, client):
    from .runtime import labels, runtime_name
    runtime_name(row['id'])
    if not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', row['sha']):
        raise Rejected('잘못된 소스 SHA')
    tag = f'campus-runtime:{row["id"]}-{row["sha"][:12]}'
    owned = labels(worker.cfg, row, 'runtime-image')
    with tempfile.TemporaryFile() as context:
        from .spring_plan import SPRING_PRESETS
        validation = image_context(archive, context, base, worker.cfg, row['preset'] in SPRING_PRESETS, image_labels=owned)
        worker.check(row['id'])
        worker.db.log(row['id'], '[image] 고정 Dockerfile로 이미지 생성 (사용자 Dockerfile/RUN 없음)\n')
        for event in client.api.build(fileobj=context, custom_context=True, tag=tag, decode=True,
                                     rm=True, forcerm=True, pull=False, network_mode='none'):
            worker.check(row['id'])
            if event.get('error'):
                raise Rejected('runtime image 생성 실패: ' + event['error'])
            if event.get('stream'):
                worker.db.log(row['id'], event['stream'])
    image = client.images.get(tag)
    if any(image.labels.get(k) != v for k, v in owned.items()):
        raise Rejected('runtime image 소유 정보 불일치')
    with worker.db.tx() as con:
        worker.guard(con)
        con.execute('UPDATE deployments SET runtime_image=? WHERE id=?', (image.id, row['id']))
    worker.db.log(row['id'], f'[image] {tag} → {image.id}\n')
    return {**validation, 'image_id': image.id, 'image_tag': tag, 'source_sha': row['sha']}
