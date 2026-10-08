"""Transaction and recovery fault tests; Docker lifecycle is tested by node_smoke.py."""
import json
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from campus.api import create_app
from campus.auth import initialize_secrets
from campus.config import Settings
from campus.db import uid
from campus.safety import Rejected
from campus.worker import Worker


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg=Settings(data=tmp_path/'data',secrets=tmp_path/'secrets',min_free=0)
    initialize_secrets(cfg)
    worker=Worker(cfg);assert worker.acquire()
    pid,a,b=uid(),uid(),uid()
    with worker.db.tx() as con:
        con.execute("INSERT INTO sources(id,kind,locator,status,created,request_key) VALUES('s','ZIP','test','READY',0,'k')")
        con.execute("INSERT INTO projects(id,slug,name,source_id,preset,production_id,created) VALUES(?,'demo','demo','s','NODE_SERVER',?,0)",(pid,a))
        for dep in (a,b):
            con.execute("INSERT INTO deployments(id,project_id,source_id,sha,preset,settings,status,created,stages,runtime_image,runtime_state) VALUES(?,?,'s',?,'NODE_SERVER','{}','READY',0,'{}',?,'RUNNING')",(dep,pid,'a'*64,'sha256:'+'b'*64))
        con.execute("INSERT INTO transitions(project_id,deployment_id,kind,created) VALUES(?,?,'promote',0)",(pid,a))
    monkeypatch.setattr(worker.runtime,'start',lambda row,recreate=False:'container')
    monkeypatch.setattr(worker.runtime,'health',lambda row:{'status':200})
    monkeypatch.setattr(worker.runtime,'retain',lambda pid:None)
    monkeypatch.setattr(worker,'cleanup_containers',lambda **kw:None)
    client=TestClient(create_app(cfg),base_url=cfg.origin)
    auth=client.post('/api/login',json={'password':(cfg.secrets/'admin-password.txt').read_text().strip()},headers={'Origin':cfg.origin})
    headers={'Origin':cfg.origin,'X-CSRF-Token':auth.json()['csrf']}
    def switch(dep,kind='promote',key=None):
        return client.post(f'/api/projects/{pid}/production',json={'deployment_id':dep,'kind':kind},headers={**headers,'Idempotency-Key':key or uid()})
    return SimpleNamespace(worker=worker,client=client,pid=pid,a=a,b=b,switch=switch)


def test_switch_is_idempotent_and_only_changes_pointer_after_health(env):
    key=uid();r=env.switch(env.b,key=key)
    assert r.json()==env.switch(env.b,key=key).json()
    assert env.client.get('/api/projects/'+env.pid).json()['production_id']==env.a
    assert env.switch(env.b).status_code==409
    env.worker.tick()
    assert env.client.get('/api/projects/'+env.pid).json()['production_id']==env.b
    assert env.client.get('/api/operations/'+r.json()['operation_id']).json()['status']=='DONE'


def test_failed_health_preserves_production_and_survives_refresh(env,monkeypatch):
    def fail(row):
        raise Rejected('test health failure')
    monkeypatch.setattr(env.worker.runtime,'health',fail)
    r=env.switch(env.b);env.worker.tick()
    assert env.client.get('/api/projects/'+env.pid).json()['production_id']==env.a
    assert env.client.get('/api/deployments/'+env.b).json()['status']=='UNAVAILABLE'
    assert env.client.get('/api/operations/'+r.json()['operation_id']).json()['status']=='FAILED'


def test_interrupted_switch_does_not_replay_pointer_change(env):
    r=env.switch(env.b);env.worker.claim()
    env.worker.recover()
    assert env.client.get('/api/projects/'+env.pid).json()['production_id']==env.a
    assert env.client.get('/api/operations/'+r.json()['operation_id']).json()['status']=='FAILED'


def test_rollback_recreates_saved_runtime_without_build_job(env,monkeypatch):
    env.switch(env.b);env.worker.tick()
    calls=[]
    monkeypatch.setattr(env.worker.runtime,'start',lambda row,recreate=False:calls.append((row['runtime_image'],recreate)))
    env.switch(env.a,'rollback');env.worker.tick()
    assert calls==[('sha256:'+'b'*64,True)]
    assert not env.worker.db.one("SELECT 1 FROM jobs WHERE kind='DEPLOY'")
    assert env.client.get('/api/projects/'+env.pid).json()['production_id']==env.a


def test_startup_failure_is_unavailable_not_ready_and_keeps_pointer(env,monkeypatch):
    import campus.runtime as runtime_module
    monkeypatch.setattr(runtime_module.docker,'from_env',lambda **kw:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(env.worker.runtime,'get',lambda client,row:None)
    def unavailable(row,recreate=False):
        raise Rejected('saved image missing')
    monkeypatch.setattr(env.worker.runtime,'start',unavailable)
    env.worker.runtime.reconcile(startup=True)
    deployment=env.client.get('/api/deployments/'+env.a).json()
    assert deployment['status']=='UNAVAILABLE' and deployment['deployment_status']=='READY'
    project=env.client.get('/api/projects/'+env.pid).json()
    assert project['production_id']==env.a and project['production_health']=='UNAVAILABLE'
    assert project['production_error']=='saved image missing'
