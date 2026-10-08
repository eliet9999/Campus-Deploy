import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .auth import Auth, COOKIE, SESSION_SECONDS
from .config import Settings
from .db import DB, uid, enqueue, new_deployment
from .safety import Rejected, github_url


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class GitSource(BaseModel):
    url: str = Field(max_length=300)


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(pattern=r'^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$')
    source_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class DeployInput(BaseModel):
    source_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class SwitchInput(BaseModel):
    deployment_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    kind: str = Field(pattern=r'^(promote|rollback)$')


def create_app(settings=None):
    cfg = settings or Settings()
    db = DB(cfg)
    db.initialize()
    auth = Auth(cfg, db)
    app = FastAPI(title='Campus Deploy', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.cfg = db, cfg

    @app.middleware('http')
    async def security(request, call_next):
        if request.headers.get('host', '').lower() != urlsplit(cfg.origin).netloc.lower():
            return JSONResponse({'detail': 'Unknown management host'}, status_code=404)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('origin') != cfg.origin:
            return JSONResponse({'detail': '정확한 관리 Origin이 필요합니다.'}, status_code=403)
        if request.url.path.startswith('/api/') and request.url.path not in ('/api/login', '/api/health'):
            session = auth.session(request.cookies.get(COOKIE))
            if not session:
                return JSONResponse({'detail': '로그인이 필요합니다.'}, status_code=401)
            request.state.session = session
            if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf']):
                return JSONResponse({'detail': 'CSRF 토큰이 올바르지 않습니다.'}, status_code=403)
        if request.url.path != '/api/sources/zip' and int(request.headers.get('content-length', '0') or 0) > 16384:
            return JSONResponse({'detail': '요청 크기 초과'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(Rejected)
    async def rejected(_, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    def request_key(request):
        key = request.headers.get('idempotency-key', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', key):
            raise HTTPException(400, 'Idempotency-Key가 필요합니다 (16–100자).')
        return key

    def cached(con, key, operation):
        old = con.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
        if old:
            if old['operation'] != operation:
                raise HTTPException(409, '이미 다른 요청에 사용된 식별자입니다.')
            return json.loads(old['response'])

    def save(con, key, operation, response):
        con.execute('INSERT INTO requests VALUES(?,?,?)', (key, operation, json.dumps(response)))
        return response

    def source_ready(con, sid):
        row = con.execute("SELECT * FROM sources WHERE id=? AND status='READY'", (sid,)).fetchone()
        if not row or not (cfg.data / 'sources' / sid).is_dir():
            raise HTTPException(409, '소스 검사가 성공한 후 배포할 수 있습니다.')
        return row

    def project_active(con, pid):
        row = con.execute('SELECT * FROM projects WHERE id=? AND deleting=0 AND deleted=0', (pid,)).fetchone()
        if not row:
            raise HTTPException(404, '프로젝트가 없거나 삭제 중입니다.')
        return row

    def present_deployment(row):
        if not row:
            raise HTTPException(404, '배포를 찾을 수 없습니다.')
        for key in ('settings', 'stages', 'validation'):
            row[key] = json.loads(row[key]) if row[key] else None
        row['preview_url'] = cfg.site_url('d-' + row['id'])
        row['deployment_status'] = row['status']
        if row['status'] == 'READY' and row['preset'] == 'NODE_SERVER' and row['runtime_state'] == 'UNAVAILABLE':
            row['status'] = 'UNAVAILABLE'
        return row

    def present_project(row):
        row['production_url'] = cfg.site_url('p-' + row['slug'])
        prod = db.one('SELECT status,preset,runtime_state,runtime_error FROM deployments WHERE id=?', (row['production_id'],)) if row['production_id'] else None
        row['production_health'] = (prod['runtime_state'] if prod['preset'] == 'NODE_SERVER' else prod['status']) if prod else None
        row['production_error'] = prod['runtime_error'] if prod else None
        return row

    @app.get('/api/health')
    def health():
        lease = db.one('SELECT expires FROM worker_lease WHERE slot=1')
        return {'ok': True, 'worker_alive': bool(lease and lease['expires'] > time.time())}

    @app.post('/api/login')
    def login(body: Login, request: Request):
        if not auth.rate_allowed(request.client.host):
            raise HTTPException(429, '로그인 시도가 너무 많습니다. 5분 후 다시 시도하세요.')
        result = auth.login(body.password)
        if not result:
            raise HTTPException(401, '암호가 올바르지 않습니다.')
        cookie, csrf = result
        response = JSONResponse({'csrf': csrf})
        response.set_cookie(COOKIE, cookie, httponly=True, samesite='strict', secure=cfg.origin.startswith('https://'), max_age=SESSION_SECONDS, path='/')
        return response

    @app.get('/api/session')
    def session(request: Request):
        return {'csrf': request.state.session['csrf'], 'origin': cfg.origin}

    @app.post('/api/logout')
    def logout(request: Request):
        with db.tx() as con:
            con.execute('DELETE FROM sessions WHERE id=?', (request.state.session['id'],))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE)
        return response

    @app.post('/api/sources/zip', status_code=202)
    async def upload(request: Request):
        key = request_key(request)
        sid = uid()
        path = cfg.data / 'uploads' / f'{sid}.zip'
        total, sha = 0, hashlib.sha256()
        try:
            with path.open('xb') as f:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > cfg.upload_limit:
                        raise HTTPException(413, '업로드 50MiB(설정값) 제한 초과')
                    sha.update(chunk)
                    f.write(chunk)
            op = 'zip:' + sha.hexdigest()
            with db.tx() as con:
                old = cached(con, key, op)
                if old:
                    path.unlink(missing_ok=True)
                    return old
                locator = request.headers.get('x-filename', 'upload.zip')[:160]
                con.execute('INSERT INTO sources(id,kind,locator,status,sha,created,request_key) VALUES(?,?,?,?,?,?,?)',
                            (sid, 'ZIP', locator, 'QUEUED', sha.hexdigest(), time.time(), key))
                enqueue(con, 'INSPECT', sid)
                return save(con, key, op, {'id': sid, 'status': 'QUEUED'})
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @app.post('/api/sources/git', status_code=202)
    def git_source(body: GitSource, request: Request):
        key, locator = request_key(request), github_url(body.url)
        op = 'git:' + locator
        with db.tx() as con:
            old = cached(con, key, op)
            if old:
                return old
            sid = uid()
            con.execute('INSERT INTO sources(id,kind,locator,status,created,request_key) VALUES(?,?,?,?,?,?)', (sid, 'GIT', locator, 'QUEUED', time.time(), key))
            enqueue(con, 'INSPECT', sid)
            return save(con, key, op, {'id': sid, 'status': 'QUEUED'})

    @app.get('/api/sources/{sid}')
    def get_source(sid: str):
        row = db.one('SELECT * FROM sources WHERE id=?', (sid,))
        if not row:
            raise HTTPException(404)
        return row

    @app.get('/api/projects')
    def projects():
        rows = db.all('''SELECT p.*, (SELECT CASE WHEN status='READY' AND preset='NODE_SERVER' AND runtime_state='UNAVAILABLE' THEN 'UNAVAILABLE' ELSE status END FROM deployments d WHERE d.project_id=p.id ORDER BY created DESC LIMIT 1) latest_status,
          (SELECT created FROM deployments d WHERE d.project_id=p.id ORDER BY created DESC LIMIT 1) latest_at
          FROM projects p WHERE deleted=0 ORDER BY created DESC''')
        return [present_project(row) for row in rows]

    @app.post('/api/projects', status_code=202)
    def create_project(body: ProjectInput, request: Request):
        key, op = request_key(request), 'project:' + body.model_dump_json()
        with db.tx() as con:
            old = cached(con, key, op)
            if old:
                return old
            source = source_ready(con, body.source_id)
            if con.execute('SELECT id FROM projects WHERE slug=?', (body.slug,)).fetchone():
                raise HTTPException(409, '이미 사용한 slug입니다. 다른 주소를 선택하세요.')
            pid = uid()
            con.execute('INSERT INTO projects(id,slug,name,source_id,preset,created) VALUES(?,?,?,?,?,?)', (pid, body.slug, body.name, body.source_id, source['preset'], time.time()))
            dep = new_deployment(con, pid, source, cfg)
            return save(con, key, op, {'id': pid, 'deployment_id': dep})

    @app.get('/api/projects/{pid}')
    def project(pid: str):
        row = db.one('SELECT * FROM projects WHERE id=? AND deleted=0', (pid,))
        if not row:
            raise HTTPException(404)
        row = present_project(row)
        row['source'] = db.one('SELECT * FROM sources WHERE id=?', (row['source_id'],))
        row['deployments'] = [present_deployment(d) for d in db.all('SELECT * FROM deployments WHERE project_id=? ORDER BY created DESC', (pid,))]
        row['transitions'] = db.all('SELECT * FROM transitions WHERE project_id=? ORDER BY id DESC', (pid,))
        row['operations'] = db.all('SELECT * FROM operations WHERE project_id=? ORDER BY created DESC LIMIT 20', (pid,))
        return row

    @app.post('/api/projects/{pid}/deployments', status_code=202)
    def redeploy(pid: str, body: DeployInput, request: Request):
        key, op = request_key(request), f'deploy:{pid}:{body.source_id}'
        with db.tx() as con:
            old = cached(con, key, op)
            if old:
                return old
            project_active(con, pid)
            source = source_ready(con, body.source_id)
            dep = new_deployment(con, pid, source, cfg)
            con.execute('UPDATE projects SET source_id=?,preset=? WHERE id=?', (source['id'], source['preset'], pid))
            return save(con, key, op, {'id': dep})

    @app.get('/api/deployments/{dep}')
    def deployment(dep: str):
        return present_deployment(db.one('SELECT * FROM deployments WHERE id=?', (dep,)))

    @app.get('/api/deployments/{dep}/logs')
    def logs(dep: str, cursor: int = 0, stream: str | None = None):
        if stream not in (None, 'build', 'runtime'):
            raise HTTPException(400, 'stream은 build 또는 runtime입니다.')
        rows = db.all('SELECT id,created,text,stream FROM logs WHERE deployment_id=? AND id>? AND (? IS NULL OR stream=?) ORDER BY id LIMIT 300', (dep, cursor, stream, stream))
        return {'items': rows, 'cursor': rows[-1]['id'] if rows else cursor}

    @app.get('/api/operations/{operation}')
    def operation_status(operation: str):
        row = db.one('SELECT * FROM operations WHERE id=?', (operation,))
        if not row:
            raise HTTPException(404)
        return row

    @app.post('/api/deployments/{dep}/cancel')
    def cancel(dep: str, request: Request):
        key, op = request_key(request), 'cancel:' + dep
        with db.tx() as con:
            old = cached(con, key, op)
            if old:
                return old
            row = con.execute('SELECT * FROM deployments WHERE id=?', (dep,)).fetchone()
            if not row or row['status'] in ('READY', 'FAILED', 'CANCELED'):
                raise HTTPException(409, '진행 중인 배포만 취소할 수 있습니다.')
            con.execute('UPDATE deployments SET cancel=1 WHERE id=?', (dep,))
            return save(con, key, op, {'ok': True, 'message': '취소 요청됨. 워커 정리 결과를 기다려 주세요.'})

    @app.post('/api/projects/{pid}/production')
    def switch(pid: str, body: SwitchInput, request: Request):
        key, op = request_key(request), f'switch:{pid}:' + body.model_dump_json()
        with db.tx() as con:
            old = cached(con, key, op)
            if old:
                return old
            project = project_active(con, pid)
            dep = con.execute("SELECT * FROM deployments WHERE id=? AND project_id=? AND status='READY'", (body.deployment_id, pid)).fetchone()
            if not dep:
                raise HTTPException(409, '정상 산출물이 있는 READY 배포만 운영할 수 있습니다.')
            if body.kind == 'rollback' and not con.execute('SELECT 1 FROM transitions WHERE project_id=? AND deployment_id=?', (pid, body.deployment_id)).fetchone():
                raise HTTPException(409, '과거 운영에 사용된 배포만 롤백할 수 있습니다.')
            if dep['preset'] == 'NODE_SERVER':
                if not dep['runtime_image']:
                    raise HTTPException(409, '보존된 runtime image가 없습니다.')
                if con.execute("SELECT 1 FROM operations WHERE project_id=? AND status IN ('QUEUED','HEALTH_CHECK')", (pid,)).fetchone():
                    raise HTTPException(409, '진행 중인 운영 전환을 기다려 주세요.')
                operation = uid()
                con.execute("INSERT INTO operations(id,project_id,deployment_id,previous_id,kind,status,created) VALUES(?,?,?,?,?,'QUEUED',?)",
                    (operation, pid, dep['id'], project['production_id'], body.kind, time.time()))
                enqueue(con, 'SWITCH', operation)
                return save(con, key, op, {'ok': True, 'operation_id': operation, 'status': 'QUEUED', 'production_id': project['production_id']})
            if not (cfg.data / 'artifacts' / body.deployment_id / 'index.html').is_file():
                raise HTTPException(409, '정상 정적 산출물이 없습니다.')
            if project['production_id'] != body.deployment_id:
                con.execute('UPDATE projects SET production_id=? WHERE id=?', (body.deployment_id, pid))
                con.execute('INSERT INTO transitions(project_id,previous_id,deployment_id,kind,created) VALUES(?,?,?,?,?)', (pid, project['production_id'], body.deployment_id, body.kind, time.time()))
            return save(con, key, op, {'ok': True, 'production_id': body.deployment_id})

    @app.delete('/api/projects/{pid}', status_code=202)
    def delete(pid: str, request: Request):
        key, op = request_key(request), 'delete:' + pid
        with db.tx() as con:
            old = cached(con, key, op)
            if old:
                return old
            project_active(con, pid)
            con.execute("UPDATE projects SET deleting=1,production_id=NULL,cleanup='정리 대기' WHERE id=?", (pid,))
            con.execute("UPDATE deployments SET cancel=1 WHERE project_id=? AND status NOT IN ('READY','FAILED','CANCELED')", (pid,))
            enqueue(con, 'DELETE', pid)
            return save(con, key, op, {'ok': True, 'id': pid})

    @app.get('/api/cleanup/{pid}')
    def cleanup(pid: str):
        return db.one('SELECT id,deleting,deleted,cleanup FROM projects WHERE id=?', (pid,))

    @app.get('/{path:path}')
    def ui(path: str):
        if path == 'api' or path.startswith('api/') or any(p.startswith('.') for p in Path(path).parts):
            raise HTTPException(404)
        candidate = (cfg.ui / path).resolve()
        if candidate.is_relative_to(cfg.ui) and candidate.is_file():
            return FileResponse(candidate)
        if not Path(path).suffix and (cfg.ui / 'index.html').exists():
            return FileResponse(cfg.ui / 'index.html')
        raise HTTPException(404, '관리 UI 빌드가 필요합니다.')

    return app
