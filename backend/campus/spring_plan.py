"""Source facts and a declarative Java deployment plan; never execute source here."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

import yaml

from .safety import Rejected, blocked, safe_name

SPRING_PRESETS = ('SPRING_BOOT', 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE')
VITE_PRESETS = ('SPRING_BOOT', 'SPRING_BOOT_VITE')
RUNTIME_PRESETS = ('NODE_SERVER', *SPRING_PRESETS)
IGNORED = {'node_modules', 'build', 'target', 'dist', '__MACOSX'}


class ManifestLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key, value in node.value:
            key = self.construct_object(key, deep=deep)
            if not isinstance(key, str) or key in result:
                raise Rejected('manifest 키는 중복 없는 문자열이어야 합니다.')
            result[key] = self.construct_object(value, deep=deep)
        return result


def mapping(value, keys, label):
    if not isinstance(value, dict) or set(value) - set(keys):
        raise Rejected(f'{label}: 허용된 설정만 사용하세요 ({", ".join(keys)}). 자유 형식 명령은 지원하지 않습니다.')
    return value


def relative(value, label, glob=False):
    if value == '.' and not glob:
        return value
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.\-/]+' if not glob else r'[A-Za-z0-9_.\-/*?]+', value):
        raise Rejected(f'{label}: 안전한 상대 경로만 허용합니다.')
    safe_name(value)
    if '**' in value:
        raise Rejected(f'{label}: 재귀 glob은 지원하지 않습니다.')
    return value


def health_path(value):
    if not isinstance(value, str) or len(value) > 200 or not re.fullmatch(r'/[A-Za-z0-9_./~-]*', value):
        raise Rejected('health.path: 쿼리/호스트 없는 절대 HTTP 경로가 필요합니다.')
    if '//' in value or any(p in ('.', '..') for p in value.split('/')):
        raise Rejected('health.path: 경로 traversal은 허용하지 않습니다.')
    return value


def read_manifest(root):
    path = root / 'campus-deploy.yaml'
    if not path.exists():
        return None
    if path.stat().st_size > 16384:
        raise Rejected('campus-deploy.yaml은 16KiB 이하여야 합니다.')
    try:
        raw = path.read_text('utf-8')
        if any(isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)) for t in yaml.scan(raw)):
            raise Rejected('manifest YAML alias/anchor/tag는 허용하지 않습니다.')
        doc = yaml.load(raw, Loader=ManifestLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise Rejected('campus-deploy.yaml 형식이 올바르지 않습니다.') from exc
    mapping(doc, ('version', 'runtime', 'profile', 'root', 'frontend', 'java', 'buildTool', 'artifact', 'port', 'health', 'services'), 'manifest')
    if type(doc.get('version')) is not int or doc['version'] != 1 or doc.get('runtime') != 'spring-boot':
        raise Rejected('manifest version: 1, runtime: spring-boot가 필요합니다.')
    if type(doc.get('java', 21)) is not int or doc.get('java', 21) != 21 or type(doc.get('port', 8080)) is not int or doc.get('port', 8080) != 8080:
        raise Rejected('이 버전은 java: 21, port: 8080만 지원합니다.')
    if doc.get('profile') not in (None, 'SPRING_BOOT_JAR', 'SPRING_BOOT_VITE'):
        raise Rejected('profile은 SPRING_BOOT_JAR 또는 SPRING_BOOT_VITE여야 합니다.')
    if doc.get('buildTool') not in (None, 'gradle', 'maven'):
        raise Rejected('buildTool은 gradle 또는 maven이어야 합니다.')
    doc['root'] = relative(doc.get('root', '.'), 'root')
    if 'frontend' in doc:
        mapping(doc['frontend'], ('root',), 'frontend')
        relative(doc['frontend'].get('root'), 'frontend.root')
    if 'artifact' in doc:
        relative(doc['artifact'], 'artifact', glob=True)
    if 'health' in doc:
        mapping(doc['health'], ('path',), 'health')
        health_path(doc['health'].get('path'))
    if 'services' in doc:
        mapping(doc['services'], ('mysql', 'redis', 'storage'), 'services')
        for name, config in doc['services'].items():
            mapping(config, ('enabled', 'mountPath') if name == 'storage' else ('enabled',), 'services.' + name)
            if type(config.get('enabled')) is not bool:
                raise Rejected('services.' + name + '.enabled는 true 또는 false여야 합니다.')
            if name == 'storage' and 'mountPath' in config:
                mount = config['mountPath']
                # Only a project-owned named volume under this reserved prefix; never a host bind.
                if not isinstance(mount, str) or not mount.startswith('/app/'):
                    raise Rejected('storage.mountPath는 /app/ 아래 저장소 경로여야 합니다.')
                relative(mount[1:], 'storage.mountPath')
                if mount in ('/app/app.jar', '/app/campus-runtime') or mount.startswith('/app/app.jar/'):
                    raise Rejected('storage.mountPath는 실행 JAR을 가릴 수 없습니다.')
    return doc


def build_fact(path, root):
    text = path.read_text('utf-8')
    location = path.parent.relative_to(root).as_posix()
    tool = 'maven' if path.name == 'pom.xml' else 'gradle'
    multi, war = False, False
    if tool == 'maven':
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise Rejected('pom.xml DTD/entity는 허용하지 않습니다.')
        try:
            xml = ET.fromstring(text)
        except ET.ParseError as exc:
            raise Rejected('pom.xml XML 형식 오류') from exc
        tags = [(e.tag.rsplit('}', 1)[-1], (e.text or '').strip()) for e in xml.iter()]
        spring = any(k == 'artifactId' and (v.startswith('spring-boot-starter') or v in ('spring-boot-maven-plugin', 'spring-boot', 'spring-boot-autoconfigure', 'spring-boot-dependencies')) for k, v in tags)
        multi = any(k == 'module' or (k == 'packaging' and v == 'pom') for k, v in tags)
        war = any(k == 'packaging' and v == 'war' for k, v in tags)
        wrapper_files = ('mvnw', '.mvn/wrapper/maven-wrapper.properties')
    else:
        spring = bool(re.search(r'["\']org\.springframework\.boot["\']', text))
        for name in ('settings.gradle', 'settings.gradle.kts'):
            if (path.parent / name).is_file():
                settings = (path.parent / name).read_text('utf-8')
                multi |= bool(re.search(r'(?m)(?:^|[;{}])\s*(?:include|includeBuild)\b', settings))
        war = bool(re.search(r'(?:id\s*\(?\s*["\']war["\']|apply\s+plugin\s*:\s*["\']war["\']|plugins\s*\{\s*war\b)', text))
        wrapper_files = ('gradlew', 'gradle/wrapper/gradle-wrapper.properties', 'gradle/wrapper/gradle-wrapper.jar')
    missing = [p for p in wrapper_files if not (path.parent / p).is_file()]
    return {'root': location, 'file': path.name, 'spring_boot': spring, 'build_tool': tool,
            'wrapper': not missing, 'wrapper_missing': missing, 'multi_module': multi, 'war': war,
            'service_hints': [name for name, needle in (('mysql', 'mysql'), ('redis', 'redis'), ('actuator', 'actuator')) if needle in text.lower()]}


def analyze(root):
    """Record independent facts. Dependency hints never enable services."""
    builds, frontends = [], []
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if any(p in IGNORED or p.startswith('.') for p in rel.parts) or not path.is_file():
            continue
        if path.name in ('build.gradle', 'build.gradle.kts', 'pom.xml'):
            builds.append(build_fact(path, root))
        elif path.name == 'package.json':
            try:
                pkg = json.loads(path.read_text('utf-8'))
                deps = {**pkg.get('dependencies', {}), **pkg.get('devDependencies', {})}
            except (ValueError, TypeError, AttributeError):
                continue
            if 'vite' in deps:
                frontends.append({'root': path.parent.relative_to(root).as_posix(), 'vite': True,
                                  'npm_lockfile': (path.parent / 'package-lock.json').is_file()})
    return {'spring_boot': any(b['spring_boot'] for b in builds), 'backends': builds, 'frontends': frontends}


def plan(root, facts, manifest=None):
    selected_root = manifest['root'] if manifest else '.'
    backends = facts['backends']
    if not manifest and (len(backends) != 1 or backends[0]['root'] != '.'):
        raise Rejected('backend root가 여러 개이거나 중첩되어 있습니다. campus-deploy.yaml의 root/buildTool을 지정하세요.')
    matches = [b for b in backends if b['root'] == selected_root and (not manifest or not manifest.get('buildTool') or b['build_tool'] == manifest['buildTool'])]
    if len(matches) != 1:
        raise Rejected('backend/buildTool을 하나로 확정할 수 없습니다. campus-deploy.yaml을 확인하세요.')
    backend = matches[0]
    # Selecting a child module must not bypass the multi-module exclusion.
    if any(b['multi_module'] and (b['root'] == '.' or selected_root == b['root'] or selected_root.startswith(b['root'] + '/')) for b in backends):
        raise Rejected('다중 Gradle/Maven 모듈은 지원하지 않습니다.')
    if not backend['spring_boot'] or backend['war']:
        raise Rejected('실행 가능한 단일 모듈 Spring Boot JAR만 지원합니다. WAR/임의 Java 앱은 미지원입니다.')
    if not (root / selected_root / 'src/main').is_dir():
        raise Rejected('Spring Boot src/main 소스가 필요합니다.')
    frontends = facts['frontends']
    requested = manifest.get('profile') if manifest else None
    chosen_front = manifest.get('frontend', {}).get('root') if manifest else None
    if chosen_front is not None:
        frontends = [f for f in frontends if f['root'] == chosen_front]
        if len(frontends) != 1:
            raise Rejected('frontend.root에서 Vite 프로젝트를 확인할 수 없습니다.')
    if len(frontends) > 1 and requested != 'SPRING_BOOT_JAR':
        raise Rejected('frontend root가 여러 개입니다. campus-deploy.yaml의 frontend.root 또는 profile을 지정하세요.')
    preset = requested or ('SPRING_BOOT_VITE' if frontends else 'SPRING_BOOT_JAR')
    frontend = None
    if preset == 'SPRING_BOOT_VITE':
        if backend['build_tool'] != 'gradle' or len(frontends) != 1 or frontends[0]['root'] == selected_root:
            raise Rejected('SPRING_BOOT_VITE는 Gradle backend와 별도 Vite frontend가 필요합니다.')
        frontend = frontends[0]['root']
        from .safety import detect
        if detect(root / frontend) != 'VITE_STATIC':
            raise Rejected('SPRING_BOOT_VITE frontend에는 잠긴 Vite 프로젝트가 필요합니다.')
    elif not backend['wrapper']:
        raise Rejected('SPRING_BOOT_JAR에는 Gradle/Maven Wrapper가 필요합니다. 누락: ' + ', '.join(backend['wrapper_missing']))
    if chosen_front is not None and preset == 'SPRING_BOOT_JAR':
        raise Rejected('SPRING_BOOT_JAR에는 frontend 설정을 지정하지 마세요.')
    output = 'build/libs' if backend['build_tool'] == 'gradle' else 'target'
    artifact = manifest.get('artifact', output + '/*.jar') if manifest else output + '/*.jar'
    if not artifact.startswith(output + '/') or not artifact.endswith('.jar'):
        raise Rejected('artifact는 backend root 기준 ' + output + '/ 아래의 .jar 경로여야 합니다.')
    # Only the already supported Vite contract implies project services.
    services = {k: {'enabled': preset == 'SPRING_BOOT_VITE'} for k in ('mysql', 'redis', 'storage')}
    services['storage']['mountPath'] = '/app/uploads'
    for k, value in (manifest or {}).get('services', {}).items():
        services[k].update(value)
    adapter = 'none'
    if preset == 'SPRING_BOOT_VITE':
        adapter = 'aurashop-v1' if (root / selected_root / 'src/main/java/com/aura/shop/controller/ProductController.java').is_file() else 'spring-vite-v1'
    path = '/api/products' if adapter == 'aurashop-v1' else '/api/health' if preset == 'SPRING_BOOT_VITE' else '/'
    explicit_health = bool(manifest and 'health' in manifest)
    if explicit_health:
        path = manifest['health']['path']
    if preset == 'SPRING_BOOT_VITE' and path == '/':
        raise Rejected('SPRING_BOOT_VITE health.path는 frontend /와 분리된 backend 경로여야 합니다.')
    return {'profile': preset, 'backend_root': selected_root, 'frontend_root': frontend, 'java': 21,
            'build_tool': backend['build_tool'], 'use_wrapper': preset == 'SPRING_BOOT_JAR',
            'artifact': artifact, 'output_dir': output, 'port': 8080, 'adapter': adapter,
            'health_path': path, 'health_policy': 'strict-2xx' if explicit_health or preset == 'SPRING_BOOT_VITE' else 'http-reachable',
            'services': services, 'service_hints': backend['service_hints'], 'manifest': manifest is not None}


def inspect(root):
    manifest = read_manifest(root)
    facts = analyze(root)
    return facts, plan(root, facts, manifest)


def service_plan(row):
    settings = json.loads(row['settings'])
    if 'plan' in settings:
        return settings['plan']['services']
    # Existing READY images/settings are read without migrating their behavior.
    return {**{k: {'enabled': row['preset'] in VITE_PRESETS} for k in ('mysql', 'redis')},
            'storage': {'enabled': row['preset'] in VITE_PRESETS, 'mountPath': '/app/uploads'}}


def source_allowed(rel):
    parts = PurePosixPath(rel).parts
    if '.mvn' in parts:
        i = parts.index('.mvn')
        tail = '/'.join(parts[i:])
        return (not blocked('/'.join(parts[:i])) if i else True) and tail in (
            '.mvn/wrapper/maven-wrapper.properties', '.mvn/wrapper/maven-wrapper.jar',
            '.mvn/wrapper/MavenWrapperDownloader.java', '.mvn/maven.config', '.mvn/jvm.config')
    return not blocked(rel)
