"""Scan only Git publish candidates against this installation's actual secrets.

Never prints secret values. Service secrets stay inside the worker container;
candidate files are streamed to a read-only scanner through stdin, not mounted.
"""
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
    names = sorted({name for name in names if name and (ROOT/name).is_file()})
    auth = json.loads((ROOT/'.secrets/auth.json').read_text('utf-8'))
    private = [(ROOT/'.secrets/admin-password.txt').read_bytes().strip(), *[v.encode() for v in auth.values()]]
    local_matches = []
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tf:
        for name in names:
            data = (ROOT/name).read_bytes()
            if any(secret and secret in data for secret in private):
                local_matches.append(name)
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info,io.BytesIO(data))
    code = '''import json,sys,tarfile,pathlib
private=[]
for p in pathlib.Path('/data/services').glob('*.json'):
    values=json.loads(p.read_text())
    private += [values[k].encode() for k in ('mysql','root','redis','jwt') if values.get(k)]
matches=[]
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|*') as tf:
    for member in tf:
        data=tf.extractfile(member).read()
        if any(secret in data for secret in private): matches.append(member.name)
print(json.dumps({'matches':matches,'secret_count':len(private)}))
'''
    result = subprocess.run(['docker','compose','exec','-T','worker','python','-c',code],cwd=ROOT,input=archive.getvalue(),capture_output=True,check=True)
    services = json.loads(result.stdout)
    report = {'candidate_files':len(names),'admin_secret_matches':local_matches,'service_secret_matches':services['matches'],
              'service_secret_count':services['secret_count'],'status':'PASS' if not local_matches and not services['matches'] else 'FAILED'}
    (ROOT/'evidence/publish-scan.json').write_text(json.dumps(report,indent=2),'utf-8')
    print(json.dumps(report,indent=2))
    if report['status'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
