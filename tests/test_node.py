import io
import json
import tarfile
import pytest
from starlette.datastructures import Headers
import httpx

from campus.config import Settings
from campus.db import DB
from campus.safety import detect, Rejected
from campus.runtime import runtime_name
from campus.runtime_image import image_context
from campus.gateway import proxy_headers


def package(root, deps=None, scripts=None):
    (root / 'package.json').write_text(json.dumps({'scripts': scripts or {'start': 'node server.js'}, 'dependencies': deps or {'express': '5.2.1'}}))
    (root / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{}}')


def test_detect_node_and_vite_priority(tmp_path):
    package(tmp_path)
    assert detect(tmp_path) == 'NODE_SERVER'
    (tmp_path / 'index.html').write_text('optional client index, still npm project')
    assert detect(tmp_path) == 'NODE_SERVER'
    package(tmp_path, {'vite': '8.3.3'}, {'build': 'vite build', 'start': 'vite preview'})
    assert detect(tmp_path) == 'VITE_STATIC'


@pytest.mark.parametrize('dep', ['pg','redis','ws','bullmq','node-cron','@prisma/client'])
def test_unsupported_node_dependencies(tmp_path, dep):
    package(tmp_path, {dep: '1'})
    with pytest.raises(Rejected, match='DB/Redis/WebSocket'):
        detect(tmp_path)


def test_no_user_dockerfile(tmp_path):
    package(tmp_path)
    (tmp_path / 'Dockerfile').write_text('FROM evil')
    with pytest.raises(Rejected, match='Dockerfile'):
        detect(tmp_path)


@pytest.mark.parametrize('value', ['../../api', 'http://api:3000', 'A'*32, 'a'*31, 'a'*33])
def test_runtime_address_cannot_be_injected(value):
    with pytest.raises(Rejected):
        runtime_name(value)


def archive(items):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as tf:
        for name, target in [('work/package.json', None), *items]:
            item = tarfile.TarInfo(name)
            if target:
                item.type, item.linkname = tarfile.SYMTYPE, target
            tf.addfile(item)
    result.seek(0)
    return result


@pytest.mark.parametrize('items', [
    [('work/../escape',None)], [('work/module','/etc/passwd')],
    [('work/module','../../etc')], [('work/link','node_modules'),('work/link/evil',None)],
    [('work/link/evil',None),('work/link','node_modules')], [('work/package.json',None)],
])
def test_image_archive_rejects_escape(items):
    with pytest.raises(Rejected):
        image_context(archive(items), io.BytesIO(), 'sha256:'+'a'*64, Settings())


def test_image_context_accepts_npm_bin_links_without_extraction():
    out = io.BytesIO()
    result = image_context(archive([('work/node_modules/.bin/tool','../tool/index.js'),('work/node_modules/tool/index.js',None)]), out, 'sha256:'+'a'*64, Settings())
    assert result['files'] == 3
    with tarfile.open(fileobj=out) as tf:
        dockerfile = tf.extractfile('Dockerfile').read().decode()
        assert 'RUN ' not in dockerfile and 'USER 1000:1000' in dockerfile
        assert tf.getmember('app/node_modules/.bin/tool').issym()


def test_migration_preserves_old_rows_and_separates_log_limits(tmp_path):
    cfg = Settings(data=tmp_path, log_limit=20)
    db = DB(cfg)
    db.initialize()
    # A second startup must not erase data or fail on existing columns.
    with db.tx() as con:
        con.execute("INSERT INTO sources(id,kind,locator,status,created,request_key) VALUES('s','ZIP','x','READY',0,'k')")
        con.execute("INSERT INTO projects(id,slug,name,source_id,preset,created) VALUES('p','p','p','s','NODE_SERVER',0)")
        con.execute("INSERT INTO deployments(id,project_id,source_id,sha,preset,settings,status,created,stages) VALUES('d','p','s','sha','NODE_SERVER','{}','READY',0,'{}')")
    db.initialize()
    db.log('d','b'*100)
    db.log('d','server started', 'runtime')
    assert db.one("SELECT text FROM logs WHERE stream='runtime'")['text'] == 'server started'
    assert db.one("SELECT status FROM deployments WHERE id='d'")['status'] == 'READY'


@pytest.mark.parametrize('headers_type', [Headers, httpx.Headers])
def test_proxy_strips_hop_headers_but_preserves_repeated_cookies(headers_type):
    values=[('connection','keep-alive, x-private'),('x-private','secret'),('upgrade','websocket'),
            ('set-cookie','a=1'),('set-cookie','b=2'),('content-type','application/json')]
    headers=headers_type(raw=[(k.encode(),v.encode()) for k,v in values]) if headers_type is Headers else headers_type(values)
    assert proxy_headers(headers)==[('set-cookie','a=1'),('set-cookie','b=2'),('content-type','application/json')]
