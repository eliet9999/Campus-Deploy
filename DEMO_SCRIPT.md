# 10–15분 P0/P1 시연 순서

## Spring Boot 추가 시연

1. 최신 `scripts/start`로 Node/Gradle/Java/MySQL/Redis 이미지를 준비하고 관리 화면을 새로고침합니다.
2. 새 프로젝트 → 공개 GitHub → `https://github.com/Gandalem/aurashop.git` → 소스 검사. **SPRING_BOOT**를 확인하고 배포합니다. 첫 Java 빌드/DB 준비에는 추가 시간이 필요합니다.
3. BUILDING → STARTING → HEALTH_CHECK → READY 및 빌드/런타임 로그, 운영 사이트를 확인합니다. 프런트와 `/api/products`가 모두 실제 응답해야 합니다.
4. 시험 계정을 만들고 로그인, 상품/장바구니 및 작은 이미지 업로드를 시험합니다. 회원가입 화면의 Daum 주소 선택은 외부 서비스가 필요합니다. 실제 결제는 수행하지 않습니다.
5. 새 Preview → 운영 반영 → 이전 버전 코드 롤백. DB/Redis/uploads는 프로젝트 단위 공유이며 데이터의 과거 시점 복원이 아니라는 점을 설명합니다.
6. 로컬 검증 자동화는 `scripts/spring_smoke.py`, frontend `npm run test:e2e`, `scripts/spring_lifecycle.py`. 마지막 검사는 Campus Deploy 중지/재기동과 시험 프로젝트 삭제를 포함하므로 시연 중 실행하지 않습니다. 자세한 데이터/복구 경계는 [SPRING_BOOT.md](SPRING_BOOT.md)를 참조하세요.

## 사전 리허설

`scripts/start` → `scripts/doctor --browser`. samples ZIP을 준비합니다. 관리 화면 3000과 사이트 8080을 각각 브라우저로 엽니다. 실제 Node 24 이미지가 준비되어 있어야 합니다. 다운로드/rate limit 변동에 대비하여 먼저 ZIP 흐름을 시연합니다.

## 화면에서 진행

1. **로그인 / 지원 범위** — 암호는 파일에서 로컬로 확인. STATIC, 클라이언트 Vite, DB 없는 Node/Express HTTP 서버를 지원한다고 설명합니다.
2. **첫 배포** — 새 프로젝트 `campus-demo-<고유값>` 생성, static-v1.zip 업로드·검사. 표시된 STATIC 결과 확인 후 배포. 실제 단계·로그·SHA를 봅니다. 운영 URL을 열어 버튼을 누릅니다.
3. **Preview** — 같은 프로젝트의 새 버전에 static-v2.zip 업로드. Preview v2와 운영 v1을 나란히 엽니다. URL 차이를 확인합니다.
4. **운영 반영** — 확인 창에서 영향 범위를 읽고 반영. 고정 주소 새로고침으로 v2 확인. 이전 Preview는 v1 유지.
5. **롤백** — 이력의 v1 롤백. 고정 주소 새로고침으로 v1 확인. 배포 개수가 늘지 않고 기존 산출물을 재사용하는 점을 봅니다.
6. **React/Vite** — 별도 프로젝트에 react-vite-spa.zip. 실제 npm ci/build stdout과 Node 버전 확인. 사이트에서 카운터를 클릭하고 `/about` 이동 후 새로고침합니다.
7. **실패** — 기존 STATIC 프로젝트에 build-failure.zip을 새 배포. FAILED와 `INTENTIONAL_BUILD_FAILURE`, exit 7 확인. 운영 v1이 유지됩니다. unsupported-server.zip은 배포 전에 거부됩니다.
8. **Git** — 새 프로젝트의 공개 GitHub 탭에서 `https://github.com/mdn/beginner-html-site-styled` 검사. 기본 브랜치·commit SHA 확인 후 배포. 실시간 GitHub 가용성은 별도이며 실패 시 오류를 그대로 보여줍니다.
9. **정리** — 시험 프로젝트 삭제 확인 창 → 삭제 완료 결과. 다른 프로젝트 운영은 유지됩니다. 정상 서비스 종료는 stop 스크립트로 하고 데이터 삭제와 구분합니다.

## Node 서버와 복구

1. 새 프로젝트에 `node-express-v1.zip`을 업로드합니다. NODE_SERVER 감지 및 `npm start`/`process.env.PORT`/DB 미지원 안내를 확인합니다. Dockerfile·포트·명령을 입력하는 화면은 없습니다.
2. BUILDING → STARTING → HEALTH_CHECK → READY를 관찰합니다. **빌드 로그**에서 실제 npm ci·이미지 ID를, **런타임 로그**에서 `NODE_READY v1 PORT=8080`을 확인합니다.
3. Preview/운영 사이트에서 **서버에 인사하기** 클릭 → 실제 POST 응답 문구를 봅니다. Network에서 `/greet`가 Node 서버 응답인지 확인합니다.
4. 같은 프로젝트에 v2 ZIP → Preview v2/운영 v1을 나란히 확인합니다. **운영 반영** 완료 뒤 같은 운영 URL이 v2를 제공합니다.
5. v1 **롤백** → 새 빌드 없이 보존 image에서 runtime 재생성·health → 운영 v1. 오래된 runtime은 정리될 수 있고 READY 이미지는 보존된다는 점을 설명합니다.
6. failure ZIP → 빌드는 성공하지만 runtime exit 17 → FAILED와 stderr. 운영은 v1을 계속 제공합니다.
7. 리허설에서만 `scripts/node_smoke.py`를 실행해 실패한 promote/rollback의 운영 보존, 이미지 기반 복구, 소유 자원 정리, 전체 Compose stop/start를 실제 확인합니다. 글로벌 Docker나 다른 프로젝트를 중지하지 않습니다.
8. Ubuntu의 최초 `sudo bash scripts/install-systemd.sh` 설치 절차를 보여줍니다. AWS 자원을 만들거나 재부팅을 실행한 것처럼 설명하지 않습니다. EBS·이미지·고정 IP가 보존되면 부팅 후 자동 복구하는 설계이며 실제 EC2 검증은 별도입니다.

## 발표 시 구분할 내용

로컬 Chromium에서 사이트 서브도메인 해석·HTML/CSS/JS·상호작용을 검증했습니다. 이는 AWS나 다른 장비의 접속 검증을 의미하지 않습니다. Windows OS `getaddrinfo`는 초기 조회에 실패했으나 최종 doctor에서는 성공했고, Chromium은 두 검사 모두 hosts override 없이 직접 접속했습니다. 브라우저가 다르면 doctor/E2E로 다시 확인합니다.

콘솔 error/requestfailed 결과와 실제 스크린샷은 `evidence/`에 있습니다. 브라우저 테스트에는 의도된 cross-origin 관리 API 읽기 거부(CORS)가 포함되어 있으므로 해당 오류를 기능 오류와 구분합니다. 무중단/CDN/완전 격리 같은 주장은 하지 않습니다.
