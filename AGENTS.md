# Campus Deploy 구현 규칙

- 이 전용 디렉터리만 수정한다. Cloud Readiness Lab/AWS App Packager 및 기존 Docker 자원은 건드리지 않는다.
- P0 STATIC/VITE_STATIC 보존. P1 NODE_SERVER: npm lockfile + npm start, PORT=8080/0.0.0.0, DB 없는 Express HTTP 앱. React/TS 관리 UI, FastAPI control-plane, Python worker, SQLite WAL, Docker 격리 빌드와 별도 gateway.
- 먼저 STATIC 실제 입력→사이트 검증, 다음 Vite 빌드, 운영/Preview/롤백, 인증·Git·안전성·복구, 문서/리허설 순서로 작업한다.
- immutable 배포, 트랜잭션 운영 포인터, 첫 성공만 자동 운영. 실패/취소가 기존 운영에 영향을 주면 안 된다.
- 관리 127.0.0.1:3000 / 사이트 8080. worker만 Docker socket. 빌드 컨테이너에 관리 네트워크·비밀·socket 전달 금지.
- 사용자 코드 실행은 제한된 비root 일회성 컨테이너 안에서만. ZIP/tar/Host/경로/URL 검증과 소유 자원만 정리. system prune 금지.
- AWS 자원/유료 서비스 변경 금지. 사용자 승인에 따라 완성본을 https://github.com/eliet9999/Campus-Deploy.git 에 업로드한다.
- 2026-10-08 사용자 승인으로 SPRING_BOOT (Java 21/Gradle, frontend/ Vite, MySQL, Redis, uploads 영속 저장)을 추가한다. aurashop URL 실제 실행을 검증한다. Python, WebSocket, cron/worker, 사용자 Dockerfile 실행, 사설 Git, 일반 monorepo, webhook/자동 HTTPS는 범위 밖이다.
- runtime은 전용 internal network, 비root/자원 제한/no host ports. 배포 이미지 보존, health 후 운영 포인터 변경, 재빌드 없는 롤백, startup reconciliation. 다른 프로젝트 자원에 영향 금지.
- 실제 HTTP, JS/CSS, 브라우저 동작과 로그를 검증한다. 테스트 성공을 꾸미지 않고 미검증/실패를 VERIFICATION.md에 구분한다.
- 고정 의존성/lockfile, 초기화·doctor·start·stop, 샘플, pytest/E2E 및 요청된 운영 문서를 제공한다. STATUS.md를 갱신한다.
- 2026-10-08 후속 요청: 일반 SPRING_BOOT_JAR(Java 21/단일 모듈 Gradle·Maven Wrapper)와 기존 SPRING_BOOT_VITE를 구분한다. frontend 필수 조건은 Vite에만 적용한다. facts/plan과 선택 campus-deploy.yaml, 명시적 서비스, generic 소스 무수정, 코드-only rollback/프로젝트 DB 공유를 유지한다. 상세 계약은 SPRING_BOOT.md, 기능 명세는 FEATURE_SPEC.md를 따른다. 기존 SPRING_BOOT 저장 기록은 호환한다.
