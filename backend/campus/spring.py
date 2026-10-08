"""Managed Java 21 / Gradle + frontend/Vite deployment recipe.

Only operator Dockerfiles are used. Generic JAR wrappers run in a restricted builder.
Compatibility adaptations are versioned, logged, and applied only to the build copy.
"""
import json
import re
import shutil
import tarfile
import fnmatch
import hashlib
import zipfile
from pathlib import PurePosixPath

from .safety import Rejected

JAVA_COMMAND = ['sh', '-c', '''while [ ! -f /work/.campus-input-ready ]; do sleep 0.1; done
java -version
gradle --no-daemon --max-workers=2 -Dorg.gradle.jvmargs=-Xmx1024m bootJar -x test
result=$?
printf "%s" "$result" > /tmp/campus-exit
exec sleep 720''']

WRAPPER_COMMANDS = {
    'gradle': './gradlew bootJar --no-daemon --max-workers=2 -Dorg.gradle.jvmargs=-Xmx1024m',
    'maven': './mvnw -DskipTests package',
}


def java_command(recipe):
    if not recipe.get('use_wrapper'):
        return JAVA_COMMAND
    # Only fixed allowlisted commands; manifest values are never shell fragments.
    return ['sh', '-c', 'while [ ! -f /work/.campus-input-ready ]; do sleep 0.1; done\n'
            'java -version\n' + WRAPPER_COMMANDS[recipe['build_tool']] +
            '\nresult=$?\nprintf "%s" "$result" > /tmp/campus-exit\nexec sleep 720']


def executable_jar(stream):
    try:
        with zipfile.ZipFile(stream) as jar:
            info = jar.getinfo('META-INF/MANIFEST.MF')
            if info.file_size > 65536:
                return False
            manifest = jar.read(info).decode('utf-8').replace('\r\n', '\n').replace('\n ', '')
            fields = dict(line.split(': ', 1) for line in manifest.splitlines() if ': ' in line)
            launcher = fields.get('Main-Class', '')
            return (launcher in ('org.springframework.boot.loader.JarLauncher', 'org.springframework.boot.loader.launch.JarLauncher',
                                 'org.springframework.boot.loader.PropertiesLauncher', 'org.springframework.boot.loader.launch.PropertiesLauncher')
                    and bool(fields.get('Start-Class')) and launcher.replace('.', '/') + '.class' in jar.namelist()
                    and any(n.startswith('BOOT-INF/classes/') and n.endswith('.class') for n in jar.namelist()))
    except (zipfile.BadZipFile, KeyError, UnicodeError, RuntimeError):
        return False


def select_jar_archive(source, output, cfg, recipe):
    """Select one executable Boot JAR; never extract untrusted build outputs to disk."""
    output_dir = recipe['output_dir']
    prefix = PurePosixPath(output_dir).name
    candidates, total, seen = [], 0, set()
    with tarfile.open(fileobj=source, mode='r:') as src:
        for member in src:
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != prefix or any(p in ('.', '..') for p in member.name.split('/'))
                    or '\\' in member.name or ':' in member.name or member.name in seen
                    or not (member.isdir() or member.isfile())):
                raise Rejected('JAR 산출물 archive 경로/링크/중복 오류')
            seen.add(member.name)
            total += member.size
            if total > cfg.runtime_archive_limit or len(seen) > cfg.runtime_file_limit:
                raise Rejected('JAR 산출물 archive 크기/파일 수 제한 초과')
            name = output_dir + '/' + '/'.join(parts[1:])
            if not member.isfile() or not fnmatch.fnmatchcase(name, recipe['artifact']):
                continue
            if recipe['build_tool'] == 'gradle' and name.endswith('-plain.jar'):
                continue
            with src.extractfile(member) as stream:
                if executable_jar(stream):
                    candidates.append((member, name))
        if len(candidates) != 1:
            raise Rejected(f'실행 가능한 Spring Boot JAR {len(candidates)}개: 하나를 확정해야 합니다. '
                           'campus-deploy.yaml의 artifact를 지정하거나 bootJar/repackage 설정을 확인하세요. '
                           + ', '.join(name for _, name in candidates)[:500])
        member, name = candidates[0]
        digest = hashlib.sha256()
        with src.extractfile(member) as data:
            while chunk := data.read(65536):
                digest.update(chunk)
        with tarfile.open(fileobj=output, mode='w') as dest:
            clean = tarfile.TarInfo('work/app.jar')
            clean.size, clean.mode = member.size, 0o644
            dest.addfile(clean, src.extractfile(member))
    output.seek(0)
    return {'artifact': name, 'artifact_sha256': digest.hexdigest()}


def inspect_spring(root):
    from .spring_plan import inspect
    return inspect(root)[1]


def prepare(worker, row, source):
    from .spring_plan import inspect
    settings = json.loads(row['settings'])
    facts, recipe = inspect(source)
    if settings.get('plan') and settings['plan'] != recipe:
        raise Rejected('검사된 배포 계획과 보존 소스가 일치하지 않습니다. 다시 소스 검사하세요.')
    work = worker.cfg.data / 'work' / row['id']
    shutil.copytree(source, work)
    changes = []
    if recipe['profile'] == 'SPRING_BOOT_VITE' and recipe['adapter'] == 'aurashop-v1':
        from .spring_adapters import aurashop_v1
        changes = aurashop_v1(work / recipe['backend_root'], work / recipe['frontend_root'])
    settings.update(plan=recipe, facts=facts, health_path=recipe['health_path'],
                    health_policy=recipe['health_policy'], adapter=recipe['adapter'], adaptations=changes)
    with worker.db.tx() as con:
        worker.guard(con)
        con.execute('UPDATE deployments SET settings=? WHERE id=?', (json.dumps(settings), row['id']))
    worker.db.log(row['id'], '[plan] ' + recipe['profile'] + '; backend=' + recipe['backend_root'] +
                  '; buildTool=' + recipe['build_tool'] + '; services=' +
                  ','.join(k for k, v in recipe['services'].items() if v['enabled']) + '\n')
    worker.db.log(row['id'], '[recipe] ' + recipe['adapter'] + '; 원본 SHA 보존\n' + '\n'.join(changes) + '\n')
    return work


def jar_archive(source, output, cfg):
    """Validate Docker's single-file archive before constructing an image context."""
    with tarfile.open(fileobj=source, mode='r:') as src:
        members = src.getmembers()
        if len(members) != 1 or members[0].name != 'app.jar' or not members[0].isfile():
            raise Rejected('bootJar 산출물은 app.jar 일반 파일 하나여야 합니다.')
        member = members[0]
        if not 0 < member.size <= cfg.runtime_archive_limit:
            raise Rejected('bootJar 크기 제한 초과/빈 파일')
        with tarfile.open(fileobj=output, mode='w') as dest:
            clean = tarfile.TarInfo('work/app.jar')
            clean.size, clean.mode = member.size, 0o644
            dest.addfile(clean, src.extractfile(member))
    output.seek(0)


def build_spring(worker, row, source, staging):
    from .builder import build
    work = prepare(worker, row, source)
    row = worker.db.one('SELECT * FROM deployments WHERE id=?', (row['id'],))
    recipe = json.loads(row['settings'])['plan']
    frontend = None
    if recipe['frontend_root']:
        frontend = build(worker, row, work / recipe['frontend_root'], staging, mode='vite',
                         environment={'VITE_API_BASE_URL': '/api'})
    row = worker.db.one('SELECT * FROM deployments WHERE id=?', (row['id'],))
    result = build(worker, row, work / recipe['backend_root'], staging, mode='java')
    result['frontend'] = frontend
    return result
