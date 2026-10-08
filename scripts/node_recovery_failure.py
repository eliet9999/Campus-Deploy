"""Fault-inject a missing owned production image, then restore it and verify recovery.
Run only after node_smoke.py on a local demo installation. Stops Campus Deploy.
The backup is retained if restore fails; no unrelated Docker resources are changed.
"""
import json
import docker
from smoke import Client, ROOT
from node_smoke import compose, eventually


def main():
    report=json.loads((ROOT/'evidence/node-smoke.json').read_text(encoding='utf-8'))
    c,engine=Client(),docker.from_env()
    dep=report['v1'];pid=report['project_id'];image_id=report['image'];url=report['production_url']
    row=c.call('GET','/api/projects/'+pid)
    assert row['production_id']==dep
    image=engine.images.get(image_id)
    assert image.labels['campus.deployment']==dep and image.labels['campus.project']==pid
    assert image.labels['campus.instance']=='campus-deploy-local'
    container=engine.containers.get('runtime-'+dep)
    assert container.labels['campus.deployment']==dep and container.image.id==image_id
    build_before=c.call('GET',f'/api/deployments/{dep}/logs?stream=build')
    backup=ROOT/'evidence/recovery-image-backup.tar'
    with backup.open('xb') as out:
        for chunk in image.save(named=True):
            out.write(chunk)
    try:
        compose('stop')
        container.remove(force=True)
        engine.images.remove(image_id,noprune=True)
        compose('up','-d','--no-build','--wait')
        eventually(lambda:c.call('GET','/api/deployments/'+dep)['status']=='UNAVAILABLE')
        failed=c.call('GET','/api/projects/'+pid)
        assert failed['production_id']==dep and failed['production_health']=='UNAVAILABLE'
        assert c.site(url).status_code==503
        assert c.site(report['static_url']).status_code==200
        print('PASS: startup missing image is UNAVAILABLE/503, static remains accessible',flush=True)
    finally:
        with backup.open('rb') as source:
            engine.images.load(source.read())
        backup.unlink()
        compose('up','-d','--no-build','--wait')
    eventually(lambda:c.site(url,'/version').status_code==200)
    restored=c.call('GET','/api/deployments/'+dep)
    assert restored['status']=='READY' and restored['runtime_state']=='RUNNING'
    assert engine.containers.get('runtime-'+dep).image.id==image_id
    assert c.call('GET',f'/api/deployments/{dep}/logs?stream=build')==build_before
    result={'missing_image_status':'UNAVAILABLE','gateway_status':503,'static_preserved':True,
            'automatic_restore':'RUNNING','same_image':image_id,'no_rebuild':True}
    (ROOT/'evidence/node-recovery-failure.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('PASS: restored image automatically resumes production without build',flush=True)


if __name__=='__main__':
    main()
