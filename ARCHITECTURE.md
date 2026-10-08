# 구조와 불변식

## SPRING_BOOT 확장

2026-10-08 사용자 승인으로 Java 21/Gradle + frontend/ Vite + MySQL/Redis를 추가했습니다. 아래 P0/P1 불변식은 유지하며 Spring의 빌드, 서비스, 데이터 정책, 복구 경계는 [SPRING_BOOT.md](SPRING_BOOT.md)에 정의합니다. Spring 화면은 gateway의 정적 SPA 제공, `/api/*`와 `/uploads/*`는 같은 배포의 Java runtime으로 프록시됩니다.

```text
Browser localhost:3000 → FastAPI + built React UI → SQLite WAL
                                             ↘ uploads (bounded stream)
SQLite jobs → single Python worker → source validation → STATIC collector
                                      ↘ Node 24 Docker SDK builder → dist
                                      ↘ npm ci/build → validated tar → fixed Dockerfile (no RUN)
                                                             → deployment image ID → Node runtime
artifact volume /.staging/<id> → atomic rename → /<id>
worker → gateway:8080 + exact Host → HTML/assets probe → READY transaction
Browser p-/d-*.localhost:8080 → read-only gateway → immutable artifact
                                         ↘ runtime-<validated deployment ID>:8080
```

## 서비스 경계

API는 인증·입력 수신·SQLite 트랜잭션·큐 기록·React production 정적 파일 제공을 담당합니다. npm/Git 장기 실행은 없습니다. 업로드는 multipart 대신 raw ZIP body를 스트리밍하며 누적 한도를 적용합니다. 소스 검사도 큐 작업이므로 ZIP 추출/GitHub HTTP 가져오기는 워커에서 실행합니다.

worker만 Docker socket을 받습니다. Git CLI/호스트 shell로 사용자 입력을 실행하지 않습니다. GitHub API로 기본 브랜치와 commit을 조회하고 고정된 codeload URL에서 ZIP을 받아 같은 안전 추출기를 통과시킵니다. Git submodule/LFS 확장은 하지 않습니다.

builder는 고정된 명령 절차를 사용합니다. Docker SDK archive 전송을 위해 **소유 라벨이 있는 명명 tmpfs 볼륨**을 `/work`에 마운트합니다. worker 내부 경로를 Docker 호스트 bind 경로로 오인하지 않습니다. 빌드 이후 archive 회수 동안 컨테이너를 살아 있게 유지해야 tmpfs가 사라지지 않습니다. 종료 코드를 기록한 뒤 dist를 회수하고 finally에서 컨테이너·작업 볼륨을 지웁니다.

gateway는 socket·로그인 비밀·소스 볼륨을 받지 않습니다. metadata/artifacts 볼륨은 읽기 전용이고 SQLite 연결도 `mode=ro`/`query_only`입니다. WAL/SHM을 읽기 전용으로 열 수 있도록 writer의 `SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE`를 켭니다. 자동 WAL checkpoint는 기본값을 유지합니다.

## SQLite와 큐

모든 연결에 foreign keys, busy timeout 10초를 적용합니다. WAL 모드와 `BEGIN IMMEDIATE` 트랜잭션으로 작업 claim과 운영 포인터를 직렬화합니다. `worker_lease`의 단일 slot에 30초 임대, 2초 갱신을 사용합니다. 다른 워커는 유효한 임대 중 작업을 claim할 수 없습니다. 오래된 워커는 변경 전 소유권/만료를 재검사합니다.

작업 종류는 INSPECT, DEPLOY, DELETE, SWITCH입니다. 요청 식별자를 작업 의미/응답과 함께 저장하여 중복 클릭을 한 작업으로 합칩니다. 다른 payload에 같은 키를 쓰면 409입니다. 로그는 DB에 cursor와 build/runtime stream으로 저장되고 UI는 cursor polling을 사용합니다. 두 stream의 한도는 각각 2MiB입니다. runtime 로그 수집은 별도 thread에서 2초 간격으로 수행하므로 다른 빌드 중에도 계속됩니다. 상태는 퍼센트가 아닌 실제 단계별 timestamp입니다.

재시작 시 RUNNING 작업을 탐지합니다. 불완전 배포는 FAILED, 불완전 검사는 FAILED, 삭제는 재시도 대상으로 복구합니다. 남은 소유 Docker 컨테이너/볼륨도 설치 인스턴스 라벨 범위에서만 정리합니다. QUEUED 작업과 READY 배포는 유지합니다.

## 공개와 운영 전환

1. 배포별 `.staging/<id>`에 검증된 산출물을 완성합니다. 준비/완료 경로는 **동일 artifact 볼륨**이어서 rename이 원자적입니다.
2. PUBLISHING에서 immutable Preview 라우트를 허용하되 UI는 아직 READY로 표시하지 않습니다.
3. 내부 gateway URL에 배포 Host를 명시하여 HTML 및 로컬 script/stylesheet/image 응답과 JS/CSS MIME을 검사합니다. 외부 asset URL의 가용성을 보증하지 않습니다.
4. 검사 성공 후 취소·삭제·lease를 같은 트랜잭션에서 재검사하고 READY로 전환합니다. 첫 성공만 production pointer를 설정합니다.
5. 이후 배포는 Preview이며 운영 반영/롤백은 포인터와 전환 이력의 단일 트랜잭션입니다. 롤백 대상은 과거 운영 이력이 있고 산출물이 존재하는 READY 배포입니다.

READY 디렉터리는 덮어쓰지 않습니다. 실패/취소는 현재 production pointer를 수정하지 않습니다. 삭제 요청은 트랜잭션으로 `deleting=1`과 운영 포인터 해제·진행 작업 cancel을 설정하므로 게이트웨이가 즉시 해당 프로젝트를 거부합니다. worker가 자기 산출물·작업 자원을 정리한 뒤 삭제 완료를 기록합니다. 다른 프로젝트가 참조하는 소스는 보존합니다. 이력과 로그는 삭제 후 감사용으로 남습니다.

STATIC은 일반 파일/404, Vite만 HTML 내비게이션의 확장자 없는 경로에 index fallback을 적용합니다. 없는 JS/CSS/이미지는 404입니다. 디렉터리 목록·dotfiles·`/api` 라우팅은 제공하지 않습니다. 응답은 보수적인 `Cache-Control: no-store`입니다.

## NODE_SERVER 수명주기

루트 npm lockfile v2/v3, scripts.start, 미지원 구조/의존성을 검사합니다. Vite 정적 판정이 우선입니다. builder는 기존 격리 설정에서 `npm ci && npm run build --if-present`를 실행합니다. 사용자 Dockerfile은 실행하지 않습니다. 설치된 `/work` tar를 크기·파일·경로·링크 검사 후 메모리/임시 파일에서 새 Docker context로 재구성합니다. host에 extract하지 않습니다. 플랫폼 Dockerfile은 고정된 base image ID, COPY, USER, ENV, CMD만 포함하며 RUN이 없습니다.

`campus-runtime:<deployment-id>-<source-sha-prefix>` tag와 설치/프로젝트/배포/전체 SHA label을 기록합니다. DB의 실제 실행 기준은 변경 불가능한 image ID입니다. `.env`, socket, 키는 context와 runtime에 전달되지 않습니다.

runtime은 `runtime-<32자리 hex ID>`, uid/gid 1000, read-only root, 0.5 CPU/512MiB/128 PID, 64MiB tmpfs, cap-drop ALL/no-new-privileges로 실행합니다. host port와 bind mount는 없습니다. Compose가 소유 label이 있는 **internal runtime network**를 만들며 gateway만 관리·runtime 양쪽에 연결됩니다. API/worker는 관리 network, builder는 별도 기본 bridge입니다. 런타임 간 통신까지 격리하는 다중 tenant network는 아닙니다.

`BUILDING → STARTING → HEALTH_CHECK → READY` 순서입니다. gateway를 통해 `/`의 2xx 응답과 컨테이너 생존을 기본 30초 안에 확인합니다. health 동안 배포별 Host는 검사용으로 열릴 수 있지만 UI READY 및 Production pointer는 검사 성공 뒤에만 기록합니다. gateway는 검증된 ID로만 upstream 주소를 만들고 method/path/query/body를 전달하며 hop-by-hop 헤더를 제거합니다. Node 앱의 `/api`도 해당 앱에만 전달됩니다. 관리 API로 fallback하지 않습니다. WebSocket은 426, 정지/복구 실패 runtime은 503입니다.

Node promote/rollback 요청은 operations 행과 SWITCH job을 같은 트랜잭션에 기록합니다. 사용자에게 operation ID를 반환하고 UI가 완료를 기다립니다. worker가 health를 재검사하고 기존 포인터가 요청 시점과 같은지 확인한 뒤 포인터·transition·operation DONE을 한 번에 commit합니다. 실패·중단은 포인터를 바꾸지 않습니다. rollback은 보존 image에서 새 컨테이너를 만들며 Git/npm/build를 호출하지 않습니다. 이미 현재 운영인 대상을 가리키는 요청은 운영 컨테이너를 파괴하지 않습니다.

운영과 최신 Node Preview의 runtime은 유지합니다. 오래된 READY runtime은 제거할 수 있지만 image는 프로젝트 삭제 전까지 보존합니다. 프로젝트 삭제는 자기 label의 runtime/image만 제거하고 로그/이력은 남깁니다. 정리 실패는 기록·재시도하며 이미 성공한 운영을 실패로 바꾸지 않습니다.

## 재시작과 영속성

기존 SQLite에 runtime 필드·log stream을 추가하는 migration이며 P0 행을 덮어쓰지 않습니다. 미완료 DEPLOY는 실패·정리, INSPECT는 실패, DELETE는 재시도, 미완료 SWITCH는 실패로 마감합니다. 운영 포인터가 이미 성공 commit된 작업을 재실행하지 않습니다. startup reconciliation은 운영 Node의 이미지 소유권을 확인하고 없어진 컨테이너를 재생성·검사합니다. idle worker도 5초 간격으로 운영 컨테이너를 확인합니다.

역사적 배포 성공 상태와 현재 runtime 상태를 구분합니다. 복구 실패는 runtime_state=UNAVAILABLE, API/UI status=UNAVAILABLE, 오류 문구 및 gateway 503으로 표시합니다. 원래 운영 포인터와 image ID는 보존하므로 이미지/엔진이 돌아오면 자동 재시도할 수 있습니다. STATIC은 기존 artifact/포인터 그대로 제공됩니다.

Ubuntu systemd 설치 스크립트는 Docker 이후 `compose up -d --no-build --wait`를 실행합니다. EBS-backed Docker data-root의 SQLite metadata, source/work data, artifacts 및 image store와 프로젝트 `.env`/`.secrets`를 함께 보존해야 합니다. runtime writable layer/tmpfs는 사용자 데이터 영속 저장소가 아닙니다.

## 용량과 범위

업로드 50MiB, 소스 200MiB, 파일 10,000개, 산출물 100MiB, 배포 로그 2MiB, 작업 tmpfs 1GiB, npm 임시/cache tmpfs 256MiB입니다. 단일 빌드 1 CPU/2GiB/256 PID/600초이며 소스 검사/빌드 시작 전 남은 디스크를 검사합니다. 소스 snapshot은 재배포에 필요하므로 프로젝트 생존 동안 유지합니다. 사용하지 않은 검사 snapshot은 현재 수동 보관 정책이며 [SECURITY.md](SECURITY.md)에 한계를 적었습니다.

참고: [Vite 정적 배포](https://vite.dev/guide/static-deploy), [Docker Engine 보안](https://docs.docker.com/engine/security/), [Vercel 운영 롤백 경험](https://vercel.com/docs/instant-rollback). 사용자 사이트는 `vite dev/preview`가 아닌 gateway가 제공합니다.

Node image 입력은 500MiB/100,000 entries, HTTP body 2MiB/response 50MiB로 제한합니다. 이미지 보존에는 설치 전체 quota/TTL이 없으므로 디스크 관찰이 필요합니다. npm 바이너리용 내부 symlink만 안전한 직접 대상을 허용하며 외부·중첩 chain·hardlink는 거부합니다. 표준 입력 조건을 넘는 프로젝트는 자동 수정하지 않습니다.
