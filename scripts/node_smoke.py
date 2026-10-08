"""Real Docker/HTTP P1 checks. Only this installation's test resources are changed."""
import json
import subprocess
import time
from urllib.parse import urlsplit

import docker
import httpx
from smoke import Client, ROOT


def compose(*args):
    subprocess.run(['docker', 'compose', *args], cwd=ROOT, check=True)


def eventually(fn, timeout=100):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except (httpx.HTTPError, docker.errors.DockerException):
            pass
        time.sleep(1)
    raise AssertionError('Condition not reached')


def main():
    c, engine = Client(), docker.from_env()
    stamp = str(int(time.time()))
    before = {x.id: x.status for x in engine.containers.list(all=True)
              if x.labels.get('com.docker.compose.project') != 'campus-deploy'
              and x.labels.get('campus.instance') != 'campus-deploy-local'}
    report = {'checks': [], 'unrelated_containers_before': before}
    def check(name):
        report['checks'].append(name)
        print('PASS:', name, flush=True)
    def deploy_project(sample, slug):
        source = c.upload(sample)
        p = c.call('POST','/api/projects',json={'name':sample,'slug':slug+'-'+stamp,'source_id':source['id']})
        d = c.wait('/api/deployments/'+p['deployment_id'])
        assert d['status'] == 'READY', d
        return c.call('GET','/api/projects/'+p['id']), d
    def switch(p, d, kind='promote', expected='DONE'):
        r = c.call('POST',f"/api/projects/{p['id']}/production",json={'deployment_id':d['id'],'kind':kind})
        op = c.wait('/api/operations/'+r['operation_id'],terminal=('DONE','FAILED'))
        assert op['status'] == expected, op
        return op
    def runtime(d):
        return engine.containers.get('runtime-'+d['id'])
    def version(url, v):
        return c.site(url,'/version').json().get('version') == v
    def log(d, stream):
        return ''.join(x['text'] for x in c.call('GET',f"/api/deployments/{d['id']}/logs?stream={stream}")['items'])

    static, static_dep = deploy_project('static-v1','node-coexist-static')
    p, v1 = deploy_project('node-express-v1','node-demo')
    url = p['production_url']
    assert v1['preset'] == 'NODE_SERVER' and version(url,'v1')
    assert all(s in v1['stages'] for s in ('BUILDING','STARTING','HEALTH_CHECK','READY'))
    check('automatic NODE_SERVER detection; npm ci/image/start/health/READY; v1 production')
    container = runtime(v1)
    attrs = container.attrs
    assert not attrs['HostConfig']['PortBindings']
    assert attrs['Config']['User'] == '1000:1000'
    assert attrs['HostConfig']['ReadonlyRootfs'] and attrs['HostConfig']['CapDrop'] == ['ALL']
    assert attrs['HostConfig']['Memory'] == 512*1024**2 and attrs['HostConfig']['PidsLimit'] == 128
    assert attrs['HostConfig']['NanoCpus'] == 500_000_000
    assert not any(m['Type']=='bind' for m in attrs['Mounts'])
    network = next(iter(attrs['NetworkSettings']['Networks']))
    assert engine.networks.get(network).attrs['Internal']
    assert network not in engine.containers.get('campus-deploy-api-1').attrs['NetworkSettings']['Networks']
    assert network not in engine.containers.get('campus-deploy-worker-1').attrs['NetworkSettings']['Networks']
    check('nonroot/read-only/CPU/memory/PIDs/capabilities/no host ports; isolated runtime network')
    assert 'npm ci' in log(v1,'build') and '[image]' in log(v1,'build')
    eventually(lambda:'NODE_READY v1' in log(v1,'runtime'))
    check('separate persisted build/runtime logs')
    assert c.site(url,'/api/projects').status_code == 404
    assert c.site(url,'/api/example').json()['app'] == 'Express sample'
    posted = httpx.post('http://127.0.0.1:8080/greet',headers={'Host':urlsplit(url).netloc},json={'name':'Campus'},trust_env=False)
    assert posted.status_code == 200 and 'v1' in posted.json()['message']
    ws = httpx.get('http://127.0.0.1:8080/',headers={'Host':urlsplit(url).netloc,'Upgrade':'websocket'},trust_env=False)
    assert ws.status_code == 426
    check('POST proxy, user API, management API boundary, WebSocket rejection')
    v2 = c.deploy(p['id'],c.upload('node-express-v2')['id'])
    assert v2['status'] == 'READY', v2
    assert version(v2['preview_url'],'v2') and version(url,'v1')
    check('v2 Preview preserves v1 production')
    runtime(v2).pause()
    try:
        failed = switch(p,v2,expected='FAILED')
        assert version(url,'v1')
        report['failed_promote'] = failed
        check('failed promotion health check preserves production')
    finally:
        runtime(v2).unpause()
    switch(p,v2)
    assert version(url,'v2')
    check('v2 promote at same production URL')
    saved_image, old_container = v1['runtime_image'], container.id
    baseline_build = log(v1,'build')
    engine.images.get(saved_image).save  # assert API available before changing test resources
    backup = ROOT/'evidence/node-v1-image.tar'
    with backup.open('wb') as out:
        for chunk in engine.images.get(saved_image).save(named=True):
            out.write(chunk)
    try:
        # The retention policy already removed old v1; missing image tests rollback failure.
        try:
            runtime(v1).remove(force=True)
        except docker.errors.NotFound:
            pass
        engine.images.remove(saved_image, noprune=True)
        report['failed_rollback'] = switch(p,v1,'rollback',expected='FAILED')
        assert version(url,'v2')
        check('missing image rollback fails without changing v2 production')
    finally:
        with backup.open('rb') as source:
            engine.images.load(source.read())
        backup.unlink()
    switch(p,v1,'rollback')
    assert version(url,'v1')
    assert runtime(v1).id != old_container and runtime(v1).image.id == saved_image
    assert log(v1,'build') == baseline_build
    check('rollback recreates v1 from same image without npm/build')
    bad = c.deploy(p['id'],c.upload('node-express-failure')['id'])
    assert bad['status'] == 'FAILED' and version(url,'v1'), bad
    assert 'INTENTIONAL_RUNTIME_FAILURE' in log(bad,'runtime')
    assert not engine.containers.list(all=True,filters={'label':'campus.deployment='+bad['id']})
    check('runtime failure is FAILED with stderr retained; existing production preserved')
    other, other_dep = deploy_project('node-express-v2','node-isolation')
    other_image = other_dep['runtime_image']
    c.call('DELETE','/api/projects/'+other['id'])
    eventually(lambda:c.call('GET','/api/cleanup/'+other['id'])['deleted'])
    assert version(url,'v1') and c.site(static['production_url']).status_code == 200
    assert engine.images.get(saved_image)
    try:
        engine.images.get(other_image)
        raise AssertionError('deleted project image remains')
    except docker.errors.ImageNotFound:
        pass
    check('project cleanup removes only owned runtime/image; static and other Node production preserved')
    compose('restart','api','gateway','worker')
    eventually(lambda:version(url,'v1'))
    eventually(lambda:c.call('GET','/api/health')['worker_alive'])
    assert c.site(static['production_url']).status_code == 200
    check('service restart preserves Node and static production')
    compose('stop')
    # Simulate loss of runtime containers while all control-plane services are stopped.
    runtime(v1).remove(force=True)
    compose('up','-d','--no-build','--wait')
    eventually(lambda:version(url,'v1'))
    current = c.call('GET','/api/deployments/'+v1['id'])
    assert current['runtime_state'] == 'RUNNING' and current['status'] == 'READY'
    assert runtime(v1).image.id == saved_image and log(v1,'build') == baseline_build
    assert c.site(static['production_url']).status_code == 200
    check('full project stop/start and missing production container: automatic image recovery, no rebuild')
    after = {x.id:x.status for x in engine.containers.list(all=True) if x.id in before}
    assert before == after
    check('unrelated Docker containers unchanged (no global Docker restart/prune)')
    report.update(project_id=p['id'],production_url=url,v1=v1['id'],v2=v2['id'],image=saved_image,
                  static_url=static['production_url'],failed=bad['id'],unrelated_containers_after=after)
    (ROOT/'evidence/node-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
