import mimetypes
import re
import httpx
from pathlib import PurePosixPath
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from .config import Settings
from .db import DB
from .safety import blocked, safe_name, Rejected

HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}


def proxy_headers(headers):
    nominated = {v.strip().lower() for v in headers.get('connection', '').split(',')}
    items = headers.multi_items() if hasattr(headers, 'multi_items') else headers.items()
    return [(k, v) for k, v in items if k.lower() not in HOP_HEADERS | nominated]


async def proxy_runtime(row, request):
    dep = row['id']
    if not re.fullmatch('[a-f0-9]{32}', dep):
        raise HTTPException(404)
    if row['runtime_state'] not in ('RUNNING', 'HEALTH_CHECK'):
        raise HTTPException(503, '웹 서버가 실행 중이 아닙니다. 운영 상태를 확인하세요.')
    if request.headers.get('upgrade', '').lower() == 'websocket':
        raise HTTPException(426, 'P1은 일반 HTTP만 지원합니다. WebSocket은 지원하지 않습니다.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2 * 1024**2:
            raise HTTPException(413, 'HTTP request body limit: 2MiB')
    headers = [(k, v) for k, v in proxy_headers(request.headers)
               if k.lower() not in ('forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto')]
    headers.extend([('x-forwarded-proto', request.url.scheme), ('x-forwarded-host', request.headers['host'])])
    # Construct the authority exclusively from the validated ID, never urljoin(user_path).
    target = httpx.URL(f'http://runtime-{dep}:8080').copy_with(
        raw_path=request.scope.get('raw_path', b'/') + (b'?' + request.scope['query_string'] if request.scope['query_string'] else b''))
    client = httpx.AsyncClient(timeout=httpx.Timeout(15, connect=3), trust_env=False, follow_redirects=False)
    try:
        response = await client.send(client.build_request(request.method, target, headers=headers, content=bytes(body)), stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(502, 'runtime 연결에 실패했습니다.')
    async def content():
        total = 0
        try:
            async for chunk in response.aiter_raw():
                total += len(chunk)
                if total > 50 * 1024**2:
                    break
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()
    result = StreamingResponse(content(), status_code=response.status_code)
    result.raw_headers = [(k.encode('latin-1'), v.encode('latin-1')) for k, v in proxy_headers(response.headers)]
    result.headers['X-Content-Type-Options'] = 'nosniff'
    result.headers['Cache-Control'] = 'no-store'
    return result

mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('application/javascript', '.mjs')
mimetypes.add_type('text/css', '.css')


def create_app(settings=None):
    cfg = settings or Settings()
    db = DB(cfg)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def serve(path: str, request: Request):
        host = request.headers.get('host', '').lower()
        expected_port = ':' + cfg.port if cfg.port else ''
        if expected_port and host.endswith(expected_port):
            host = host[:-len(expected_port)]
        if ':' in host or not host.endswith('.' + cfg.domain):
            raise HTTPException(404)
        label = host[:-(len(cfg.domain) + 1)]
        if re.fullmatch(r'd-[a-f0-9]{32}', label):
            row = db.one("SELECT d.* FROM deployments d JOIN projects p ON p.id=d.project_id WHERE d.id=? AND d.status IN ('PUBLISHING','HEALTH_CHECK','READY') AND p.deleting=0 AND p.deleted=0", (label[2:],), readonly=True)
        elif re.fullmatch(r'p-[a-z0-9][a-z0-9-]{0,39}', label):
            row = db.one("SELECT d.* FROM projects p JOIN deployments d ON p.production_id=d.id WHERE p.slug=? AND p.deleting=0 AND p.deleted=0 AND d.status='READY'", (label[2:],), readonly=True)
        else:
            row = None
        if not row:
            raise HTTPException(404)
        if row['preset'] == 'NODE_SERVER':
            return await proxy_runtime(row, request)
        if request.method not in ('GET', 'HEAD'):
            raise HTTPException(405)
        if not row['artifact']:
            raise HTTPException(404)
        # The database stores an ID, never an arbitrary disk path.
        if row['artifact'] != row['id']:
            raise HTTPException(404)
        if path == 'api' or path.startswith('api/'):
            raise HTTPException(404)
        try:
            rel = safe_name(path.rstrip('/') or 'index.html')
        except Rejected:
            raise HTTPException(404)
        if blocked(str(rel)):
            raise HTTPException(404)
        root = (cfg.data / 'artifacts' / row['id']).resolve()
        file = root.joinpath(*rel.parts).resolve()
        if not file.is_relative_to(root):
            raise HTTPException(404)
        if file.is_dir() and (file / 'index.html').is_file():
            file /= 'index.html'
        if not file.is_file():
            accepts_html = 'text/html' in request.headers.get('accept', '')
            if row['preset'] == 'VITE_STATIC' and not PurePosixPath(path).suffix and accepts_html:
                file = root / 'index.html'
            else:
                raise HTTPException(404)
        return FileResponse(file, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})

    return app
