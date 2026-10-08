"""Exercise explicit manifest services with a generic, unmodified JAR application."""
import json
import time
from urllib.parse import urlsplit

import docker
import httpx
from smoke import Client, ROOT
from node_smoke import compose, eventually
from spring_jar_smoke import edited_zip


def main():
    c, engine = Client(), docker.from_env(timeout=30)
    report = {'checks': [], 'status': 'RUNNING'}
    evidence = ROOT / 'evidence/spring-jar-services.json'
    def check(name):
        report['checks'].append(name)
        evidence.write_text(json.dumps(report, indent=2), 'utf-8')
        print('PASS:', name, flush=True)
    source = c.upload('spring-gradle-services')
    assert source['preset'] == 'SPRING_BOOT_JAR' and source['plan']['adapter'] == 'none'
    p = c.call('POST', '/api/projects', json={'name':'Generic JAR services', 'slug':'jar-services-'+str(int(time.time())), 'source_id':source['id']})
    pid = p['id']
    report['project_id'] = pid
    d = c.wait('/api/deployments/' + p['deployment_id'], timeout=1200)
    assert d['status'] == 'READY', d.get('error')
    project = c.call('GET', '/api/projects/' + pid)
    url = project['production_url']
    app = httpx.Client(base_url='http://127.0.0.1:8080', headers={'Host':urlsplit(url).netloc}, trust_env=False, timeout=30)
    assert app.get('/api/health').json() == {'mysql':1, 'redis':'PONG'}
    value = 'persistent-' + str(int(time.time()))
    expected = dict.fromkeys(('mysql','redis','storage'),value)
    assert app.post('/api/value', json={'value':value}).json() == expected
    runtime = engine.containers.get('runtime-' + d['id'])
    assert any(m['Destination'] == '/app/state' and m['Type'] == 'volume' for m in runtime.attrs['Mounts'])
    assert d['settings']['adaptations'] == []
    check('manifest-only MySQL/Redis/storage /app/state; real reads/writes; generic adapter none')
    original_build = c.call('GET', '/api/deployments/' + d['id'] + '/logs?stream=build')
    failed_source = c.call('POST', '/api/sources/zip', content=edited_zip('spring-gradle-services', {'src/main/resources/application.properties':'demo.fail=true\n'}))
    failed_source = c.wait('/api/sources/' + failed_source['id'])
    assert failed_source['status'] == 'READY'
    failed = c.call('POST','/api/projects/'+pid+'/deployments',json={'source_id':failed_source['id']})
    failed = c.wait('/api/deployments/'+failed['id'],timeout=1200)
    assert failed['status'] == 'FAILED' and 'runtime 종료' in failed['error']
    assert c.call('GET','/api/projects/'+pid)['production_id'] == d['id']
    assert app.get('/api/value').json() == expected
    check('generic JAR failed Preview preserves existing production and all three data stores')
    compose('stop')
    for name in ('runtime-'+d['id'], 'campus-deploy-local-'+pid+'-mysql', 'campus-deploy-local-'+pid+'-redis'):
        container = engine.containers.get(name)
        assert container.labels['campus.project'] == pid and container.labels['campus.instance'] == 'campus-deploy-local'
        container.remove(force=True, v=True)
    compose('up','-d','--no-build','--wait')
    eventually(lambda: engine.containers.get('runtime-'+d['id']).attrs['State']['Running'] and
               c.call('GET','/api/projects/'+pid)['production_health'] == 'RUNNING' and app.get('/api/health').status_code == 200,timeout=240)
    assert app.get('/api/value').json() == expected
    assert engine.containers.get('runtime-'+d['id']).image.id == d['runtime_image']
    assert c.call('GET','/api/deployments/'+d['id']+'/logs?stream=build') == original_build
    check('generic JAR and selected services recover after Compose restart and missing containers; image/data/logs preserved')
    # Delete only this script's fixture, then check two pre-existing demos remain available.
    other = json.loads((ROOT/'evidence/spring-jar.json').read_text())['projects']
    before = {name: c.site(p['url'],'/api/hello','application/json').json() for name,p in other.items() if name != 'spring-maven-api'}
    c.call('DELETE','/api/projects/'+pid)
    eventually(lambda: c.call('GET','/api/cleanup/'+pid)['deleted'], timeout=120)
    filters = {'label':['campus.instance=campus-deploy-local','campus.project='+pid]}
    eventually(lambda: not engine.containers.list(all=True,filters=filters) and not engine.volumes.list(filters=filters)
               and not engine.networks.list(filters=filters) and not engine.images.list(filters=filters), timeout=30)
    for name, response in before.items():
        assert c.site(other[name]['url'],'/api/hello','application/json').json() == response
    check('generic JAR project-owned DB/Redis/storage/network/images deleted; other demo responses unchanged')
    report.update(status='PASS',deployment_id=d['id'],image=d['runtime_image'],source_sha=d['sha'])
    evidence.write_text(json.dumps(report,indent=2),'utf-8')


if __name__ == '__main__':
    main()
