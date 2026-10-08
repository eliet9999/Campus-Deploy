"""Destructive only to this Compose worker and test-owned projects. Keeps existing production sites."""
import io
import json
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from smoke import Client, ROOT


def docker(*args):
    result=subprocess.run(['docker',*args],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def wait_for(fn, timeout=120):
    start=time.monotonic()
    while time.monotonic()-start<timeout:
        value=fn()
        if value:
            return value
        time.sleep(.5)
    raise AssertionError('condition timeout')


def no_resources(dep):
    containers=docker('ps','-aq','--filter','label=campus.deployment='+dep)
    volumes=docker('volume','ls','-q','--filter','label=campus.deployment='+dep)
    return not containers and not volumes


def slow_source(client):
    package=json.loads((ROOT/'samples/build-failure/package.json').read_text())
    package['scripts']['build']='node slow.mjs'
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('package.json',json.dumps(package))
        z.writestr('package-lock.json',(ROOT/'samples/build-failure/package-lock.json').read_bytes())
        z.writestr('slow.mjs',"console.log('SLOW_BUILD_STARTED'); setTimeout(()=>process.exit(0),120000);\n")
    (ROOT/'samples/slow-build.zip').write_bytes(data.getvalue())
    row=client.call('POST','/api/sources/zip',content=data.getvalue())
    row=client.wait('/api/sources/'+row['id'])
    assert row['status']=='READY',row
    return row['id']


def main():
    c=Client();results={}
    smoke=json.loads((ROOT/'evidence/smoke.json').read_text())
    def production_ok():
        assert 'VERSION 01' in c.site(smoke['production_url']).text
    production_ok()
    # Real public GitHub default-branch resolution and immutable commit archive.
    git=c.call('POST','/api/sources/git',json={'url':'https://github.com/mdn/beginner-html-site-styled.git'})
    git=c.wait('/api/sources/'+git['id'])
    results['github']=git
    if git['status']=='READY':
        assert len(git['sha'])==40 and git['branch']
        project=c.call('POST','/api/projects',json={'name':'GitHub · MDN static','slug':'github-'+str(int(time.time())),'source_id':git['id']})
        deployed=c.wait('/api/deployments/'+project['deployment_id'])
        assert deployed['status']=='READY',deployed
        results['github_deployment']=deployed
    else:
        print('GitHub retrieval unverified:',git.get('error'),flush=True)
    static=c.upload('static-v1')['id'];slow=slow_source(c)
    project=c.call('POST','/api/projects',json={'name':'Lifecycle check','slug':'lifecycle-'+str(int(time.time())),'source_id':static})
    pid=project['id'];assert c.wait('/api/deployments/'+project['deployment_id'])['status']=='READY'
    # Two durable requests while worker stopped, plus duplicate request collapse.
    docker('compose','stop','worker')
    key=uuid.uuid4().hex
    def submit(_):
        return c.call('POST',f'/api/projects/{pid}/deployments',key=key,json={'source_id':static})
    with ThreadPoolExecutor(max_workers=2) as pool:
        duplicate=list(pool.map(submit,range(2)))
    assert duplicate[0]==duplicate[1]
    a=duplicate[0]['id'];b=c.call('POST',f'/api/projects/{pid}/deployments',json={'source_id':static})['id']
    assert c.call('GET','/api/deployments/'+a)['status']=='QUEUED'
    assert c.call('GET','/api/deployments/'+b)['status']=='QUEUED'
    docker('compose','start','worker')
    ra=c.wait('/api/deployments/'+a);rb=c.wait('/api/deployments/'+b)
    assert ra['status']==rb['status']=='READY'
    assert ra['finished']<=rb['stages']['FETCHING']
    results['queue']={'first':a,'second':b,'duplicate_collapsed':True,'serial_timing':True}
    # Cancel a real running build and inspect its actual isolation settings.
    cancel=c.call('POST',f'/api/projects/{pid}/deployments',json={'source_id':slow})['id']
    cid=wait_for(lambda:docker('ps','-q','--filter','label=campus.deployment='+cancel))
    inspect=json.loads(docker('inspect',cid))[0]
    hc=inspect['HostConfig']
    assert inspect['Config']['User']=='1000:1000' and hc['ReadonlyRootfs']
    assert hc['Memory']==2147483648 and hc['NanoCpus']==1000000000 and hc['PidsLimit']==256
    assert hc['CapDrop']==['ALL'] and 'no-new-privileges:true' in hc['SecurityOpt']
    assert hc['NetworkMode']=='bridge' and not any('docker.sock' in m['Destination'] for m in inspect['Mounts'])
    results['isolation']={'user':inspect['Config']['User'],'memory':hc['Memory'],'nano_cpus':hc['NanoCpus'],'pids':hc['PidsLimit'],'network':hc['NetworkMode'],'readonly_root':True,'mounts':inspect['Mounts']}
    c.call('POST','/api/deployments/'+cancel+'/cancel')
    assert c.wait('/api/deployments/'+cancel)['status']=='CANCELED'
    wait_for(lambda:no_resources(cancel));production_ok();results['cancel']=cancel
    # Kill the worker during a build; lease expiry and restart must fail it and clean owned resources.
    stale=c.call('POST',f'/api/projects/{pid}/deployments',json={'source_id':slow})['id']
    wait_for(lambda:docker('ps','-q','--filter','label=campus.deployment='+stale))
    docker('compose','kill','-s','SIGKILL','worker');docker('compose','start','worker')
    recovered=c.wait('/api/deployments/'+stale,timeout=100)
    assert recovered['status']=='FAILED' and '재시작' in recovered['error'],recovered
    wait_for(lambda:no_resources(stale));production_ok();results['stale_recovery']=recovered
    # Small test-only worker timeout. No production setting is changed.
    docker('compose','stop','worker')
    docker('compose','run','-d','--no-deps','--name','campus-timeout-check','-e','BUILD_TIMEOUT_SECONDS=5','worker')
    timed=c.call('POST',f'/api/projects/{pid}/deployments',json={'source_id':slow})['id']
    timeout=c.wait('/api/deployments/'+timed,timeout=100)
    assert timeout['status']=='FAILED' and timeout['exit_code']==124 and 'timeout' in timeout['error'],timeout
    wait_for(lambda:no_resources(timed));production_ok();results['timeout']=timeout
    docker('rm','-f','campus-timeout-check');docker('compose','start','worker')
    # Delete while a build runs. Keep the reference production project and its immutable versions.
    deleting=c.call('POST',f'/api/projects/{pid}/deployments',json={'source_id':slow})['id']
    wait_for(lambda:docker('ps','-q','--filter','label=campus.deployment='+deleting))
    c.call('DELETE','/api/projects/'+pid)
    wait_for(lambda:c.call('GET','/api/cleanup/'+pid)['deleted'])
    assert c.call('GET','/api/deployments/'+deleting)['status']=='CANCELED'
    assert no_resources(deleting)
    assert not docker('ps','-aq','--filter','label=campus.project='+pid)
    assert not docker('volume','ls','-q','--filter','label=campus.project='+pid)
    production_ok();results['delete_running']={'project':pid,'cleanup':c.call('GET','/api/cleanup/'+pid)}
    # API and gateway restart: persisted cookies, records, stdout logs and production remain.
    before=c.call('GET','/api/deployments/'+smoke['react_deployment']+'/logs')
    docker('compose','restart','api','gateway')
    wait_for(lambda:json.loads(docker('inspect','campus-deploy-api-1'))[0]['State']['Health']['Status']=='healthy')
    after=c.call('GET','/api/deployments/'+smoke['react_deployment']+'/logs')
    assert before==after
    production_ok();results['restart_persistence']={'logs_preserved':True,'session_preserved':True,'production_preserved':True}
    (ROOT/'evidence/robustness.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Lifecycle checks passed; evidence/robustness.json',flush=True)


if __name__=='__main__':
    try:
        main()
    finally:
        # Restore normal worker if an assertion fails. Remove only our specifically named test helper.
        found=docker('ps','-aq','--filter','name=^/campus-timeout-check$')
        if found:
            docker('rm','-f',found)
        docker('compose','start','worker')
