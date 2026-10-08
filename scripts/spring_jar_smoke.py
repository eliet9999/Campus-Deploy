"""Real Gradle/Maven JAR builds, routing, image rollback and scoped recovery."""
import io
import json
import sys
import time
import zipfile
from urllib.parse import urlsplit

import docker
import httpx

from smoke import Client, ROOT
from node_smoke import compose, eventually


def edited_zip(sample, changes):
    result = io.BytesIO()
    with zipfile.ZipFile(ROOT / 'samples' / (sample + '.zip')) as original, zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as dest:
        for entry in original.infolist():
            rel = entry.filename.split('/', 1)[1]
            dest.writestr(entry.filename, changes.get(rel, original.read(entry)))
        existing = {e.filename.split('/', 1)[1] for e in original.infolist()}
        for rel, value in changes.items():
            if rel not in existing:
                dest.writestr(sample + '/' + rel, value)
    return result.getvalue()


def main():
    c, engine = Client(), docker.from_env(timeout=30)
    stamp = str(int(time.time()))
    report = {'checks': [], 'projects': {}, 'status': 'RUNNING', 'environment': 'Windows Docker Desktop Linux; AWS not tested'}
    evidence = ROOT / 'evidence/spring-jar.json'
    if '--finish-cleanup' in sys.argv:
        # Resume only the failed cleanup assertion, without repeating successful builds.
        # Older platform versions could mark deleted while partial-label images remained.
        from types import SimpleNamespace
        sys.path.insert(0, str(ROOT / 'backend'))
        from campus.runtime import Runtime
        from campus.config import Settings
        report = json.loads(evidence.read_text('utf-8'))
        pid = report['projects']['spring-maven-api']['id']
        assert c.call('GET','/api/cleanup/'+pid)['deleted']
        protected = {
            'containers': {x.id:x.status for x in engine.containers.list(all=True) if x.labels.get('campus.project') != pid},
            'images': {i.id for i in engine.images.list(all=True) if i.labels.get('campus.project') != pid},
            'volumes': {v.name for v in engine.volumes.list() if (v.attrs.get('Labels') or {}).get('campus.project') != pid},
        }
        Runtime(SimpleNamespace(cfg=Settings(),db=None,check=lambda:None)).remove_project_images(pid)
        filters = {'label':['campus.instance=campus-deploy-local','campus.project='+pid]}
        assert not engine.images.list(all=True,filters=filters)
        assert not engine.containers.list(all=True,filters=filters) and not engine.volumes.list(filters=filters)
        assert protected['images'] <= {i.id for i in engine.images.list(all=True)}
        assert protected['volumes'] <= {v.name for v in engine.volumes.list()}
        assert all(engine.containers.get(cid).status == state for cid,state in protected['containers'].items())
        for name in ('spring-gradle-api','spring-gradle-web'):
            assert c.site(report['projects'][name]['url'],'/api/hello','application/json').json()['version'] == 'v1'
        report['checks'] = list(dict.fromkeys([*report['checks'],'scoped project deletion including old partial-label images; all other current containers/images/volumes preserved']))
        report.update(status='PASS',cleanup_fix='Single ownership LABEL + project-scoped intermediate cleanup; successful build/recovery checks reused',
                      protected_resources={k:len(v) for k,v in protected.items()})
        evidence.write_text(json.dumps(report,indent=2),'utf-8')
        print('PASS: corrected cleanup and unrelated resource preservation; earlier build/rollback/recovery results retained',flush=True)
        return
    if '--resume' in sys.argv:
        report = json.loads(evidence.read_text('utf-8'))
    own_projects = {p['id'] for p in report['projects'].values()}
    # Protect every pre-existing project resource, including other Campus projects.
    before = {'containers': {x.id: x.status for x in engine.containers.list(all=True) if x.labels.get('campus.project') not in own_projects},
              'volumes': sorted(v.name for v in engine.volumes.list() if (v.attrs.get('Labels') or {}).get('campus.project') not in own_projects),
              'images': sorted(i.id for i in engine.images.list() if i.labels.get('campus.project') not in own_projects)}
    def record(name):
        report['checks'] = list(dict.fromkeys([*report['checks'], name]))
        evidence.write_text(json.dumps(report, indent=2), 'utf-8')
        print('PASS:', name, flush=True)
    def upload_bytes(data):
        s = c.call('POST', '/api/sources/zip', content=data)
        return c.wait('/api/sources/' + s['id'])
    def logs(dep):
        return c.call('GET', '/api/deployments/' + dep + '/logs?stream=build')
    def deploy_sample(name):
        saved = report['projects'].get(name)
        if saved and saved.get('url'):
            p = c.call('GET', '/api/projects/' + saved['id'])
            d = c.call('GET', '/api/deployments/' + saved['deployment_id'])
            assert d['status'] == 'READY'
            return p, d
        s = c.upload(name)
        assert s['preset'] == 'SPRING_BOOT_JAR' and not s['plan']['frontend_root']
        p = c.call('POST', '/api/projects', json={'name': name, 'slug': name + '-' + stamp, 'source_id': s['id']})
        report['projects'][name] = p
        evidence.write_text(json.dumps(report, indent=2), 'utf-8')
        d = c.wait('/api/deployments/' + p['deployment_id'], timeout=1200)
        assert d['status'] == 'READY', d.get('error')
        p = c.call('GET', '/api/projects/' + p['id'])
        report['projects'][name] = {'id': p['id'], 'deployment_id': d['id'], 'source_id': s['id'], 'url': p['production_url'], 'image': d['runtime_image']}
        assert c.site(p['production_url'], '/api/hello', 'application/json').json()['version'] == 'v1'
        container = engine.containers.get('runtime-' + d['id'])
        attrs = container.attrs
        assert attrs['Config']['User'] == '1000:1000' and attrs['HostConfig']['ReadonlyRootfs']
        assert attrs['HostConfig']['CapDrop'] == ['ALL'] and attrs['HostConfig']['PidsLimit'] == 128
        assert attrs['HostConfig']['Memory'] == 1024**3 and attrs['HostConfig']['NanoCpus'] == 500_000_000
        assert 'no-new-privileges:true' in attrs['HostConfig']['SecurityOpt']
        assert not attrs['HostConfig']['PortBindings'] and not any(m['Type'] == 'bind' for m in attrs['Mounts'])
        assert not engine.volumes.list(filters={'label': ['campus.project=' + p['id']]})
        assert not any('DATASOURCE' in x or 'REDIS' in x for x in attrs['Config']['Env'])
        assert 'SERVER_PORT=8080' in attrs['Config']['Env']
        record(name + ': real Wrapper build, executable JAR image, HTTP API, restricted runtime, no services')
        return p, d
    for sample, message in [('spring-no-wrapper', 'Wrapper'), ('spring-multimodule', '다중')]:
        s = upload_bytes((ROOT / 'samples' / (sample + '.zip')).read_bytes())
        assert s['status'] == 'FAILED' and message in s['error']
    record('missing wrapper and multi-module rejected before build')
    gradle, v1 = deploy_sample('spring-gradle-api')
    assert c.site(gradle['production_url']).status_code == 404 and v1['validation']['http_checks'][0]['status'] == 404
    assert v1['validation']['http_checks'][0]['policy'] == 'http-reachable'
    record('API-only root 404 is explicitly HTTP-reachable; actual /api/hello 200 verified separately')
    maven, md = deploy_sample('spring-maven-api')
    web, wd = deploy_sample('spring-gradle-web')
    assert 'JAR 안에서' in c.site(web['production_url']).text
    assert 'javascript' in c.site(web['production_url'], '/app.js').headers['content-type']
    response = httpx.post('http://127.0.0.1:8080/api/echo', headers={'Host': urlsplit(gradle['production_url']).netloc}, json={'message':'real POST'}, trust_env=False)
    assert response.json() == {'message':'real POST'}
    record('JAR embedded page/JS and HTTP POST served through existing gateway')
    pid = gradle['id']
    def deploy_bytes(data, expected='READY'):
        s = upload_bytes(data)
        assert s['status'] == 'READY', s.get('error')
        d = c.call('POST', '/api/projects/' + pid + '/deployments', json={'source_id': s['id']})
        d = c.wait('/api/deployments/' + d['id'], timeout=1200)
        assert d['status'] == expected, d.get('error')
        return d
    v2 = deploy_bytes(edited_zip('spring-gradle-api', {'src/main/resources/application.properties':'demo.version=v2\n',
                 'campus-deploy.yaml':'version: 1\nruntime: spring-boot\nhealth: {path: /api/hello}\n'}))
    assert c.call('GET', '/api/projects/' + pid)['production_id'] == v1['id']
    assert c.site(v2['preview_url'], '/api/hello', 'application/json').json()['version'] == 'v2'
    assert c.site(gradle['production_url'], '/api/hello', 'application/json').json()['version'] == 'v1'
    old_build, old_image = logs(v1['id']), v1['runtime_image']
    def switch(d, kind):
        op = c.call('POST', '/api/projects/' + pid + '/production', json={'deployment_id':d['id'], 'kind':kind})
        result = c.wait('/api/operations/' + op['operation_id'], timeout=240, terminal=('DONE','FAILED'))
        assert result['status'] == 'DONE', result.get('error')
    switch(v2, 'promote')
    assert c.site(gradle['production_url'], '/api/hello', 'application/json').json()['version'] == 'v2'
    switch(v1, 'rollback')
    assert logs(v1['id']) == old_build and engine.containers.get('runtime-' + v1['id']).image.id == old_image
    assert c.site(gradle['production_url'], '/api/hello', 'application/json').json()['version'] == 'v1'
    record('v1 production/v2 preview, strict custom health, promote, same-image rollback; no wrapper/Git/build rerun')
    failed = deploy_bytes(edited_zip('spring-gradle-api', {'src/main/resources/application.properties':'demo.fail=true\n'}), 'FAILED')
    assert 'runtime 종료' in failed['error']
    assert c.call('GET', '/api/projects/' + pid)['production_id'] == v1['id']
    assert c.site(gradle['production_url'], '/api/hello', 'application/json').status_code == 200
    record('real Java startup exit classified and production preserved')
    ambiguous = deploy_bytes((ROOT / 'samples/spring-ambiguous-jars.zip').read_bytes(), 'FAILED')
    assert 'JAR 2개' in ambiguous['error'] and 'artifact' in ambiguous['error']
    resolved = deploy_bytes(edited_zip('spring-ambiguous-jars', {'campus-deploy.yaml': 'version: 1\nruntime: spring-boot\nartifact: build/libs/campus-api-1.0.0.jar\n'}))
    assert resolved['validation']['artifact'] == 'build/libs/campus-api-1.0.0.jar'
    record('two executable JARs fail explicitly; manifest artifact resolves ambiguity')
    # These projects have no data services; aurashop lifecycle separately verifies all services/data.
    compose('stop')
    for project, dep in ((gradle, v1), (maven, md), (web, wd)):
        container = engine.containers.get('runtime-' + dep['id'])
        assert container.labels['campus.project'] == project['id'] and container.labels['campus.instance'] == 'campus-deploy-local'
        container.remove(force=True, v=True)
    compose('up', '-d', '--no-build', '--wait')
    for project, dep in ((gradle, v1), (maven, md), (web, wd)):
        eventually(lambda: engine.containers.get('runtime-' + dep['id']).attrs['State']['Running'] and
                   c.call('GET', '/api/projects/' + project['id'])['production_health'] == 'RUNNING' and
                   c.site(project['production_url'], '/api/hello', 'application/json').status_code == 200, timeout=240)
        assert engine.containers.get('runtime-' + dep['id']).image.id == dep['runtime_image']
    assert logs(v1['id']) == old_build
    record('Compose restart + missing Gradle/Maven/web runtimes recovered from saved images without rebuild')
    # Delete one disposable JAR project; leave Gradle API and web for browser/demo use.
    c.call('DELETE', '/api/projects/' + maven['id'])
    eventually(lambda: c.call('GET', '/api/cleanup/' + maven['id'])['deleted'], timeout=120)
    labels = {'label':['campus.instance=campus-deploy-local', 'campus.project=' + maven['id']]}
    eventually(lambda: not engine.containers.list(all=True, filters=labels) and not engine.images.list(filters=labels)
               and not engine.volumes.list(filters=labels), timeout=30)
    for cid, state in before['containers'].items():
        container = engine.containers.get(cid)
        assert container.status == state
    assert set(before['volumes']) <= {v.name for v in engine.volumes.list()}
    assert set(before['images']) <= {i.id for i in engine.images.list()}
    record('scoped project deletion; all pre-existing containers/images/volumes preserved')
    report['status'] = 'PASS'
    evidence.write_text(json.dumps(report, indent=2), 'utf-8')


if __name__ == '__main__':
    main()
