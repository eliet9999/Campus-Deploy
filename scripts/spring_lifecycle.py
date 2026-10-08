"""Local Spring data persistence / rollback / recovery / owned cleanup checks.

Requires spring_smoke.py and the Spring browser test to have completed.
Stops only Campus Deploy during recovery. Does not change AWS or other projects.
"""
import base64
import json
import secrets
import time
from urllib.parse import urlsplit

import docker
import httpx
from smoke import Client, ROOT
from node_smoke import compose, eventually


def main():
    c, engine = Client(), docker.from_env(timeout=30)
    seed = json.loads((ROOT/'evidence/spring.json').read_text())
    pid, v1 = seed['project_id'], seed['deployment_id']
    p = c.call('GET','/api/projects/'+pid)
    url, host = p['production_url'], urlsplit(p['production_url']).netloc
    before = {x.id:x.status for x in engine.containers.list(all=True)
              if x.labels.get('com.docker.compose.project')!='campus-deploy' and x.labels.get('campus.instance')!='campus-deploy-local'}
    report={'project_id':pid,'url':url,'checks':[]}
    def check(name):
        report['checks'].append(name);print('PASS:',name,flush=True)
        (ROOT/'evidence/spring-lifecycle.json').write_text(json.dumps(report,indent=2),'utf-8')
    def owned(container, project=pid):
        assert container.labels['campus.instance']=='campus-deploy-local' and container.labels['campus.project']==project
        return container
    def runtime(dep):
        return owned(engine.containers.get('runtime-'+dep))
    def logs(dep):
        return c.call('GET',f'/api/deployments/{dep}/logs?stream=build')
    app=httpx.Client(base_url='http://127.0.0.1:8080',headers={'Host':host,'Origin':url,'Accept':'application/json'},trust_env=False,timeout=20)
    email='campus-persistence-'+str(int(time.time()))+'@example.test'
    password=secrets.token_urlsafe(24)
    r=app.post('/api/auth/signup',json={'email':email,'password':password,'name':'Campus persistence test','address':'Test','phone':'01000000000','role':'USER'})
    assert r.status_code==200,(r.status_code,r.text[:300])
    r=app.post('/api/auth/login',json={'email':email,'password':password})
    assert r.status_code==200,(r.status_code,r.text[:300])
    app.headers['Authorization']='Bearer '+r.json()['accessToken']
    assert app.post('/api/auth/reissue').status_code==200
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC')
    uploaded=app.post('/api/files/upload',files={'file':('persistence.png',png,'image/png')})
    assert uploaded.status_code==200
    image_path=uploaded.text
    assert image_path.startswith('/uploads/') and app.get(image_path).content==png
    product=app.post('/api/products',json={'categoryId':1,'name':'Campus persistence product','price':1000,'stockQuantity':3,'description':'Test data','imageUrls':[image_path]})
    assert product.status_code==200
    product_id=product.json()['id']
    check('real MySQL signup/product, Redis login/reissue, persistent upload')
    data_names=[f'campus-deploy-local-{pid}-{kind}' for kind in ('mysql','redis')]
    for name in ['runtime-'+v1,*data_names]:
        container=owned(engine.containers.get(name)); attrs=container.attrs
        assert not attrs['HostConfig']['PortBindings'] and attrs['Config']['User']=='1000:1000'
        assert attrs['HostConfig']['ReadonlyRootfs'] and attrs['HostConfig']['CapDrop']==['ALL']
        assert not any(m['Type']=='bind' for m in attrs['Mounts'])
    network=f'campus-deploy-local-{pid}-data-net'
    assert engine.networks.get(network).attrs['Internal']
    for service in ('api','gateway','worker'):
        assert network not in engine.containers.get('campus-deploy-'+service+'-1').attrs['NetworkSettings']['Networks']
    check('nonroot/read-only/no host ports; DB network excludes API/gateway/worker; no host binds')
    old_image=runtime(v1).image.id
    old_build=logs(v1)
    d=c.call('POST',f'/api/projects/{pid}/deployments',json={'source_id':seed['source_id']})
    v2=c.wait('/api/deployments/'+d['id'],timeout=1200)
    assert v2['status']=='READY',v2.get('error')
    assert c.call('GET','/api/projects/'+pid)['production_id']==v1
    env=runtime(v2['id']).attrs['Config']['Env']
    assert 'SPRING_JPA_HIBERNATE_DDL_AUTO=validate' in env
    assert app.get('/api/products/'+str(product_id)).status_code==200
    check('new Spring Preview preserves production pointer/data; schema validation only')
    def switch(dep,kind):
        op=c.call('POST',f'/api/projects/{pid}/production',json={'deployment_id':dep,'kind':kind})
        result=c.wait('/api/operations/'+op['operation_id'],timeout=240,terminal=('DONE','FAILED'))
        assert result['status']=='DONE',result.get('error')
    switch(v2['id'],'promote')
    old_container=runtime(v1).id if engine.containers.list(all=True,filters={'name':'^/runtime-'+v1+'$'}) else None
    switch(v1,'rollback')
    assert runtime(v1).image.id==old_image and runtime(v1).id!=old_container and logs(v1)==old_build
    assert app.get('/api/products/'+str(product_id)).status_code==200 and app.get(image_path).content==png
    assert app.post('/api/auth/reissue').status_code==200
    check('promote/code rollback without rebuild; MySQL/uploads/Redis session/signing key preserved')
    # Remove only this project's runtime and service containers, never volumes/images.
    compose('stop')
    for name in ['runtime-'+v1,*data_names]:
        owned(engine.containers.get(name)).remove(force=True, v=True)
    compose('up','-d','--no-build','--wait')
    # Metadata can still contain yesterday's RUNNING before startup reconciliation
    # reaches this project. Require the recreated container and live API as well.
    eventually(lambda:runtime(v1).attrs['State']['Running'] and
               c.call('GET','/api/projects/'+pid)['production_health']=='RUNNING' and
               app.get('/api/products/'+str(product_id)).status_code==200,timeout=240)
    assert runtime(v1).image.id==old_image and logs(v1)==old_build
    assert app.get('/api/products/'+str(product_id)).status_code==200 and app.get(image_path).content==png
    assert app.post('/api/auth/reissue').status_code==200
    check('Compose stop/start + missing runtime/MySQL/Redis containers: same image/data/session automatically recovered')
    # Browser smoke created an independent disposable project. Verify full scoped removal.
    other=json.loads((ROOT/'evidence/browser-spring.json').read_text())
    other_pid=other['projectId'];assert other_pid!=pid
    report['deleted_project_id']=other_pid
    other_before=c.call('GET','/api/projects/'+other_pid)
    other_images=[d['runtime_image'] for d in other_before['deployments'] if d['runtime_image']]
    c.call('DELETE','/api/projects/'+other_pid)
    eventually(lambda:c.call('GET','/api/cleanup/'+other_pid)['deleted'],timeout=120)
    labels=['campus.instance=campus-deploy-local','campus.project='+other_pid]
    assert not engine.containers.list(all=True,filters={'label':labels})
    assert not engine.volumes.list(filters={'label':labels}) and not engine.networks.list(filters={'label':labels})
    for image in other_images:
        try:engine.images.get(image)
        except docker.errors.ImageNotFound:pass
        else:raise AssertionError('deleted project image remains')
    assert app.get('/api/products/'+str(product_id)).status_code==200
    check('delete removes only selected Spring project containers/images/volumes/network; other Spring data preserved')
    after={x.id:x.status for x in engine.containers.list(all=True)
           if x.labels.get('com.docker.compose.project')!='campus-deploy' and x.labels.get('campus.instance')!='campus-deploy-local'}
    assert before==after
    check('unrelated Docker containers unchanged')
    report.update(deployment=v1,image=old_image,source_sha=seed['source_sha'],unrelated_container_count=len(before),status='PASS')
    (ROOT/'evidence/spring-lifecycle.json').write_text(json.dumps(report,indent=2),'utf-8')


if __name__=='__main__':main()
