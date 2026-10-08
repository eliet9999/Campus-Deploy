import argparse
import json
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',action='store_true');args=parser.parse_args()
    report={'python':platform.python_version(),'platform':platform.platform(),'checks':[]}
    def check(name,ok,detail):
        report['checks'].append({'name':name,'ok':ok,'detail':detail});print(f'{"PASS" if ok else "CHECK"} {name}: {detail}',flush=True)
    settings=dict(line.split('=',1) for line in (ROOT/'.env').read_text('utf-8-sig').splitlines() if '=' in line and not line.startswith('#')) if (ROOT/'.env').exists() else {}
    origin=settings.get('ADMIN_ORIGIN','http://localhost:3000');domain=settings.get('BASE_DOMAIN','localhost');port=settings.get('SITE_PORT','8080')
    for name,cmd in [('docker',['docker','version']),('compose',['docker','compose','version'])]:
        try:
            result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,encoding='utf-8');check(name,result.returncode==0,result.stdout[:1000] if result.returncode==0 else result.stderr)
        except OSError as e:check(name,False,str(e))
    for p in (3000,int(port or 80)):
        with socket.socket() as s:
            result=s.connect_ex(('127.0.0.1',p))
        check('port '+str(p),True,'listening (expected when started)' if result==0 else 'free (run start)')
    check('secrets', (ROOT/'.secrets/auth.json').is_file(),'secret file present; contents not displayed')
    check('host disk free',shutil.disk_usage(ROOT).free>1024**3,str(shutil.disk_usage(ROOT).free//1024**2)+' MiB')
    try:
        r=httpx.get(origin+'/api/health',trust_env=False,timeout=5);check('management health',r.status_code==200,str(r.json()))
        check('worker lease',r.json().get('worker_alive') is True,'single worker heartbeat')
    except Exception as e:check('management health',False,str(e))
    try:
        resolved=socket.getaddrinfo('p-doctor.'+domain,int(port or 80));check('OS subdomain DNS',True,str(sorted({x[4][0] for x in resolved})))
    except OSError as e:check('OS subdomain DNS',False,f'{e}; Chromium .localhost handling is checked separately')
    if args.browser:
        result=subprocess.run(['node','scripts/browser-doctor.mjs',f'{settings.get("SITE_SCHEME","http")}://p-doctor.{domain}'+(':'+port if port else '')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        check('real browser subdomain',result.returncode==0,result.stdout or result.stderr)
    else:check('real browser subdomain',False,'not run: use --browser after frontend npm ci and npx playwright install chromium')
    result=subprocess.run(['docker','compose','ps','--format','json'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8');report['compose']=result.stdout
    (ROOT/'evidence').mkdir(exist_ok=True)
    (ROOT/'evidence/doctor.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    required={'docker','compose','secrets','management health','worker lease'}
    if args.browser:required.add('real browser subdomain')
    return 0 if all(c['ok'] for c in report['checks'] if c['name'] in required) else 1


if __name__=='__main__':sys.exit(main())
