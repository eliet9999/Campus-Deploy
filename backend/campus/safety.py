import hashlib
import json
import re
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


class Rejected(ValueError):
    pass


def github_url(value):
    p = urlsplit(value)
    if p.scheme != 'https' or p.netloc != 'github.com' or p.query or p.fragment:
        raise Rejected('공개 github.com HTTPS 저장소 주소만 지원합니다. 인증정보·포트·쿼리는 허용하지 않습니다.')
    match = re.fullmatch(r'/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_][A-Za-z0-9_.-]{0,99})/?', p.path)
    if not match:
        raise Rejected('저장소 루트 주소를 입력하세요: https://github.com/owner/repo')
    owner, repo = match.groups()
    repo = repo.removesuffix('.git')
    if repo in ('.', '..') or not repo:
        raise Rejected('잘못된 저장소 이름입니다.')
    return f'https://github.com/{owner}/{repo}'


def safe_name(name):
    if not name or '\\' in name or ':' in name or name.startswith('/') or any(ord(c) < 32 for c in name):
        raise Rejected(f'위험한 archive 경로: {name[:100]}')
    parts = name.rstrip('/').split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts):
        raise Rejected(f'위험한 archive 경로: {name[:100]}')
    if len(name) > 240 or len(parts) > 20:
        raise Rejected('경로 길이/깊이 한도를 초과했습니다.')
    return PurePosixPath(*parts)


def blocked(path):
    for part in PurePosixPath(path).parts:
        p = part.lower()
        if p.startswith('.') or p in ('node_modules', '__macosx', '__pycache__', 'campus.sqlite3', 'secrets', 'id_rsa', 'id_ed25519') or p.endswith(('.pem', '.key', '.p12', '.pfx')):
            return True
    return False


def copy_limited(src, dst, budget):
    total = 0
    with dst.open('xb') as out:
        while chunk := src.read(64 * 1024):
            total += len(chunk)
            if total > budget:
                raise Rejected('압축 해제/산출물 크기 제한을 초과했습니다.')
            out.write(chunk)
    return total


def extract_zip(archive, destination, settings):
    destination.mkdir(parents=True, exist_ok=False)
    seen = set()
    total = 0
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) > settings.file_limit:
            raise Rejected('ZIP 파일 수 제한을 초과했습니다.')
        for info in z.infolist():
            path = safe_name(info.filename)
            key = str(path).casefold()
            if key in seen:
                raise Rejected('ZIP에 중복 경로가 있습니다.')
            seen.add(key)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise Rejected('ZIP 링크/특수 파일은 허용하지 않습니다.')
            if info.flag_bits & 1:
                raise Rejected('암호화 ZIP은 지원하지 않습니다.')
            dest = destination.joinpath(*path.parts)
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            if info.file_size > settings.extract_limit - total:
                raise Rejected('ZIP 압축 해제 크기 제한을 초과했습니다.')
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src:
                total += copy_limited(src, dest, settings.extract_limit - total)
    roots = [p for p in destination.iterdir() if p.name != '__MACOSX']
    return roots[0] if len(roots) == 1 and roots[0].is_dir() else destination


def detect(root):
    if (root / '.gitmodules').exists():
        raise Rejected('Git submodule은 지원하지 않습니다.')
    server_files = ('requirements.txt', 'pyproject.toml', 'pom.xml', 'build.gradle', 'build.gradle.kts', 'manage.py')
    if any((root / p).exists() for p in server_files):
        raise Rejected('Python/Spring Boot/Java 서버는 지원하지 않습니다. P1은 Node HTTP 앱만 지원합니다.')
    pkg_file = root / 'package.json'
    if pkg_file.exists():
        try:
            pkg = json.loads(pkg_file.read_text('utf-8'))
            deps = {**pkg.get('dependencies', {}), **pkg.get('devDependencies', {}), **pkg.get('optionalDependencies', {})}
        except (ValueError, TypeError, AttributeError) as exc:
            raise Rejected('package.json 형식이 올바르지 않습니다.') from exc
        if any(p in deps for p in ('next', 'nuxt', '@nestjs/core', '@sveltejs/kit', '@remix-run/node')):
            raise Rejected('이 서버 프레임워크/SSR은 지원하지 않습니다. P1 검증 대상은 Express HTTP 앱입니다.')
        if 'workspaces' in pkg or any((root / p).exists() for p in ('pnpm-lock.yaml', 'yarn.lock')):
            raise Rejected('monorepo/pnpm/yarn은 지원하지 않습니다. npm lockfile이 필요합니다.')
        script = pkg.get('scripts', {}).get('build', '')
        if not (root / 'package-lock.json').is_file():
            raise Rejected('package-lock.json v2/v3이 필요합니다. npm ci로 재현 가능한 소스만 지원합니다.')
        if '--ssr' in script or any((root / p).exists() for p in ('entry-server.js', 'src/entry-server.tsx', 'src/entry-server.ts')):
            raise Rejected('Vite SSR은 지원하지 않습니다.')
        lock = json.loads((root / 'package-lock.json').read_text('utf-8'))
        if lock.get('lockfileVersion') not in (2, 3):
            raise Rejected('npm lockfileVersion 2 또는 3이 필요합니다.')
        # Restrict install sources; npm scripts still execute only in the isolated builder.
        for entry in lock.get('packages', {}).values():
            resolved = entry.get('resolved', '')
            if entry.get('link') or (resolved and not resolved.startswith('https://registry.npmjs.org/')):
                raise Rejected('npm 공식 registry의 잠긴 패키지만 지원합니다. Git/file 의존성은 지원하지 않습니다.')
        if 'vite' in deps and script:
            return 'VITE_STATIC'
        start = pkg.get('scripts', {}).get('start')
        if not isinstance(start, str) or not start.strip():
            raise Rejected('NODE_SERVER에는 package.json scripts.start (npm start)가 필요합니다.')
        unsupported = sorted(set(deps) & {'pg', 'mysql', 'mysql2', 'mongoose', 'mongodb', 'redis', 'ioredis',
            'sqlite3', 'better-sqlite3', 'prisma', '@prisma/client', 'sequelize', 'typeorm', 'knex',
            'ws', 'socket.io', 'socket.io-client', 'bull', 'bullmq', 'node-cron', 'cron', 'agenda', 'pm2'})
        if unsupported:
            raise Rejected('DB/Redis/WebSocket/worker/cron은 지원하지 않습니다: ' + ', '.join(unsupported))
        if (root / 'Dockerfile').exists() or (root / 'dockerfile').exists():
            raise Rejected('사용자 Dockerfile 프로젝트는 지원하지 않습니다. npm start 기반 소스를 제출하세요.')
        return 'NODE_SERVER'
    if (root / 'index.html').is_file():
        return 'STATIC'
    raise Rejected('루트 index.html, npm Vite 또는 lockfile과 npm start가 있는 Node HTTP 프로젝트가 필요합니다. 중첩/monorepo는 지원하지 않습니다.')


def collect_static(root, output, settings):
    output.mkdir(parents=True, exist_ok=False)
    total = 0
    count = 0
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        safe_name(rel)
        if path.is_symlink():
            raise Rejected('산출물 링크는 허용하지 않습니다.')
        if blocked(rel) or path.is_dir():
            continue
        count += 1
        if count > settings.file_limit:
            raise Rejected('산출물 파일 수 제한을 초과했습니다.')
        dest = output / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with path.open('rb') as src:
            total += copy_limited(src, dest, settings.artifact_limit - total)
    return validate_artifact(output, settings)


def extract_artifact(archive, output, settings):
    output.mkdir(parents=True, exist_ok=False)
    total = 0
    seen = set()
    with tarfile.open(fileobj=archive, mode='r|*') as tf:
        for member in tf:
            path = safe_name(member.name)
            key = str(path).casefold()
            if key in seen:
                raise Rejected('산출물 archive 중복 경로')
            seen.add(key)
            if len(seen) > settings.file_limit:
                raise Rejected('산출물 파일 수 제한 초과')
            if not (member.isdir() or member.isfile()) or member.issym() or member.islnk():
                raise Rejected('산출물 링크/특수 파일 금지')
            if path.parts[0] != 'dist':
                raise Rejected('dist 외의 산출물 경로')
            rel = PurePosixPath(*path.parts[1:])
            if len(path.parts) == 1 or blocked(str(rel)):
                continue
            dest = output.joinpath(*rel.parts)
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                if member.size > settings.artifact_limit - total:
                    raise Rejected('산출물 크기 제한 초과')
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as src:
                    total += copy_limited(src, dest, settings.artifact_limit - total)
    return validate_artifact(output, settings)


def validate_artifact(root, settings):
    index = root / 'index.html'
    if not index.is_file() or index.stat().st_size == 0:
        raise Rejected('산출물 index.html이 없거나 비어 있습니다.')
    if index.stat().st_size > 5 * 1024**2:
        raise Rejected('index.html은 5MiB 이하만 지원합니다.')
    files = [p for p in root.rglob('*') if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    if total > settings.artifact_limit or len(files) > settings.file_limit:
        raise Rejected('산출물 제한 초과')
    digest = hashlib.sha256()
    for p in sorted(files):
        digest.update(p.relative_to(root).as_posix().encode())
        with p.open('rb') as f:
            while chunk := f.read(65536):
                digest.update(chunk)
    return {'files': len(files), 'bytes': total, 'sha256': digest.hexdigest()}


def remove_owned(root: Path, path: Path):
    root, path = root.resolve(), path.resolve()
    if path == root or not path.is_relative_to(root):
        raise Rejected('정리 대상이 소유 디렉터리를 벗어났습니다.')
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
