# Campus Deploy

공개 GitHub 저장소 또는 ZIP에서 정적 사이트, Node/Express, Spring Boot + Vite + MySQL/Redis 앱을 배포하는 팀 시연용 플랫폼입니다. React/TypeScript 관리 화면, FastAPI control-plane, SQLite 영속 큐, 별도 워커, Docker 격리 빌드, 정적 파일 제공·서버 프록시 gateway가 연결되어 있습니다.

## 지원 범위

| 프리셋 | 입력 | 실행/산출물 |
|---|---|---|
| STATIC | 루트 `index.html`, HTML/CSS/클라이언트 JS | npm 실행 없이 정적 파일 수집 |
| VITE_STATIC | 루트 `package.json`, npm `package-lock.json` v2/v3, Vite 의존성, build script | Node 24 컨테이너에서 `npm ci`, `npm run build`, `dist/index.html` |
| NODE_SERVER | 루트 `package.json`, npm lockfile v2/v3, `scripts.start` | 격리된 `npm ci`, 선택적 `npm run build --if-present`, 배포별 이미지, `npm start` |
| SPRING_BOOT | 루트 Spring Boot Gradle/Java 21 + `frontend/` Vite/npm lockfile | Vite 정적 화면 + bootJar/Java 이미지 + 프로젝트별 MySQL·Redis·uploads |

ZIP을 감싼 단일 최상위 폴더는 정규화합니다. 공개 `https://github.com/owner/repo`의 실제 기본 브랜치를 조회하고 commit SHA에 고정된 archive를 가져옵니다. 계정/PAT는 필요 없습니다. npm 공식 registry의 잠긴 의존성만 지원합니다.

Node 앱은 `process.env.PORT`를 사용하고 `0.0.0.0`에서 listen하며 `/`에 2xx HTTP 응답을 반환해야 합니다. 플랫폼이 `PORT=8080`을 전달하므로 사용자에게 명령·포트를 묻지 않습니다.

```js
app.listen(Number(process.env.PORT), '0.0.0.0');
```

루트 Gradle Spring Boot는 SPRING_BOOT로, npm 프로젝트는 Vite를 먼저 판정하고 나머지 start 프로젝트를 NODE_SERVER로 검사합니다. Python/FastAPI, Maven, Next.js SSR/ISR, WebSocket, background worker/cron, 일반 monorepo/다중 Gradle 모듈, pnpm/yarn, private Git, webhook/자동 HTTPS는 지원하지 않습니다. Node 프리셋의 DB/Redis 제한은 유지됩니다. 사용자 Dockerfile/Compose는 실행하지 않습니다. 모든 프로그램의 동작을 정적으로 판별하는 것은 아닙니다.

**GitHub 코드 주소는 실행 중인 사이트 주소가 아닙니다.** 플랫폼을 실행한 뒤 관리 화면의 공개 GitHub 입력란에 저장소 루트 주소를 넣어 배포하세요. 사용자 승인으로 `Gandalem/aurashop`의 Spring Boot·MySQL·Redis·frontend 구성 지원을 추가했습니다.

## Spring Boot / aurashop

`https://github.com/Gandalem/aurashop.git` 입력 → 소스 검사 → **SPRING_BOOT** → 배포하기. 최초 Java 의존성 다운로드와 DB 초기화에 몇 분이 필요할 수 있습니다. Docker/AWS/DB 암호/포트 입력은 필요하지 않습니다. `scripts/start`가 Gradle 9.4.1/JDK 21, Temurin 21.0.10 JRE, MySQL 8.4.8, Redis 7.4.8 고정 버전 이미지를 준비합니다. 배포는 사용한 실제 image ID를 보존합니다.

기본 계약은 루트 단일 Spring Boot Gradle 프로젝트, `frontend/`의 잠긴 Vite, `/api/health`의 2xx, Spring 표준 datasource/Redis 환경 변수, `/api/*` 및 `/uploads/*`입니다. aurashop은 상품 조회 `/api/products`로 DB 연결까지 검사합니다. Maven 및 임의의 Java 구조 전체를 지원하는 것은 아닙니다.

`aurashop-v1` 레시피는 배포용 복사본에서만 API/refresh 주소와 중복 `/api`를 정규화하고 업로드 URL을 상대 경로로 변경합니다. JWT 서명키는 프로젝트별로 생성·보존합니다. 원본 Git/SHA는 그대로 유지하고 변경 파일/레시피는 빌드 로그와 배포 settings에 기록합니다. 일반 Spring 프로젝트는 이 소스 보정을 받지 않습니다.

MySQL·Redis·uploads는 프로젝트 전용 internal network와 영속 볼륨을 사용합니다. **Preview와 운영은 데이터를 공유하며 롤백은 코드만 되돌립니다.** 첫 배포만 Hibernate `update`, 이후에는 `validate`로 자동 스키마 변경을 막습니다. 수동 migration/DB snapshot 복원은 미지원이며 새 코드와 기존 DB의 호환성이 필요합니다. 재배포·서버 재시작으로 데이터가 삭제되지 않습니다. 프로젝트 삭제는 DB·Redis·업로드 데이터까지 영구 삭제합니다. 실패/취소 때 서비스 볼륨은 남겨 재시도할 수 있습니다.

## GitHub 주소로 배포하기

1. 관리 화면 `http://localhost:3000`을 새로고침하고 **새 프로젝트**를 누릅니다. 기본 선택은 **공개 GitHub**입니다.
2. `https://github.com/owner/repository` 주소를 붙여넣습니다. 프로젝트 이름과 고유 주소가 자동으로 채워지며 직접 수정할 수도 있습니다.
3. **소스 검사 → 배포하기 → READY → 운영 사이트 열기** 순서로 진행합니다. ZIP은 필요하지 않습니다.

바로 시험할 수 있는 정적 예시는 `https://github.com/mdn/beginner-html-site-styled`입니다. Node/Express도 같은 Git 입력 경로를 사용하며 앞의 npm/PORT 조건을 충족해야 합니다. 현재 로컬 설정에서는 이 PC의 Docker가 사이트를 실행합니다. GitHub 업로드만으로 AWS에 사이트가 공개되지는 않습니다. ZIP 입력은 **ZIP 업로드** 탭을 선택하면 계속 사용할 수 있습니다.

## Windows 시작

최초 준비: Docker Desktop을 **Linux containers** 모드로 실행하고 Python 3.12 이상을 설치합니다. 관리 화면 빌드는 Docker에서 하므로 호스트 Node는 E2E/개발 때만 필요합니다. 이 PC에는 Python 3.13.2, Node 24.11.1, Docker Desktop이 이미 확인되었습니다.

```powershell
cd D:\CampusDeploy\campus-deploy
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
powershell -ExecutionPolicy Bypass -File scripts/doctor.ps1
```

`ExecutionPolicy Bypass`는 해당 스크립트 프로세스에만 적용합니다. 시스템 정책을 바꾸지 않습니다.

관리 화면: <http://localhost:3000>. 로그인 암호는 **`.secrets/admin-password.txt`**에 저장됩니다. 설정 스크립트는 기존 암호·서명키·`.env`를 보존하고 암호를 터미널에 출력하지 않습니다. 공개 회원가입은 없습니다.

## Ubuntu 시작

Python 3.12+, `python3-venv`, Docker Engine + Compose plugin을 운영자가 준비한 뒤:

```bash
cd campus-deploy
bash scripts/setup.sh
bash scripts/start.sh
bash scripts/doctor.sh
sudo bash scripts/install-systemd.sh
```

Docker daemon 권한은 운영자 전용입니다. worker의 socket 접근 권한은 사실상 호스트 관리자 권한입니다. Ubuntu 네이티브 호스트 설치는 이번 실행에서 미검증이며, 동일 Compose의 Linux 컨테이너 동작은 Windows Docker Desktop에서 검증했습니다.

`install-systemd.sh`는 최초 준비/빌드 후 한 번 설치합니다. Docker 시작 뒤 기존 플랫폼 이미지를 이용해 Compose를 자동 실행합니다. 운영 Node 컨테이너가 없으면 worker가 저장된 image ID로 복구하고 HTTP를 검사합니다. `.env`, `.secrets`, Docker data-root와 명명 볼륨은 EBS에 보존해야 합니다. 부팅할 때 Git/npm 재실행은 필요하지 않습니다. EC2 절차는 [AWS_DEMO.md](AWS_DEMO.md)를 참조하세요.

## 사용자 시연

1. 로그인 → **새 프로젝트** → 이름과 slug 입력.
2. `samples/static-v1.zip` 선택 → **소스 검사** → `STATIC` 확인 → **배포하기**.
3. READY와 실제 로그 확인 → **운영 사이트 열기** → 인사하기 버튼 클릭.
4. **새 버전 배포**에서 `static-v2.zip` 검사·배포. Preview는 v2, 고정 운영 주소는 v1입니다.
5. **운영 반영** → 고정 주소 새로고침 → v2 확인. v1 Preview도 여전히 열립니다.
6. 운영 전환 이력의 v1 **이 버전으로 롤백** → 고정 주소 새로고침 → v1 확인. 새 빌드는 생기지 않습니다.
7. 새 프로젝트에 `react-vite-spa.zip` 배포 → 버튼 클릭 → `/about` 이동·새로고침.
8. `build-failure.zip`을 기존 프로젝트에 새 배포 → 실제 exit 7 및 로그, 운영 보존 확인. `unsupported-server.zip`은 검사 단계에서 거부됩니다.
9. 새 프로젝트에 `node-express-v1.zip` → NODE_SERVER → BUILDING/STARTING/HEALTH_CHECK/READY → **웹 서버 실행 중**. 빌드/런타임 로그를 각각 확인하고 사이트의 **서버에 인사하기**를 누릅니다.
10. 같은 프로젝트에 `node-express-v2.zip`을 배포합니다. Preview v2와 운영 v1을 비교한 뒤 운영 반영·v1 롤백을 실행합니다. 전환은 서버 응답 검사 후 완료되며 롤백은 보존 이미지로 컨테이너를 다시 만듭니다.
11. `node-express-failure.zip`은 npm 설치·이미지 생성 후 서버가 exit 17로 종료합니다. 배포 FAILED와 runtime stderr, 기존 운영 유지를 확인합니다.

`이 소스로 재배포`는 선택한 불변 소스 snapshot을 다시 빌드합니다. Git 최신 commit을 가져오려면 **새 버전 배포 → 공개 GitHub → 소스 검사**를 사용합니다.

## 주소와 운영

- 관리: `http://localhost:3000` (호스트 loopback에만 바인딩).
- 운영: `http://p-<slug>.localhost:8080`.
- Preview: `http://d-<deployment-id>.localhost:8080`.
- `BASE_DOMAIN`, `SITE_SCHEME`, `SITE_PORT`는 URL/Host 설정입니다. `SITE_PORT`는 Compose의 외부 포트에도 적용됩니다(내부 gateway는 8080 고정). P0가 TLS를 제공하지 않으므로 기본값은 HTTP입니다.
- 기본 사이트 바인딩도 `127.0.0.1`입니다. 승인된 외부 시연만 [AWS_DEMO.md](AWS_DEMO.md)를 따릅니다.
- 메타데이터·로그·산출물·보존 소스는 Compose 명명 볼륨 `campus-deploy_metadata`, `campus-deploy_artifacts`, `campus-deploy_data`에 있습니다. `.data`는 로컬 테스트용이며 운영 볼륨이 아닙니다.
- 일반 재시작/중지는 데이터를 지우지 않습니다. 진행 중이던 작업은 lease 만료(최대 약 30초) 뒤 실패·정리되고 기존 운영은 유지됩니다.
- Node 이미지와 과거 READY 이미지는 Docker image store에 보존됩니다. 실행 컨테이너는 프로젝트당 현재 운영과 최신 Node Preview를 유지하고 나머지는 제거할 수 있습니다. 정지된 과거 Preview는 503이며 운영 반영/롤백으로 다시 실행할 수 있습니다. 운영 복구 실패는 **UNAVAILABLE / 서버 복구 필요**로 표시하고 gateway는 503을 반환합니다.
- NODE_SERVER HTTP 요청 본문은 2MiB, 응답은 50MiB 한도입니다. WebSocket은 미지원입니다. 앱의 `/api/*`는 해당 Node 앱으로만 전달되며 관리 API로 연결하지 않습니다.

```powershell
.\scripts\stop.ps1
.\scripts\start.ps1
docker compose logs --tail 100 worker
```

영속 데이터 삭제는 별도 파괴 작업입니다. 정상 종료와 혼동하지 마세요. 모든 프로젝트를 지우기로 결정한 운영자만 프로젝트 경로에서 `docker compose down --volumes`를 실행합니다. 이 명령은 여기서 실행하지 않았습니다. `.secrets`와 `.env`는 별도로 남습니다. 다른 프로젝트 자원이나 `docker system prune`을 사용하지 않습니다.

## 검증 명령

```powershell
.\.venv\Scripts\python.exe scripts/samples.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/smoke.py
.\.venv\Scripts\python.exe scripts/node_smoke.py
cd frontend
npm ci
npx playwright install chromium
npm run test:e2e
cd ..
.\.venv\Scripts\python.exe scripts/doctor.py --browser
.\.venv\Scripts\python.exe scripts/robustness.py
```

`robustness.py`는 **이 플랫폼의 워커를 실제 중단/재시작**합니다. `node_smoke.py`는 자체 시험 이미지의 백업·제거·복원, 컨테이너 제거, **전체 Campus Deploy Compose 중지/재시작**까지 수행합니다. 시연·개발 중에만 실행하세요. 다른 프로젝트 자원 보존도 검사하며 글로벌 Docker 재시작/prune은 하지 않습니다. E2E는 암호를 로컬 파일에서 읽되 출력하지 않으며 Playwright trace를 저장하지 않습니다.

실제 결과/명령/제약은 [VERIFICATION.md](VERIFICATION.md), 시스템 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), 보안 경계는 [SECURITY.md](SECURITY.md), 발표 순서는 [DEMO_SCRIPT.md](DEMO_SCRIPT.md)에 있습니다.
