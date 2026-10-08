# 실검증 보고서

최신 P1 기준일: **2026-10-08, Asia/Seoul**. 로컬 Windows + Docker Desktop Linux engine에서 실행했습니다. AWS에서 실행하지 않았습니다. 아래 P0 기록은 2026-10-07의 이력이며, 이 절의 P1 회귀 결과가 최신입니다. `evidence/` 원본은 로컬에서 생성되는 비밀 제외 자료이며 Git에는 선별한 요약/스크린샷만 `docs/verification/`에 포함합니다.

## P1 실제 결과

| 검사 | 결과 |
|---|---|
| 변경 전 기존 pytest | 45 passed |
| STATIC/VITE HTTP smoke | P1에서도 실제 npm ci/build·v1/v2·실패 exit 7·운영 전환/롤백 PASS |
| 확장 pytest | **73 passed**, 감지·tar/link·log·proxy headers·운영 전환 idempotency/실패·중단 복구·startup 오류 표시 검사 |
| TypeScript/Vite 관리 UI build | PASS |
| Chromium E2E | GitHub URL 입력, STATIC, VITE, NODE_SERVER **4 passed (43.8초)**; 실제 DNS, 버튼 클릭, 운영 전환/롤백 |
| 기존 robustness.py | 공개 Git/SHA, queue, 취소, timeout, worker SIGKILL, 삭제, 영속성 전체 PASS |
| node_smoke.py | 아래 P1 수명주기 14개 실제 검사 PASS; 로그·이미지 ID 보존 증거 |
| node_recovery_failure.py | 실제 운영 컨테이너·이미지 누락 후 startup UNAVAILABLE/HTTP 503, STATIC 보존. 이미지 복원 뒤 자동 RUNNING, 동일 image/build log 확인 PASS |
| Ubuntu systemd installer | Linux 컨테이너의 `bash -n` 구문 검사 PASS. 실제 systemd install/boot는 미실행 |
| 최종 audit/doctor | SQLite integrity ok, staging/work 비어 있음, builder/작업 볼륨 0, Node 10개 모두 host port 없음, 운영 HTTP 유지. Chromium DNS PASS; OS getaddrinfo는 11001로 실패 |

GitHub에 포함한 [선별 증적과 화면](docs/verification/README.md), [기계 판독 요약](docs/verification/summary.json)을 확인할 수 있습니다.

GitHub 입력 개선 후 공개 GitHub 기본 탭에서 `https://github.com/mdn/beginner-html-site-styled` 주소만 입력했습니다. 이름/slug 자동 입력 → 소스 검사 → 배포 → 운영 사이트 Chromium HTTP 200 및 실제 제목 표시를 확인했습니다. ZIP은 사용하지 않았으며 소스 종류 GIT와 40자리 commit SHA를 확인했습니다. 기존 ZIP STATIC/Vite/Node 시나리오도 함께 통과했습니다. [GitHub 브라우저 증적](docs/verification/browser-github.json). `Gandalem/aurashop`은 GitHub로 소스를 가져왔지만 Spring Boot/Java 미지원으로 소스 검사에서 거부되었으며 배포 성공으로 취급하지 않습니다.

Node 샘플 Express는 npm registry 확인 후 **5.2.1**로 고정했고 3개 sample lockfile을 생성했습니다. 실제 빌더는 Node v24.11.1 / npm 11.6.2입니다. ZIP SHA-256 또는 Git commit SHA를 배포·이미지 label에 기록합니다.

1. ZIP Express 자동 NODE_SERVER 감지 → 실제 npm ci → 고정 Dockerfile 이미지 → STARTING/HEALTH_CHECK/READY.
2. 실제 runtime inspect: uid/gid 1000, read-only, 0.5 CPU, 512MiB, PID 128, cap-drop ALL, no-new-privileges, host published port 없음, bind/socket 없음.
3. runtime network internal=true, gateway 연결, API/worker는 미연결. builder는 bridge에서 npm 설치.
4. Preview HTTP 200, `/version`의 PORT=8080, 실제 POST `/greet`, 앱 `/api/example`. 관리 `/api/projects` 404, WebSocket 426.
5. UI에 웹 서버 실행 중과 현재 운영 ID, 별도 build/runtime 로그. Node browser 실제 POST 응답과 정상 console/network 결과.
6. v2 Preview가 READY여도 v1 Production 유지.
7. v2 runtime을 pause한 상태에서 promote → health 실패/operation FAILED → v1 유지. unpause 후 promote → 같은 URL v2.
8. v1 이미지를 백업하고 소유 이미지/컨테이너만 제거 → rollback 실패 → v2 유지. 이미지 복원 뒤 rollback 성공.
9. 성공 rollback에서 v1 컨테이너 ID는 새 값, image ID는 기존 값, build log는 byte 내용 그대로. Git/npm/build 재실행 없음.
10. failure sample npm 설치·이미지 생성은 성공, runtime exit 17 → FAILED와 stderr 보존. 운영 v1 유지, 실패 자원 정리.
11. 별도 Node 프로젝트 삭제 → 해당 runtime/image 제거, 다른 Node image/운영 및 STATIC artifact 보존.
12. api/gateway/worker restart 후 운영 유지. 전체 Campus Deploy compose stop 후 Production runtime 삭제 → compose up 뒤 자동 image 복구·health, Node v1+STATIC 모두 정상.
13. 다른 기존 Docker container 4개의 ID와 exited 상태가 전후 동일. 글로벌 daemon restart/prune은 하지 않음.
14. 관리 Origin/CSRF/host, static 경로/SPA/MIME 정책의 기존 회귀 검사가 유지됨.

실행 명령: `python scripts/node_smoke.py`, `python scripts/node_recovery_failure.py`, `python scripts/smoke.py`, `python scripts/robustness.py`, `python -m pytest -q`, frontend의 `npm run test:e2e`. Python은 실제로 프로젝트 `.venv`를 사용합니다. fault-injection 스크립트는 이 설치의 서비스를 중지하므로 개발·시연 환경에서만 실행합니다.

P1 개발 중 처음 실제 Docker 시험에서 DockerClient의 context manager 미지원이 드러났습니다. `contextlib.closing`으로 수정하고 전체 수명주기를 다시 검증했습니다. 첫 Node E2E는 모든 UI 동작 후 Playwright의 별도 Node HTTP client가 `.localhost`를 IPv6 `::1`로 선택하여 실패했습니다. 제품의 Chromium 접속은 성공했고, 경계 검사도 실제 Chromium 새 페이지로 수행하도록 바꾼 뒤 3개 전체 E2E가 통과했습니다. IPv4-only loopback 설정은 유지했습니다.

**미검증:** 실제 EC2 stop/start, Ubuntu systemd boot, 외부 장비/공인 DNS/SG, 글로벌 Docker daemon 재시작 및 호스트 강제 재부팅. 로컬 Compose 전체 중지/재시작과 runtime 누락 복구를 이들과 혼동하지 않습니다. AWS 자원 변경은 전혀 실행하지 않았습니다. 장기 부하·완전한 악성 코드/tenant 격리·공급망 취약점 전수 감사도 미검증입니다.

## P0 원래 검증 이력 (2026-10-07)

## 최종 결과

| 명령 (프로젝트 루트 기준) | 실제 종료 코드 | 결과/증적 |
|---|---:|---|
| `.venv\Scripts\python.exe -m pytest tests/test_static_flow.py -q -p no:cacheprovider --basetemp .data/test-static-2` | 0 | 첫 단계 STATIC 실제 HTTP/운영 전환/롤백: 1 passed |
| `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .data/test-stage4` | 0 | 45 passed, 1 warning (Starlette TestClient의 httpx 향후 변경 안내) |
| `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .data/test-final --junitxml evidence/pytest.xml` | 0 | 최종 소스 회귀 45 passed, 7.44초. [pytest XML](evidence/pytest.xml) |
| `npm run build` (frontend) | 0 | TypeScript + Vite production 빌드, React UI를 API origin에서 제공 |
| `docker compose build api` 및 `docker compose up -d --no-build` | 0 | API/gateway/worker 실제 실행, Linux 명명 볼륨 |
| `.venv\Scripts\python.exe scripts/smoke.py` | 0 | STATIC v1/v2, 실제 Vite npm 빌드, 실패 exit 7, 운영 보존, rollback, 중복 요청. [smoke.json](evidence/smoke.json) |
| `npm run test:e2e` (frontend, 수정 후) | 0 | Chromium 2 passed, 15.8초. [Playwright 결과](evidence/playwright-results.json) |
| `.venv\Scripts\python.exe scripts/robustness.py` | 0 | 공개 Git, 실제 queue/취소/SIGKILL/timeout/빌드 중 삭제/재시작 지속성. [robustness.json](evidence/robustness.json) |
| `.venv\Scripts\python.exe scripts/doctor.py --browser` | 0 | Docker/API/worker/실제 Chromium 서브도메인 통과. OS DNS 주의사항은 아래 참조. [doctor.json](evidence/doctor.json) |
| `powershell -ExecutionPolicy Bypass -File scripts/setup.ps1` (재실행) | 0 | 기존 암호/서명키 보존, Windows 비밀 폴더 ACL, 샘플 재생성 |
| `scripts/stop.ps1` → `scripts/start.ps1` | 0 | 영속 볼륨 삭제 없이 중지·실제 재기동, 서비스 실행 유지 |
| `.venv\Scripts\python.exe scripts/audit.py` | 0 | SQLite integrity ok, gateway mount 전부 read-only, worker만 socket, 삭제 산출물 부재, staging/work 빈 디렉터리, 잔존 빌드 컨테이너/볼륨 각 0. [최종 감사](evidence/final-audit.json) |

pytest 임시 데이터는 `.data/test-*` 아래에 있습니다. SQLite 로그/프로젝트/배포/전환 이력의 운영 원본은 `campus-deploy_metadata` 볼륨에 있습니다. 테스트 실패를 성공으로 치환하는 백엔드 분기는 없습니다. 모든 샘플은 일반 API ZIP/Git 입력 경로를 통과했습니다.

## 요구사항별 확인

| # | 검증 대상 | 관찰한 결과 |
|---:|---|---|
| 1 | STATIC ZIP → 실제 브라우저 | UI에서 업로드·검사·배포. v1 문구, CSS computed background, JS 인사하기 버튼 확인 |
| 2 | React/Vite 실제 설치·빌드·클릭 | Node v24.11.1, npm 11.6.2, npm ci, vite build stdout 보존. 카운터 0→1 확인 |
| 3 | SPA/MIME | `/about` 이동·새로고침 후 React 동작. JS `application/javascript`, CSS `text/css`, 모두 200. missing.js는 404 |
| 4 | 공개 Git/commit | `mdn/beginner-html-site-styled`, API가 반환한 기본 branch `main`, SHA `6c7a360ddb4a0d75be06044bf8a914f260ff10c7`, 실제 STATIC 배포 READY |
| 5 | Preview와 운영 분리 | v2 Preview READY 상태에서도 고정 운영 URL의 v1 문구 유지 |
| 6 | promote와 immutable Preview | 운영을 v2로 전환한 후에도 v1 배포별 URL은 v1 |
| 7 | rollback | 고정 URL 새로고침 시 v1. 단위/통합 테스트에서 jobs 행이 늘지 않음 확인 |
| 8 | 빌드 실패 | 실제 Node `process.exit(7)` → FAILED/exit 7/INTENTIONAL_BUILD_FAILURE 로그. 기존 운영 v1 보존 |
| 9 | queue/중복/timeout/취소 | 워커 중지 중 두 작업 QUEUED, 동일 키 요청 두 건 한 작업으로 수렴, 첫 작업 finished ≤ 둘째 FETCHING. 실제 실행 중 취소와 5초 시험용 timeout(124) 후 소유 컨테이너/볼륨 0 |
| 10 | 재시작/중단 복구 | 실행 중 worker SIGKILL → lease 만료 후 FAILED/정리. API/gateway 재시작 후 세션·프로젝트·로그·운영 보존 |
| 11 | 인증/위험 입력/origin | 익명 변경 401, 잘못된 Origin/CSRF 403, 로그인 제한 429, 위험 Git URL/ZIP/tar 차단. unknown Host, dotfile, 사이트 `/api` 404. 브라우저의 관리 API cross-origin 읽기 차단 |
| 12 | 소유 삭제 | 빌드 중 프로젝트 삭제 → CANCELED, 해당 라우트/산출물 정리 완료 및 소유 Docker 자원 0. 다른 프로젝트 운영 v1 유지. 공유 소스 보존은 pytest로 별도 검사 |

### 실제 버전과 격리 설정

- Host: Windows, Python 3.13.2, Node v24.11.1, npm 11.6.2, Git 2.51.0.windows.2.
- Docker Desktop 4.93.0, Engine 29.8.1 linux/amd64, Compose 5.5.1.
- Python platform image: `python:3.13.2-slim-bookworm@sha256:6b3223eb4d93718828223966ad316909c39813dee3ee9395204940500792b740`.
- Node builder: `node:24.11.1-bookworm-slim`, 실제 digest `sha256:48abc13a19400ca3985071e287bd405a1d99306770eb81d61202fb6b65cf0b57`.
- npm/패키지 registry에서 버전·Node engine 조건을 조회한 뒤 React 19.3.0, Vite 8.3.3, TypeScript 7.0.2, Playwright 1.63.0으로 고정했습니다. Python 전체 전이 의존성은 `requirements.lock`, UI/샘플은 `package-lock.json`에 있습니다.
- 실제 `docker inspect`로 uid/gid `1000:1000`, read-only root, memory `2147483648`, NanoCPUs `1000000000`, PID 256, cap-drop ALL, no-new-privileges, bridge, socket 미전달을 확인했습니다. [실제 격리 기록](evidence/robustness.json)
- tmpfs 작업 1GiB + `/tmp` 256MiB, Node/npm 실제 출력과 image digest는 [Vite 로그](evidence/vite-build-log.json)에 있습니다.
- 기본 timeout은 600초입니다. timeout 동작 검증은 별도의 시험 worker에만 5초를 주어 실행했으며, 정상 worker 설정은 변경하지 않았습니다.

## 브라우저 증적

Chromium 실제 DNS 해석을 사용했고 hosts/resolver override를 넣지 않았습니다.

- [로그인 화면](evidence/01-login.png)
- [ZIP 감지 결과](evidence/02-source-detection.png)
- [STATIC CSS/버튼 동작](evidence/03-static-browser.png)
- [프로젝트와 롤백 이력](evidence/04-project-rollback.png)
- [프로젝트 목록](evidence/05-projects.png)
- [React /about 새로고침 후 카운터](evidence/06-react-spa.png)
- [Vite 실제 배포 로그 화면](evidence/07-vite-deployment.png)

관리 UI와 STATIC 사이트는 browser pageerror, requestfailed, 예상하지 못한 console error가 없었습니다. React도 앱 동작·JS/CSS 로딩 오류가 없었습니다. 별도의 공격 검증으로 사이트에서 `localhost:3000/api/projects` fetch를 시도하여 **예상한 CORS 차단 오류 1건/관련 console 2줄**이 남았으며, 앱 실패와 구분해 원본을 보존했습니다. [STATIC 네트워크 기록](evidence/browser-static.json), [React MIME·CORS 기록](evidence/browser-react.json), [의도된 빌드 실패 로그](evidence/failure-build-log.json)

Windows OS의 `socket.getaddrinfo('p-doctor.localhost')`는 첫 doctor에서 11001로 실패했고, 최종 재기동 후 doctor에서는 `127.0.0.1` 해석에 성공했습니다. 원인을 단정하지 않습니다. Chromium은 두 검사 모두 hosts/resolver override 없이 실제 접속하여 unknown project 404까지 받았습니다. doctor는 OS 조회와 브라우저 확인을 별도 항목으로 표시합니다. 일반 HTTP smoke는 `127.0.0.1:8080` + 정확한 Host로, E2E는 실제 서브도메인 URL로 검증했습니다.

## 개발 중 실패와 수정 기록

| 초기 검증 | 종료 코드 | 원인/조치 |
|---|---:|---|
| 첫 pytest 실행 | 1 | sandbox의 기존 Temp/cache 접근 거부. 프로젝트 내부 고유 basetemp에서 실제 재실행하여 통과 |
| 초기 Compose STATIC smoke | 1 | gateway read-only SQLite WAL/SHM 접근 실패. writer close checkpoint 설정과 Linux metadata 볼륨으로 해결 |
| 볼륨 분리 후 smoke | 1 | staging/artifacts 간 EXDEV. staging을 artifacts 볼륨 내부 `.staging`으로 이동하여 원자 rename 확보 |
| 첫 Docker Vite smoke | 1 | read-only root + 직접 tmpfs에 put_archive 불가. Docker SDK archive가 지원하는 소유 명명 tmpfs 작업 볼륨으로 해결 |
| 첫 UI TypeScript build | 1 | CSS side-effect 타입 선언 누락. vite/client 타입 추가 후 통과 |
| 첫 STATIC E2E | 1 | 새 배포 대기 중 이전 Preview가 잠시 노출됨. 선택 배포 상태 처리와 테스트의 완료 대기를 수정하고 전체 2개 E2E 통과 |
| 첫 Git 시연 저장소 smoke | 1 | octocat/Spoon-Knife의 HTML이 저장소에 없는 `/forkit.gif`를 참조. 게이트웨이 검사가 올바르게 FAILED 처리. 입력을 개조하지 않고 완결된 MDN 공개 샘플로 별도 성공 검증 |

## 미검증과 제한

- AWS EC2 배포·자원 변경, 실제 공인 IPv4/sslip.io DNS, 외부 네트워크/다른 장비 접속: **미실행**. 로컬 성공과 구분합니다.
- Ubuntu 네이티브 설치 스크립트: **미실행**. Linux 컨테이너/Compose는 실제 실행했습니다.
- 장시간 부하, host 재부팅/디스크 완전 소진, 600초 실제 대기 전체, 악성 코드 탈출/네트워크 egress/metadata 완전 차단: **미검증 또는 미구현**. 한계는 SECURITY.md에 있습니다.
- 의존성 잠금과 실제 호환 빌드는 확인했지만 공급망 취약점 전수 감사는 하지 않았습니다.
- 당시 P0에서 webhook/자동 HTTPS, 사용자 서버 앱, 결제/조직 기능은 구현하지 않았습니다. 2026-10-08 P1의 Node HTTP 서버 추가 결과는 문서 첫 절을 참조하세요.
