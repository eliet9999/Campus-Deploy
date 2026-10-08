import io
import json
import tarfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from campus.config import Settings
from campus.db import DB, uid
from campus.gateway import create_app
from campus.runtime_image import image_context
from campus.safety import Rejected, detect
from campus.services import Services, project_id
from campus.spring import jar_archive, inspect_spring


def spring_source(root):
    (root / 'build.gradle').write_text("plugins { id 'org.springframework.boot' version '4.0.5' }")
    (root / 'src/main').mkdir(parents=True)
    frontend = root / 'frontend'
    frontend.mkdir()
    (frontend / 'package.json').write_text(json.dumps({'scripts': {'build': 'vite build'}, 'devDependencies': {'vite': '8.0.1'}}))
    (frontend / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{}}')


def test_gradle_frontend_detection_ignores_submitted_dockerfile(tmp_path):
    spring_source(tmp_path)
    (tmp_path / 'Dockerfile').write_text('RUN should-never-execute')
    assert detect(tmp_path) == 'SPRING_BOOT'
    assert inspect_spring(tmp_path)['health_path'] == '/api/health'


def test_spring_rejects_unlocked_frontend_and_multimodule(tmp_path):
    spring_source(tmp_path)
    (tmp_path / 'frontend/package-lock.json').unlink()
    with pytest.raises(Rejected, match='lock'):
        detect(tmp_path)
    (tmp_path / 'frontend/package-lock.json').write_text('{"lockfileVersion":3,"packages":{}}')
    (tmp_path / 'settings.gradle').write_text("include 'another-server'")
    with pytest.raises(Rejected, match='다중'):
        detect(tmp_path)


@pytest.mark.parametrize('name,link', [('app.jar','/etc/passwd'), ('../app.jar',None), ('other.jar',None)])
def test_java_result_rejects_unexpected_files_and_links(name, link):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        member = tarfile.TarInfo(name)
        if link:
            member.type, member.linkname = tarfile.SYMTYPE, link
        tf.addfile(member)
    buf.seek(0)
    with pytest.raises(Rejected):
        jar_archive(buf, io.BytesIO(), Settings())


def test_java_image_contains_only_jar_and_operator_dockerfile():
    buf, normalized, context = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        member = tarfile.TarInfo('app.jar')
        member.size = 3
        tf.addfile(member, io.BytesIO(b'jar'))
    buf.seek(0)
    jar_archive(buf, normalized, Settings())
    image_context(normalized, context, 'sha256:'+'a'*64, Settings(), spring=True)
    with tarfile.open(fileobj=context) as tf:
        assert tf.getnames() == ['Dockerfile', 'app/app.jar']
        script = tf.extractfile('Dockerfile').read().decode()
        assert 'RUN ' not in script and 'java' in script and 'USER 1000:1000' in script


@pytest.mark.parametrize('pid', ['../data','x'*32,'a'*33,'', 'a'*31])
def test_service_names_reject_unsafe_project_id(pid):
    with pytest.raises(Rejected):
        project_id(pid)


def test_cleanup_refuses_other_project_resources():
    cfg = Settings()
    services = Services(SimpleNamespace(cfg=cfg))
    with pytest.raises(Rejected, match='소유권'):
        services.owned(SimpleNamespace(attrs={'Labels': {'campus.instance': cfg.instance, 'campus.project': 'b'*32, 'campus.kind':'mysql'}}), 'a'*32, 'mysql')


def test_missing_credentials_never_regenerates_password_for_existing_database(tmp_path):
    cfg=Settings(data=tmp_path);cfg.initialize()
    services=Services(SimpleNamespace(cfg=cfg));pid='a'*32
    volume=SimpleNamespace(attrs={'Labels':services.labels(pid,'mysql-data')})
    client=SimpleNamespace(volumes=SimpleNamespace(get=lambda name:volume))
    with pytest.raises(Rejected,match='백업에서 복원'):
        services.credentials(pid,client)
    assert not list((tmp_path/'services').iterdir())


def test_spring_gateway_splits_api_uploads_and_spa_without_management(tmp_path, monkeypatch):
    cfg = Settings(data=tmp_path)
    db = DB(cfg); db.initialize()
    pid, dep = uid(), uid()
    with db.tx() as con:
        con.execute("INSERT INTO sources(id,kind,locator,status,created,request_key) VALUES('s','ZIP','x','READY',0,'k')")
        con.execute("INSERT INTO projects(id,slug,name,source_id,preset,production_id,created) VALUES(?,'spring','spring','s','SPRING_BOOT',?,0)", (pid,dep))
        con.execute("INSERT INTO deployments(id,project_id,source_id,sha,preset,settings,status,created,stages,artifact,runtime_state) VALUES(?,?,'s','sha','SPRING_BOOT','{}','READY',0,'{}',?,'RUNNING')", (dep,pid,dep))
    folder=tmp_path/'artifacts'/dep
    folder.mkdir(); (folder/'index.html').write_text('Spring UI')
    from fastapi.responses import PlainTextResponse
    async def upstream(row, request):
        return PlainTextResponse('user backend: '+request.url.path, status_code=404 if request.url.path=='/api/projects' else 200)
    monkeypatch.setattr('campus.gateway.proxy_runtime',upstream)
    client=TestClient(create_app(cfg),base_url='http://p-spring.localhost:8080')
    assert client.get('/signin',headers={'Accept':'text/html'}).text=='Spring UI'
    assert client.get('/api/products').text=='user backend: /api/products'
    assert client.post('/api/auth/login').text=='user backend: /api/auth/login'
    assert client.get('/uploads/image.png').text=='user backend: /uploads/image.png'
    assert client.get('/api/projects').status_code==404
    assert client.get('/missing.js').status_code==404
    with db.tx() as con:
        con.execute("UPDATE deployments SET runtime_state='UNAVAILABLE' WHERE id=?",(dep,))
    assert client.get('/').status_code==503


def test_project_credentials_are_redacted_from_both_log_streams(tmp_path):
    cfg=Settings(data=tmp_path);db=DB(cfg);db.initialize()
    pid,dep=uid(),uid()
    with db.tx() as con:
        con.execute("INSERT INTO sources(id,kind,locator,status,created,request_key) VALUES('s','ZIP','x','READY',0,'k')")
        con.execute("INSERT INTO projects(id,slug,name,source_id,preset,created) VALUES(?,'demo','demo','s','SPRING_BOOT',0)",(pid,))
        con.execute("INSERT INTO deployments(id,project_id,source_id,sha,preset,settings,status,created,stages) VALUES(?,?,'s','sha','SPRING_BOOT','{}','READY',0,'{}')",(dep,pid))
    secrets={k:k+'-private-value' for k in ('mysql','root','redis','jwt')}
    (tmp_path/'services'/(pid+'.json')).write_text(json.dumps(secrets))
    for stream in ('build','runtime'):
        db.log(dep, ' '.join(secrets.values()), stream)
    assert all(row['text']==' '.join(['[REDACTED]']*4) for row in db.all('SELECT text FROM logs'))


def test_spring_proxy_reconstructs_forwarded_authority_including_port_prefix(monkeypatch):
    import asyncio
    import httpx
    from starlette.requests import Request
    from campus.gateway import proxy_runtime
    captured={}
    class Client:
        def __init__(self,**kwargs):pass
        def build_request(self,method,target,headers,content):
            captured.update(dict(headers));return object()
        async def send(self,*args,**kwargs):
            raise httpx.ConnectError('deliberate test stop after capturing headers')
        async def aclose(self):pass
    monkeypatch.setattr('campus.gateway.httpx.AsyncClient',Client)
    async def receive():return {'type':'http.request','body':b'','more_body':False}
    scope={'type':'http','method':'GET','scheme':'http','path':'/api/products','raw_path':b'/api/products','query_string':b'',
           'headers':[(b'host',b'p-shop.localhost:8080'),(b'forwarded',b'host=api:3000'),
                      (b'x-forwarded-port',b'3000'),(b'x-forwarded-prefix',b'/admin')],
           'server':('gateway',8080)}
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(proxy_runtime({'id':'a'*32,'runtime_state':'RUNNING'},Request(scope,receive)))
    assert 'forwarded' not in captured and 'x-forwarded-port' not in captured and 'x-forwarded-prefix' not in captured
    assert captured['x-forwarded-host']=='p-shop.localhost:8080' and captured['x-forwarded-proto']=='http'
