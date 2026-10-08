"""Read-only final audit of this installation's mounts, persistence and cleaned test resources."""
import json
import subprocess
from smoke import Client, ROOT


def run(*args):
    return subprocess.check_output(['docker',*args],cwd=ROOT,text=True,encoding='utf-8').strip()


if __name__=='__main__':
    result={}
    for service in ('api','gateway','worker'):
        row=json.loads(run('inspect','campus-deploy-'+service+'-1'))[0]
        mounts=[{'destination':m['Destination'],'read_write':m['RW']} for m in row['Mounts']]
        sockets=[m for m in mounts if 'docker.sock' in m['destination']]
        assert bool(sockets)==(service=='worker')
        if service=='gateway':
            assert all(not m['read_write'] for m in mounts)
        if service=='api':
            assert row['HostConfig']['PortBindings']['3000/tcp'][0]['HostIp']=='127.0.0.1'
        result[service]={'mounts':mounts,'ports':row['HostConfig']['PortBindings'],'state':row['State']['Status']}
    assert not run('ps','-aq','--filter','label=campus.instance=campus-deploy-local','--filter','label=campus.kind=build')
    assert not run('volume','ls','-q','--filter','label=campus.instance=campus-deploy-local','--filter','label=campus.kind=build-work')
    result['orphan_build_containers']=0;result['orphan_work_volumes']=0
    code="""import json,sqlite3,pathlib
c=sqlite3.connect('file:/data/metadata/campus.sqlite3?mode=ro',uri=True)
assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
rows=c.execute('SELECT d.id,p.deleted FROM deployments d JOIN projects p ON p.id=d.project_id').fetchall()
assert all(not (pathlib.Path('/data/artifacts')/dep).exists() for dep,deleted in rows if deleted)
assert not list(pathlib.Path('/data/artifacts/.staging').iterdir())
assert not list(pathlib.Path('/data/work').iterdir())
print(json.dumps({'sqlite_integrity':'ok','deleted_artifacts_absent':True,'staging_empty':True,'work_empty':True,'deployment_count':len(rows)}))
"""
    result['storage']=json.loads(run('compose','exec','-T','worker','python','-c',code))
    c=Client();smoke=json.loads((ROOT/'evidence/smoke.json').read_text())
    assert 'VERSION 01' in c.site(smoke['production_url']).text
    assert c.call('GET','/api/deployments/'+smoke['react_deployment']+'/logs')['items']
    result['post_stop_start_production_and_logs']=True
    runtimes=[]
    runtime_ids=run('ps','-aq','--filter','label=campus.instance=campus-deploy-local','--filter','label=campus.kind=runtime').split()
    for container_id in runtime_ids:
        row=json.loads(run('inspect',container_id))[0]
        assert not row['HostConfig']['PortBindings']
        assert row['Config']['User']=='1000:1000' and row['HostConfig']['ReadonlyRootfs']
        assert row['HostConfig']['Memory']==512*1024**2 and row['HostConfig']['NanoCpus']==500_000_000
        assert row['HostConfig']['PidsLimit']==128 and row['HostConfig']['CapDrop']==['ALL']
        assert 'no-new-privileges:true' in row['HostConfig']['SecurityOpt']
        assert not any(m['Type']=='bind' for m in row['Mounts'])
        networks=list(row['NetworkSettings']['Networks'])
        assert networks==['campus-deploy-local-runtime']
        runtimes.append({'deployment':row['Config']['Labels']['campus.deployment'],'image':row['Image'],'state':row['State']['Status'],'published_ports':False})
    result['node_runtimes']=runtimes
    network=json.loads(run('network','inspect','campus-deploy-local-runtime'))[0]
    assert network['Internal']
    for service in ('api','worker'):
        row=json.loads(run('inspect','campus-deploy-'+service+'-1'))[0]
        assert 'campus-deploy-local-runtime' not in row['NetworkSettings']['Networks']
    result['runtime_network_internal']=True
    node=json.loads((ROOT/'evidence/node-smoke.json').read_text(encoding='utf-8'))
    assert c.site(node['production_url'],'/version').json()['version']=='v1'
    result['node_production_after_regressions']=True
    (ROOT/'evidence/final-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
