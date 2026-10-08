"""Managed Java 21 / Gradle + frontend/Vite deployment recipe.

Repository Dockerfiles, compose files and wrapper executables are never executed.
Compatibility adaptations are versioned, logged, and applied only to the build copy.
"""
import json
import re
import shutil
import tarfile

from .safety import Rejected

JAVA_COMMAND = ['sh', '-c', '''while [ ! -f /work/.campus-input-ready ]; do sleep 0.1; done
java -version
gradle --no-daemon --max-workers=2 -Dorg.gradle.jvmargs=-Xmx1024m bootJar -x test
result=$?
if [ "$result" = 0 ]; then
  mkdir -p /work/campus-runtime
  count=0
  for jar in /work/build/libs/*.jar; do
    case "$jar" in *-plain.jar) continue;; esac
    [ -f "$jar" ] || continue
    count=$((count+1))
    cp "$jar" /work/campus-runtime/app.jar || result=1
  done
  [ "$count" = 1 ] || result=1
fi
printf "%s" "$result" > /tmp/campus-exit
exec sleep 720''']


def inspect_spring(root):
    gradle = next((root / p for p in ('build.gradle', 'build.gradle.kts') if (root / p).is_file()), None)
    if not gradle or 'org.springframework.boot' not in gradle.read_text('utf-8'):
        raise Rejected('Java는 Spring Boot Gradle 프로젝트만 지원합니다.')
    if not (root / 'src/main').is_dir():
        raise Rejected('Spring Boot src/main 소스가 필요합니다.')
    settings_file = next((root / p for p in ('settings.gradle', 'settings.gradle.kts') if (root / p).is_file()), None)
    if settings_file and re.search(r'(?m)^\s*(?:include|includeBuild)\b', settings_file.read_text('utf-8')):
        raise Rejected('다중 Gradle 모듈은 지원하지 않습니다. 루트 Spring Boot와 frontend/ Vite를 사용하세요.')
    from .safety import detect
    if not (root / 'frontend/package.json').is_file() or detect(root / 'frontend') != 'VITE_STATIC':
        raise Rejected('Spring Boot에는 frontend/ 안의 Vite 및 npm lockfile이 필요합니다.')
    aura = (root / 'src/main/java/com/aura/shop/controller/ProductController.java').is_file()
    return {'adapter': 'aurashop-v1' if aura else 'spring-vite-v1',
            'health_path': '/api/products' if aura else '/api/health'}


def prepare(worker, row, source):
    recipe = inspect_spring(source)
    work = worker.cfg.data / 'work' / row['id']
    shutil.copytree(source, work)
    changes = []
    if recipe['adapter'] == 'aurashop-v1':
        api = work / 'frontend/src/api.js'
        text = api.read_text('utf-8')
        marker = 'api.interceptors.request.use('
        if marker not in text or 'import.meta.env.VITE_API_BASE_URL' not in text:
            raise Rejected('aurashop API 소스가 호환 레시피와 다릅니다. 어댑터 갱신이 필요합니다.')
        # Some original screens use /api/products, others use /auth/login.
        # Both resolve once under the same-origin /api base.
        text = text.replace('http://localhost:8080/api/', '/api/')
        text += '''\n// Campus Deploy aurashop-v1: normalize mixed API prefixes.
api.interceptors.request.use(config => {
  if (config.url?.startsWith('/api/')) config.url = config.url.slice(4);
  return config;
});
'''
        api.write_text(text, 'utf-8')
        changes.append('frontend/src/api.js: same-origin API/refresh, 중복 /api 정규화')
        upload = work / 'src/main/java/com/aura/shop/controller/FileUploadController.java'
        text = upload.read_text('utf-8')
        if '"http://localhost:8080/uploads/"' not in text:
            raise Rejected('aurashop 업로드 URL 소스가 호환 레시피와 다릅니다.')
        upload.write_text(text.replace('"http://localhost:8080/uploads/"', '"/uploads/"'), 'utf-8')
        changes.append('FileUploadController.java: 업로드 URL을 상대 경로로 반환')
        jwt = work / 'src/main/java/com/aura/shop/util/JwtUtil.java'
        text = jwt.read_text('utf-8')
        old = 'Keys.secretKeyFor(SignatureAlgorithm.HS256)'
        if old not in text:
            raise Rejected('aurashop JWT 소스가 호환 레시피와 다릅니다.')
        jwt.write_text(text.replace(old, 'Keys.hmacShaKeyFor(java.util.Base64.getDecoder().decode(System.getenv("CAMPUS_JWT_SECRET")))'), 'utf-8')
        changes.append('JwtUtil.java: 프로젝트별 영속 서명키 (재시작/코드 롤백 후 세션 유지)')
    settings = json.loads(row['settings'])
    settings.update(recipe, adaptations=changes)
    with worker.db.tx() as con:
        worker.guard(con)
        con.execute('UPDATE deployments SET settings=? WHERE id=?', (json.dumps(settings), row['id']))
    worker.db.log(row['id'], '[recipe] ' + recipe['adapter'] + '; 원본 SHA 보존, 배포 복사본만 사용\n' + '\n'.join(changes) + '\n')
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
    frontend = build(worker, row, work / 'frontend', staging, mode='vite',
                     environment={'VITE_API_BASE_URL': '/api'})
    row = worker.db.one('SELECT * FROM deployments WHERE id=?', (row['id'],))
    result = build(worker, row, work, staging, mode='java')
    result['frontend'] = frontend
    return result
