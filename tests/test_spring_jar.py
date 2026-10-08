import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from campus.builder import source_tar
from campus.config import Settings
from campus.db import DB, new_deployment, uid
from campus.gateway import create_app
from campus.safety import Rejected, detect
from campus.services import Services
from campus.spring import executable_jar, select_jar_archive
from campus.spring_plan import inspect, read_manifest, source_allowed

ROOT = Path(__file__).resolve().parents[1]


def sample(tmp_path, name='spring-gradle-api'):
    shutil.copytree(ROOT / 'samples' / name, tmp_path, dirs_exist_ok=True)
    return tmp_path


def manifest(root, extra=''):
    (root / 'campus-deploy.yaml').write_text('version: 1\nruntime: spring-boot\n' + extra, 'utf-8')


@pytest.mark.parametrize('name,tool', [('spring-gradle-api','gradle'), ('spring-maven-api','maven'), ('spring-gradle-web','gradle')])
def test_frontend_free_wrapper_source_is_jar(tmp_path, name, tool):
    sample(tmp_path, name)
    facts, plan = inspect(tmp_path)
    assert facts['spring_boot'] and not facts['frontends']
    assert detect(tmp_path) == 'SPRING_BOOT_JAR'
    assert plan['build_tool'] == tool and plan['use_wrapper']
    assert plan['adapter'] == 'none' and not any(v['enabled'] for v in plan['services'].values())
    assert plan['health_path'] == '/' and plan['health_policy'] == 'http-reachable'


def test_kotlin_build_and_manifest_explicit_health(tmp_path):
    sample(tmp_path)
    (tmp_path / 'build.gradle').unlink()
    (tmp_path / 'build.gradle.kts').write_text('plugins { java; id("org.springframework.boot") version "4.0.5" }')
    manifest(tmp_path, 'buildTool: gradle\nhealth:\n  path: /ready\n')
    assert inspect(tmp_path)[1]['health_policy'] == 'strict-2xx'
    assert inspect(tmp_path)[1]['health_path'] == '/ready'


def test_dependency_hints_do_not_provision_services(tmp_path):
    sample(tmp_path)
    with (tmp_path / 'build.gradle').open('a') as f:
        f.write("dependencies { runtimeOnly 'com.mysql:mysql-connector-j'; implementation 'org.springframework.boot:spring-boot-starter-data-redis'; implementation 'org.springframework.boot:spring-boot-starter-actuator' }")
    facts, plan = inspect(tmp_path)
    assert set(plan['service_hints']) == {'mysql', 'redis', 'actuator'}
    assert not any(v['enabled'] for v in plan['services'].values())
    manifest(tmp_path, 'services:\n  redis:\n    enabled: true\n  storage:\n    enabled: true\n    mountPath: /app/files\n')
    plan = inspect(tmp_path)[1]
    assert plan['services']['redis']['enabled'] and not plan['services']['mysql']['enabled']
    assert plan['services']['storage']['mountPath'] == '/app/files'


@pytest.mark.parametrize('extra', [
    'root: ../outside\n', 'root: /tmp\n', 'root: C:\\outside\n', 'java: 17\n', 'port: 9090\n',
    'build: rm -rf /\n', 'buildTool: "gradle; curl bad"\n', 'artifact: "build/libs/$(touch bad).jar"\n',
    'artifact: ../app.jar\n', 'artifact: target/*.war\n', 'artifact: build/libs/**/*.jar\n',
    'health: {path: //evil.example}\n', 'health: {path: /../api}\n', 'health: {path: "/api?x=1"}\n',
    'services: {mysql: {enabled: "true"}}\n', 'services: {postgres: {enabled: true}}\n',
    'services: {storage: {enabled: true, mountPath: /var/run/docker.sock}}\n',
    'services: {storage: {enabled: true, mountPath: /app/app.jar}}\n',
    'services: {storage: {enabled: true, mountPath: /app/../tmp}}\n',
    'root: .\nroot: elsewhere\n', 'root: &x .\nfrontend: {root: *x}\n',
    'root: !!str .\n', 'profile: arbitrary\n', 'version: 2\n',
])
def test_manifest_rejects_unsafe_or_unknown_fields(tmp_path, extra):
    sample(tmp_path)
    manifest(tmp_path, extra)
    with pytest.raises(Rejected):
        inspect(tmp_path)


def test_nested_backend_requires_manifest_without_guessing(tmp_path):
    sample(tmp_path / 'backend')
    with pytest.raises(Rejected, match='campus-deploy.yaml'):
        detect(tmp_path)
    manifest(tmp_path, 'root: backend\nbuildTool: gradle\n')
    assert inspect(tmp_path)[1]['backend_root'] == 'backend'
    sample(tmp_path / 'another', 'spring-maven-api')
    assert inspect(tmp_path)[1]['build_tool'] == 'gradle'


def test_multiple_vite_roots_require_selection(tmp_path):
    sample(tmp_path)
    for name in ('one', 'two'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'package.json').write_text('{"scripts":{"build":"vite build"},"devDependencies":{"vite":"8.0.1"}}')
        (tmp_path / name / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{}}')
    with pytest.raises(Rejected, match='frontend root'):
        detect(tmp_path)
    manifest(tmp_path, 'frontend: {root: two}\n')
    assert inspect(tmp_path)[1]['frontend_root'] == 'two'
    manifest(tmp_path, 'profile: SPRING_BOOT_JAR\n')
    assert inspect(tmp_path)[1]['frontend_root'] is None


@pytest.mark.parametrize('name,match', [('spring-no-wrapper', 'Wrapper'), ('spring-multimodule','다중')])
def test_unsupported_samples_fail_precisely(tmp_path, name, match):
    sample(tmp_path, name)
    with pytest.raises(Rejected, match=match):
        detect(tmp_path)


def test_manifest_cannot_bypass_maven_multimodule(tmp_path):
    sample(tmp_path / 'api', 'spring-maven-api')
    (tmp_path / 'pom.xml').write_text('<project><packaging>pom</packaging><modules><module>api</module></modules></project>')
    manifest(tmp_path, 'root: api\n')
    with pytest.raises(Rejected, match='다중'):
        inspect(tmp_path)


@pytest.mark.parametrize('tool', ['gradle', 'maven'])
def test_war_rejected(tmp_path, tool):
    sample(tmp_path, 'spring-' + tool + '-api')
    if tool == 'gradle':
        with (tmp_path / 'build.gradle').open('a') as f:
            f.write("\napply plugin: 'war'\n")
    else:
        p = tmp_path / 'pom.xml'
        p.write_text(p.read_text().replace('<modelVersion>', '<packaging>war</packaging><modelVersion>'))
    with pytest.raises(Rejected, match='WAR'):
        inspect(tmp_path)


def boot_jar(executable=True):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as z:
        if executable:
            z.writestr('META-INF/MANIFEST.MF', 'Main-Class: org.springframework.boot.loader.launch.JarLauncher\r\nStart-Class: example.Application\r\n')
            z.writestr('org/springframework/boot/loader/launch/JarLauncher.class', b'class')
            z.writestr('BOOT-INF/classes/example/Application.class', b'class')
        else:
            z.writestr('META-INF/MANIFEST.MF', 'Main-Class: example.Main\n')
    return result.getvalue()


def archive_jars(names):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as archive:
        for name, data in names:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    result.seek(0)
    return result


def test_plain_jar_ignored_and_executable_jar_selected(tmp_path):
    sample(tmp_path)
    plan = inspect(tmp_path)[1]
    result = select_jar_archive(archive_jars([('libs/app-plain.jar',boot_jar()), ('libs/app.jar',boot_jar())]), io.BytesIO(), Settings(), plan)
    assert result['artifact'] == 'build/libs/app.jar' and len(result['artifact_sha256']) == 64


def test_ambiguous_jars_require_exact_artifact(tmp_path):
    sample(tmp_path)
    plan = inspect(tmp_path)[1]
    entries = [('libs/first.jar',boot_jar()), ('libs/second.jar',boot_jar())]
    with pytest.raises(Rejected, match='2개'):
        select_jar_archive(archive_jars(entries), io.BytesIO(), Settings(), plan)
    plan['artifact'] = 'build/libs/second.jar'
    assert select_jar_archive(archive_jars(entries), io.BytesIO(), Settings(), plan)['artifact'].endswith('second.jar')


def test_non_boot_jar_is_not_an_executable_spring_application():
    assert not executable_jar(io.BytesIO(boot_jar(False)))


def test_source_keeps_maven_wrapper_but_never_credentials(tmp_path):
    sample(tmp_path, 'spring-maven-api')
    assert source_allowed('.mvn/wrapper/maven-wrapper.properties')
    assert source_allowed('backend/.mvn/wrapper/maven-wrapper.properties')
    assert not source_allowed('.mvn/settings.xml') and not source_allowed('.env')
    assert not source_allowed('.git/.mvn/wrapper/maven-wrapper.jar')
    archive = io.BytesIO()
    source_tar(tmp_path, archive)
    with tarfile.open(fileobj=archive) as tf:
        assert tf.getmember('mvnw').mode & 0o100
        assert tf.getmember('.mvn/wrapper/maven-wrapper.properties').isfile()


def test_no_services_never_touches_docker_or_credentials(tmp_path):
    sample(tmp_path)
    plan = inspect(tmp_path)[1]
    services = Services(SimpleNamespace(cfg=Settings(data=tmp_path)))
    row = {'project_id': 'a'*32, 'preset': 'SPRING_BOOT_JAR', 'settings': json.dumps({'plan': plan})}
    network, volume, env = services.ensure(object(), row)
    assert network is None and volume is None and env['SERVER_PORT'] == '8080'
    assert not any('DATASOURCE' in k or 'REDIS' in k for k in env)
    assert not (tmp_path / 'services').exists()


def test_jar_gateway_forwards_root_api_and_static_paths(tmp_path, monkeypatch):
    cfg = Settings(data=tmp_path)
    db = DB(cfg); db.initialize()
    pid, dep = uid(), uid()
    with db.tx() as con:
        con.execute("INSERT INTO sources(id,kind,locator,status,created,request_key) VALUES('s','ZIP','x','READY',0,'k')")
        con.execute("INSERT INTO projects(id,slug,name,source_id,preset,production_id,created) VALUES(?,'jar','jar','s','SPRING_BOOT_JAR',?,0)", (pid,dep))
        con.execute("INSERT INTO deployments(id,project_id,source_id,sha,preset,settings,status,created,stages,runtime_state) VALUES(?,?,'s','sha','SPRING_BOOT_JAR','{}','READY',0,'{}','RUNNING')", (dep,pid))
    from fastapi.responses import PlainTextResponse
    async def proxy(row, request):
        return PlainTextResponse(request.method + ' ' + request.url.path)
    monkeypatch.setattr('campus.gateway.proxy_runtime', proxy)
    client = TestClient(create_app(cfg), base_url='http://p-jar.localhost:8080')
    for path in ('/', '/api/hello', '/app.js', '/actuator/health'):
        assert client.get(path).text == 'GET ' + path
    assert client.post('/api/echo', json={}).text == 'POST /api/echo'


@pytest.mark.parametrize('status,policy,success,detail', [
    (200,'strict-2xx',True,''), (404,'http-reachable',True,''),
    (404,'strict-2xx',False,'HTTP 404'), (401,'http-reachable',False,'HTTP 401'),
    (500,'http-reachable',False,'HTTP 500'), (502,'http-reachable',False,'CONNECTION_FAILURE'),
    (504,'http-reachable',False,'UPSTREAM_TIMEOUT'),
])
def test_health_policy_does_not_confuse_root_404_and_application_health(monkeypatch, status, policy, success, detail):
    from contextlib import nullcontext
    from campus.runtime import Runtime
    cfg = Settings(spring_health_timeout=1)
    records = []
    runtime = Runtime(SimpleNamespace(cfg=cfg, db=SimpleNamespace(log=lambda *args: records.append(args)), check=lambda *args: None))
    container = SimpleNamespace(attrs={'State': {'Running': True}}, reload=lambda: None)
    monkeypatch.setattr(runtime, 'state', lambda *args: None)
    monkeypatch.setattr(runtime, 'capture', lambda *args: None)
    monkeypatch.setattr(runtime, 'get', lambda *args: container)
    monkeypatch.setattr('campus.runtime.docker.from_env', lambda **kw: SimpleNamespace(close=lambda: None))
    http = SimpleNamespace(stream=lambda *args, **kw: nullcontext(SimpleNamespace(status_code=status)))
    monkeypatch.setattr('campus.runtime.httpx.Client', lambda **kw: nullcontext(http))
    times = iter([0, 0, 2])
    monkeypatch.setattr('campus.runtime.time.monotonic', lambda: next(times))
    monkeypatch.setattr('campus.runtime.time.sleep', lambda _: None)
    row = {'id':'a'*32, 'preset':'SPRING_BOOT_JAR', 'settings':json.dumps({'health_path':'/', 'health_policy':policy})}
    if success:
        result = runtime.health(row)
        assert result['status'] == status and result['policy'] == policy
        if policy == 'http-reachable':
            assert '연결만' in result['warning']
    else:
        with pytest.raises(Rejected, match=detail):
            runtime.health(row)


@pytest.mark.parametrize('mysql,redis,storage', [(a,b,c) for a in (False,True) for b in (False,True) for c in (False,True)])
def test_only_explicit_services_are_created(tmp_path, monkeypatch, mysql, redis, storage):
    sample(tmp_path)
    plan = inspect(tmp_path)[1]
    for name, enabled in [('mysql',mysql), ('redis',redis), ('storage',storage)]:
        plan['services'][name]['enabled'] = enabled
    worker = SimpleNamespace(cfg=Settings(data=tmp_path), check=lambda *a:None,
                             db=SimpleNamespace(log=lambda *a:None, one=lambda *a:{'production_id':None}))
    services = Services(worker)
    made = []
    monkeypatch.setattr(services, 'credentials', lambda *a: {k:k for k in ('mysql','root','redis','jwt','mysql_image','redis_image')})
    monkeypatch.setattr(services, 'network', lambda *a:'internal-data-net')
    monkeypatch.setattr(services, 'volume', lambda client,row,name: made.append(name) or name)
    monkeypatch.setattr(services, 'service', lambda client,row,kind,*a: made.append(kind) or kind)
    monkeypatch.setattr(services, 'ready', lambda *a:True)
    row = {'id':'b'*32,'project_id':'a'*32,'preset':'SPRING_BOOT_JAR','settings':json.dumps({'plan':plan})}
    network, volume, env = services.ensure(object(), row)
    assert ('mysql' in made) == mysql and ('redis' in made) == redis
    assert (volume is not None) == storage and (network is not None) == (mysql or redis)
    assert ('SPRING_DATASOURCE_URL' in env) == mysql and ('SPRING_DATA_REDIS_HOST' in env) == redis
    if mysql:
        assert env['SPRING_JPA_HIBERNATE_DDL_AUTO'] == 'validate'
    assert 'CAMPUS_JWT_SECRET' not in env


def test_runtime_cleanup_removes_partial_owned_image_labels_but_rejects_other_project():
    from campus.runtime import Runtime
    cfg = Settings()
    runtime = Runtime(SimpleNamespace(cfg=cfg, db=None, check=lambda:None))
    owned = {'campus.instance':cfg.instance, 'campus.project':'a'*32, 'campus.kind':'runtime-image'}
    image = SimpleNamespace(id='image', labels=owned.copy())
    live, deleted = [image], []
    def remove(image_id, force, noprune):
        assert not force and noprune
        deleted.append(image_id); live.clear()
    client = SimpleNamespace(images=SimpleNamespace(list=lambda **kw:live.copy(), remove=remove))
    runtime.remove_images(client, owned, 'f'*40)
    assert deleted == ['image']
    live.append(SimpleNamespace(id='foreign',labels={**owned,'campus.project':'b'*32}))
    with pytest.raises(Rejected,match='소유권'):
        runtime.remove_images(client,owned)
    assert deleted == ['image']


def test_image_context_sets_all_ownership_labels_in_one_instruction():
    from campus.runtime_image import image_context
    archive = archive_jars([('work/app.jar',boot_jar())])
    result = io.BytesIO()
    labels = {'campus.project':'a'*32,'campus.deployment':'b'*32,'campus.source-sha':'c'*40}
    image_context(archive,result,'sha256:'+'d'*64,Settings(),spring=True,image_labels=labels)
    with tarfile.open(fileobj=result) as tf:
        dockerfile = tf.extractfile('Dockerfile').read().decode()
    assert dockerfile.count('\nLABEL ') == 1
    assert all(k+'='+json.dumps(v) in dockerfile for k,v in labels.items())
    assert 'RUN ' not in dockerfile
