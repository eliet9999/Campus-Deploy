"""Exercise the public HTTP API. Requires setup + running compose; keeps demonstration projects."""
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import httpx

ROOT = Path(__file__).resolve().parents[1]


class Client:
    def __init__(self):
        self.origin = os.getenv('ADMIN_ORIGIN', 'http://localhost:3000')
        self.http = httpx.Client(base_url=self.origin, timeout=60, trust_env=False)
        password = (ROOT / '.secrets/admin-password.txt').read_text().strip()
        result = self.http.post('/api/login', json={'password': password}, headers={'Origin': self.origin})
        result.raise_for_status()
        self.headers = {'Origin': self.origin, 'X-CSRF-Token': result.json()['csrf']}

    def call(self, method, path, key=None, **kwargs):
        r = self.http.request(method, path, headers={**self.headers, 'Idempotency-Key': key or uuid.uuid4().hex}, **kwargs)
        r.raise_for_status()
        return r.json()

    def wait(self, path, timeout=180, terminal=('READY', 'FAILED', 'CANCELED')):
        started = time.monotonic()
        previous = None
        while time.monotonic() - started < timeout:
            row = self.call('GET', path)
            if row.get('status') != previous:
                print(path, row.get('status'), flush=True)
                previous = row.get('status')
            if row.get('status') in terminal:
                return row
            time.sleep(.5)
        raise AssertionError(f'Timeout waiting for {path}')

    def upload(self, sample):
        row = self.call('POST', '/api/sources/zip', content=(ROOT / 'samples' / (sample + '.zip')).read_bytes())
        row = self.wait('/api/sources/' + row['id'])
        assert row['status'] == 'READY', row
        return row

    def deploy(self, pid, sid):
        row = self.call('POST', f'/api/projects/{pid}/deployments', json={'source_id': sid})
        return self.wait('/api/deployments/' + row['id'])

    def site(self, url, path='/', accept='text/html'):
        parsed = urlsplit(url)
        return httpx.get('http://127.0.0.1:8080' + path, headers={'Host': parsed.netloc, 'Accept': accept}, trust_env=False)


def main():
    client = Client()
    stamp = str(int(time.time()))
    source = client.upload('static-v1')
    body = {'name': 'Campus Garden', 'slug': 'garden-' + stamp, 'source_id': source['id']}
    key = uuid.uuid4().hex
    project = client.call('POST', '/api/projects', key=key, json=body)
    duplicate = client.call('POST', '/api/projects', key=key, json=body)
    assert project == duplicate
    v1 = client.wait('/api/deployments/' + project['deployment_id'])
    assert v1['status'] == 'READY', v1
    project = client.call('GET', '/api/projects/' + project['id'])
    url = project['production_url']
    assert 'VERSION 01' in client.site(url).text
    source2 = client.upload('static-v2')
    v2 = client.deploy(project['id'], source2['id'])
    assert v2['status'] == 'READY', v2
    assert 'VERSION 01' in client.site(url).text
    assert 'VERSION 02' in client.site(v2['preview_url']).text
    client.call('POST', f"/api/projects/{project['id']}/production", json={'kind': 'promote', 'deployment_id': v2['id']})
    assert 'VERSION 02' in client.site(url).text
    assert 'VERSION 01' in client.site(v1['preview_url']).text
    client.call('POST', f"/api/projects/{project['id']}/production", json={'kind': 'rollback', 'deployment_id': v1['id']})
    assert 'VERSION 01' in client.site(url).text
    react_source = client.upload('react-vite-spa')
    react = client.call('POST', '/api/projects', json={'name': 'React Counter', 'slug': 'react-' + stamp, 'source_id': react_source['id']})
    react_dep = client.wait('/api/deployments/' + react['deployment_id'])
    logs = client.call('GET', f"/api/deployments/{react_dep['id']}/logs")
    (ROOT / 'evidence/vite-build-log.json').write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding='utf-8')
    assert react_dep['status'] == 'READY', react_dep
    assert client.site(react_dep['preview_url'], '/about').status_code == 200
    assert client.site(react_dep['preview_url'], '/missing.js').status_code == 404
    bad_source = client.upload('build-failure')
    failed = client.deploy(project['id'], bad_source['id'])
    assert failed['status'] == 'FAILED' and failed['exit_code'] == 7, failed
    assert 'VERSION 01' in client.site(url).text
    failure_logs = client.call('GET', f"/api/deployments/{failed['id']}/logs")
    assert any('INTENTIONAL_BUILD_FAILURE' in item['text'] for item in failure_logs['items'])
    (ROOT / 'evidence/failure-build-log.json').write_text(json.dumps(failure_logs, ensure_ascii=False, indent=2), encoding='utf-8')
    unsupported = client.call('POST', '/api/sources/zip', content=(ROOT / 'samples/unsupported-server.zip').read_bytes())
    assert client.wait('/api/sources/' + unsupported['id'])['status'] == 'FAILED'
    result = {'static_project': project['id'], 'production_url': url, 'v1': v1['id'], 'v2': v2['id'],
              'react_project': react['id'], 'react_deployment': react_dep['id'], 'react_url': react_dep['preview_url'],
              'failed_deployment': failed['id'], 'checks': 'HTTP API/STATIC/Vite real Docker/npm/failure/rollback/idempotency; browser checked separately'}
    (ROOT / 'evidence/smoke.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
