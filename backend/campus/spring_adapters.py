"""Existing aurashop-v1 compatibility only. Generic JAR sources are never rewritten."""
from .safety import Rejected


def aurashop_v1(backend, frontend):
    changes = []
    api = frontend / 'src/api.js'
    text = api.read_text('utf-8')
    marker = 'api.interceptors.request.use('
    if marker not in text or 'import.meta.env.VITE_API_BASE_URL' not in text:
        raise Rejected('aurashop API 소스가 호환 레시피와 다릅니다. 어댑터 갱신이 필요합니다.')
    # Some original screens use /api/products, others use /auth/login.
    # Both resolve once under the same-origin /api base.
    text = text.replace('http://localhost:8080/api/', '/api/')
    text += '''\n// Campus Deploy aurashop-v1: normalize mixed API prefixes.
api.interceptors.request.use(config => {
  if (config.url?.startsWith('/api/')) config.url = config.url.slice(4);
  return config;
});
'''
    api.write_text(text, 'utf-8')
    changes.append('frontend/src/api.js: same-origin API/refresh, 중복 /api 정규화')
    upload = backend / 'src/main/java/com/aura/shop/controller/FileUploadController.java'
    text = upload.read_text('utf-8')
    if '"http://localhost:8080/uploads/"' not in text:
        raise Rejected('aurashop 업로드 URL 소스가 호환 레시피와 다릅니다.')
    upload.write_text(text.replace('"http://localhost:8080/uploads/"', '"/uploads/"'), 'utf-8')
    changes.append('FileUploadController.java: 업로드 URL을 상대 경로로 반환')
    jwt = backend / 'src/main/java/com/aura/shop/util/JwtUtil.java'
    text = jwt.read_text('utf-8')
    old = 'Keys.secretKeyFor(SignatureAlgorithm.HS256)'
    if old not in text:
        raise Rejected('aurashop JWT 소스가 호환 레시피와 다릅니다.')
    jwt.write_text(text.replace(old, 'Keys.hmacShaKeyFor(java.util.Base64.getDecoder().decode(System.getenv("CAMPUS_JWT_SECRET")))'), 'utf-8')
    changes.append('JwtUtil.java: 프로젝트별 영속 서명키 (재시작/코드 롤백 후 세션 유지)')
    return changes
