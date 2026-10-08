import argparse
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args,cwd=ROOT,check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','stop'])
    args=parser.parse_args()
    if args.action=='start':
        if not (ROOT/'.secrets/auth.json').is_file() or not (ROOT/'.env').is_file():
            raise SystemExit('Run scripts/setup.ps1 or bash scripts/setup.sh first.')
        settings=dict(line.split('=',1) for line in (ROOT/'.env').read_text('utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
        run('docker','version')
        run('docker','pull',settings.get('BUILD_IMAGE','node:24.11.1-bookworm-slim'))
        for key, default in [('JAVA_BUILD_IMAGE','gradle:9.4.1-jdk21'), ('JAVA_RUNTIME_IMAGE','eclipse-temurin:21.0.10_7-jre-jammy'),
                             ('MYSQL_IMAGE','mysql:8.4.8'), ('REDIS_IMAGE','redis:7.4.8-alpine')]:
            run('docker','pull',settings.get(key,default))
        run('docker','compose','build','api')
        run('docker','compose','up','-d','--no-build','--wait')
        print('Management: '+settings.get('ADMIN_ORIGIN','http://localhost:3000'))
        print('Password: .secrets/admin-password.txt (preserved; not printed)')
    else:
        run('docker','compose','stop')
        settings=dict(line.split('=',1) for line in (ROOT/'.env').read_text('utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
        instance=settings.get('INSTANCE_ID','campus-deploy-local')
        for kind in ('runtime','mysql','redis'):
            result=subprocess.run(['docker','ps','-q','--filter','label=campus.instance='+instance,'--filter','label=campus.kind='+kind],cwd=ROOT,check=True,text=True,capture_output=True)
            owned=result.stdout.split()
            if owned:
                run('docker','stop',*owned)
        print('Stopped. Persistent volumes and secrets preserved. No data was deleted.')
