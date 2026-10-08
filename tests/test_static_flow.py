import io
import json
import threading
import time
import zipfile

import httpx
import uvicorn
from fastapi.testclient import TestClient

from campus.api import create_app
from campus.auth import initialize_secrets
from campus.config import Settings
from campus.db import uid
from campus.gateway import create_app as gateway_app
from campus.worker import Worker


def zip_bytes(version='v1'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('sample/index.html', f'<html><link rel="stylesheet" href="/style.css"><h1>{version}</h1><script src="/app.js"></script></html>')
        z.writestr('sample/style.css', 'body { color: navy }')
        z.writestr('sample/app.js', 'document.body.dataset.loaded="yes"')
        z.writestr('sample/.env', 'SECRET=not-public')
    return out.getvalue()


def test_static_upload_real_gateway_and_rollback(tmp_path):
    import socket
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    cfg = Settings(data=tmp_path / 'data', secrets=tmp_path / 'secrets', port=str(port), gateway=f'http://127.0.0.1:{port}', min_free=0)
    initialize_secrets(cfg)
    client = TestClient(create_app(cfg), base_url='http://localhost:3000')
    server = uvicorn.Server(uvicorn.Config(gateway_app(cfg), log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.02)
    worker = Worker(cfg)
    assert worker.acquire()
    headers = {'Origin': cfg.origin}
    password = (cfg.secrets / 'admin-password.txt').read_text().strip()
    auth = client.post('/api/login', json={'password': password}, headers=headers)
    assert auth.status_code == 200
    headers['X-CSRF-Token'] = auth.json()['csrf']

    def post(path, **kwargs):
        return client.post(path, headers={**headers, 'Idempotency-Key': uid()}, **kwargs)

    def source(version):
        r = post('/api/sources/zip', content=zip_bytes(version))
        assert r.status_code == 202, r.text
        worker.tick()
        sid = r.json()['id']
        assert client.get('/api/sources/' + sid).json()['status'] == 'READY'
        return sid

    def get(host, path='/'):
        return httpx.get(cfg.gateway + path, headers={'Host': host + '.localhost:' + str(port)}, trust_env=False)

    try:
        sid = source('v1')
        p = post('/api/projects', json={'name': 'Demo', 'slug': 'demo', 'source_id': sid}).json()
        worker.tick()
        first = client.get('/api/deployments/' + p['deployment_id']).json()
        assert first['status'] == 'READY', first
        assert 'v1' in get('p-demo').text
        assert get('p-demo', '/style.css').headers['content-type'].startswith('text/css')
        assert get('p-demo', '/.env').status_code == 404
        assert get('p-demo', '/api/projects').status_code == 404
        assert get('unknown').status_code == 404
        sid2 = source('v2')
        second = post(f"/api/projects/{p['id']}/deployments", json={'source_id': sid2}).json()['id']
        worker.tick()
        assert 'v1' in get('p-demo').text
        assert 'v2' in get('d-' + second).text
        switched = post(f"/api/projects/{p['id']}/production", json={'kind': 'promote', 'deployment_id': second})
        assert switched.status_code == 200
        assert 'v2' in get('p-demo').text
        assert 'v1' in get('d-' + first['id']).text
        jobs_before = worker.db.all('SELECT * FROM jobs')
        assert post(f"/api/projects/{p['id']}/production", json={'kind': 'rollback', 'deployment_id': first['id']}).status_code == 200
        assert 'v1' in get('p-demo').text
        assert worker.db.all('SELECT * FROM jobs') == jobs_before
        assert client.get('/api/deployments/' + first['id'] + '/logs').json()['items']
    finally:
        server.should_exit = True
        thread.join(timeout=5)
