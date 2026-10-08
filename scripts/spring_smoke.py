"""Real GitHub -> Vite/Gradle -> Spring/MySQL/Redis deployment smoke.

Uses only Campus Deploy's authenticated public API. Leaves a usable demo project.
Run from the project virtualenv after scripts/start. No upstream repository writes.
"""
import json
import time
import sys
from smoke import Client, ROOT


def main():
    client = Client()
    evidence = ROOT / 'evidence/spring.json'
    if '--retry' in sys.argv:
        result = json.loads(evidence.read_text('utf-8'))
        deployment = client.call('POST', '/api/projects/' + result['project_id'] + '/deployments', json={'source_id': result['source_id']})
        result['deployment_id'] = deployment['id']
        project = {'id': result['project_id'], 'deployment_id': deployment['id']}
    else:
        result, project = create(client)
    evidence.write_text(json.dumps(result, indent=2), 'utf-8')
    deployment = client.wait('/api/deployments/' + project['deployment_id'], timeout=1200)
    if deployment['status'] != 'READY':
        raise AssertionError(deployment.get('error'))
    project = client.call('GET', '/api/projects/' + project['id'])
    url = project['production_url']
    assert client.site(url).status_code == 200
    products = client.site(url, '/api/products', accept='application/json')
    assert products.status_code == 200 and isinstance(products.json(), list)
    result.update(url=url, preset=deployment['preset'], status=deployment['status'],
                  image=deployment['runtime_image'], validation=deployment['validation'])
    evidence.write_text(json.dumps(result, indent=2), 'utf-8')
    print(json.dumps(result, indent=2), flush=True)


def create(client):
    source = client.call('POST', '/api/sources/git', json={'url': 'https://github.com/Gandalem/aurashop.git'})
    source = client.wait('/api/sources/' + source['id'])
    assert source['status'] == 'READY' and source['preset'] == 'SPRING_BOOT', source.get('error')
    project = client.call('POST', '/api/projects', json={
        'name': 'Aura Shop · Spring Boot', 'slug': 'aurashop-' + str(int(time.time())), 'source_id': source['id']})
    result = {'repository': source['locator'], 'source_sha': source['sha'], 'source_id': source['id'],
              'project_id': project['id'], 'deployment_id': project['deployment_id']}
    return result, project


if __name__ == '__main__':
    main()
