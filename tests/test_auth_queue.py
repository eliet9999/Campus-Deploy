import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from campus.api import create_app
from campus.auth import initialize_secrets
from campus.config import Settings
from campus.db import DB, uid
from campus.worker import Worker


@pytest.fixture
def setup(tmp_path):
    cfg=Settings(data=tmp_path/'data',secrets=tmp_path/'secrets',min_free=0)
    initialize_secrets(cfg)
    app=create_app(cfg)
    client=TestClient(app,base_url=cfg.origin)
    login=client.post('/api/login',json={'password':(cfg.secrets/'admin-password.txt').read_text().strip()},headers={'Origin':cfg.origin})
    headers={'Origin':cfg.origin,'X-CSRF-Token':login.json()['csrf']}
    worker=Worker(cfg)
    assert worker.acquire()
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        archive.writestr('index.html','<h1>Queue sample</h1>')
    response=client.post('/api/sources/zip',content=data.getvalue(),headers={**headers,'Idempotency-Key':uid()})
    worker.tick()
    return cfg,client,headers,worker,response.json()['id']


def test_auth_origin_csrf_and_host(setup):
    cfg,client,h,worker,sid=setup
    anonymous=TestClient(create_app(cfg),base_url=cfg.origin)
    assert anonymous.post('/api/projects',json={},headers={'Origin':cfg.origin}).status_code==401
    assert client.post('/api/projects',json={},headers={'Origin':cfg.origin}).status_code==403
    assert client.post('/api/projects',json={},headers={**h,'Origin':'http://p-evil.localhost:8080'}).status_code==403
    assert client.get('/api/projects',headers={'Host':'evil.localhost:3000'}).status_code==404
    response=client.get('/api/session')
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store'
    cookie=client.cookies.get('campus_session')
    assert cookie and len(cookie)>32
    logout=client.post('/api/logout',headers=h)
    assert logout.status_code==200
    assert client.get('/api/projects').status_code==401
    client.cookies.set('campus_session',cookie)
    assert client.get('/api/projects').status_code==401


def test_login_rate_limit_persists(setup):
    cfg,_,_,_,_=setup
    client=TestClient(create_app(cfg),base_url=cfg.origin)
    codes=[]
    for _ in range(11):
        codes.append(client.post('/api/login',json={'password':'wrong'},headers={'Origin':cfg.origin}).status_code)
    assert 429 in codes and 401 in codes
    restarted=TestClient(create_app(cfg),base_url=cfg.origin)
    assert restarted.post('/api/login',json={'password':'wrong'},headers={'Origin':cfg.origin}).status_code==429


def test_concurrent_idempotency_and_single_worker(setup):
    cfg,client,h,worker,sid=setup
    key=uid()
    def submit(_):
        return client.post('/api/projects',json={'name':'Queue','slug':'queue','source_id':sid},headers={**h,'Idempotency-Key':key}).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(submit,range(2)))
    assert results[0]==results[1]
    assert len(worker.db.all('SELECT * FROM deployments'))==1
    assert not Worker(cfg).acquire()
    job=worker.claim()
    assert job['subject']==results[0]['deployment_id']
    assert worker.claim() is None
    conflict=client.post('/api/projects',json={'name':'Other','slug':'other','source_id':sid},headers={**h,'Idempotency-Key':key})
    assert conflict.status_code==409


def test_cancel_and_stale_recovery(setup):
    cfg,client,h,worker,sid=setup
    project=client.post('/api/projects',json={'name':'Cancel','slug':'cancel','source_id':sid},headers={**h,'Idempotency-Key':uid()}).json()
    dep=project['deployment_id']
    assert client.post('/api/deployments/'+dep+'/cancel',headers={**h,'Idempotency-Key':uid()}).status_code==200
    worker.tick()
    assert client.get('/api/deployments/'+dep).json()['status']=='CANCELED'
    new=client.post('/api/projects/'+project['id']+'/deployments',json={'source_id':sid},headers={**h,'Idempotency-Key':uid()}).json()['id']
    worker.claim();worker.stage(new,'BUILDING');worker.db.log(new,'preserved log\n')
    with worker.db.tx() as con:
        con.execute('UPDATE worker_lease SET expires=0')
    restarted=Worker(cfg);assert restarted.acquire();restarted.recover()
    row=client.get('/api/deployments/'+new).json()
    assert row['status']=='FAILED' and '재시작' in row['error']
    assert any('preserved log' in item['text'] for item in client.get('/api/deployments/'+new+'/logs').json()['items'])


def test_delete_cancels_queued_and_cleans_only_owner(setup):
    cfg,client,h,worker,sid=setup
    a=client.post('/api/projects',json={'name':'A','slug':'aaa','source_id':sid},headers={**h,'Idempotency-Key':uid()}).json()
    b=client.post('/api/projects',json={'name':'B','slug':'bbb','source_id':sid},headers={**h,'Idempotency-Key':uid()}).json()
    # Cancel both queued builds to avoid needing a gateway in this lifecycle test.
    client.post('/api/deployments/'+b['deployment_id']+'/cancel',headers={**h,'Idempotency-Key':uid()})
    client.delete('/api/projects/'+a['id'],headers={**h,'Idempotency-Key':uid()})
    assert client.post('/api/projects/'+a['id']+'/deployments',json={'source_id':sid},headers={**h,'Idempotency-Key':uid()}).status_code==404
    while worker.tick():
        pass
    assert client.get('/api/cleanup/'+a['id']).json()['deleted']==1
    assert client.get('/api/projects/'+b['id']).status_code==200
    assert (cfg.data/'sources'/sid).exists()  # source is shared with B


def test_log_bound(setup):
    _,client,h,worker,sid=setup
    p=client.post('/api/projects',json={'name':'Logs','slug':'logs','source_id':sid},headers={**h,'Idempotency-Key':uid()}).json()
    worker.cfg.log_limit=1024
    worker.db.log(p['deployment_id'],'x'*10000)
    worker.db.log(p['deployment_id'],'must not append')
    logs=client.get('/api/deployments/'+p['deployment_id']+'/logs').json()['items']
    assert len(logs)==1 and len(logs[0]['text'].encode())<1200
