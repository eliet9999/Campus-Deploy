# 진행 상황

## 현재 완료: Spring Boot 일반화 (2026-10-08)

- `SPRING_BOOT_JAR`를 추가했습니다. Java 21 단일 모듈 Gradle/Maven Wrapper 앱은 frontend 없이 실행할 수 있습니다. 기존 프런트 결합 경로는 `SPRING_BOOT_VITE`로 분리하고 저장된 `SPRING_BOOT` 배포도 유지합니다.
- 소스 facts와 배포 plan을 분리하고 선택 `campus-deploy.yaml`로 root/buildTool/artifact/health/서비스를 확정합니다. 일반 JAR 소스를 수정하지 않고 의존성 hint만으로 DB를 만들지 않습니다.
- 선택 MySQL/Redis/storage, HTTP health 정책, 불변 이미지 롤백·재시작 복구, 프로젝트 단위 데이터 보존·삭제를 구현했습니다. Preview/Production 서비스 공유와 코드 롤백의 한계를 UI·문서에 표시합니다.
- 변경 전 **pytest 94 / Chromium 5**, 변경 후 **pytest 154 / Chromium 6** 통과. 일반 JAR 수명주기 11개, 선택 서비스 4개, 기존 aurashop 수명주기 7개 실제 검사를 통과했습니다. 원인·수정·검증 범위는 [VERIFICATION.md](VERIFICATION.md)에 기록합니다.
- 실행 JAR 복수 산출물/Wrapper 누락/멀티모듈 실패 샘플을 제공하며 정상 Gradle API/Maven API/JAR 웹/서비스 샘플도 포함합니다.
- 기능·API·기술·보안 경계·실행 방법은 [FEATURE_SPEC.md](FEATURE_SPEC.md)에 정리했습니다. 아래 내용은 이전 단계의 이력입니다. 실제 AWS/EC2/Ubuntu 부팅 검증은 수행하지 않았습니다.

## 이전 Spring Boot 확장 (2026-10-08, 사용자 승인)

- SPRING_BOOT: 루트 Gradle/Java 21 + frontend/ Vite/npm lockfile, 고정 Java 빌드/이미지, 프로젝트별 MySQL·Redis·uploads 영속 저장을 추가했습니다. 사용자 Dockerfile/Compose는 실행하지 않습니다.
- aurashop-v1 배포 복사본 보정: 같은 origin API/refresh 및 업로드 주소, 혼용 API 접두사, 영속 JWT key. 원본 SHA와 보정 목록을 기록합니다.
- Preview/Production·health·image rollback·startup recovery에 Spring을 통합했습니다. DB는 프로젝트 단위 공유, 코드만 롤백, 최초 이후 schema validate입니다. 데이터 snapshot/자동 migration은 미지원입니다.
- 기존 회귀와 Spring 검사 포함 pytest 94개 PASS. 실제 Spring 운영 전환·image 롤백·컨테이너 누락 복구에서 DB/Redis/uploads/세션 보존 및 프로젝트별 삭제 격리를 확인했습니다. 검증 상세는 VERIFICATION.md에 기록합니다.

## P1 (2026-10-08, Asia/Seoul)

- GitHub 입력 개선: 새 프로젝트의 기본 탭을 공개 GitHub로 변경하고 저장소 URL에서 이름/고유 slug를 자동 입력합니다. 기존 GitHub 수집·지원 검사 API를 그대로 사용하며 ZIP은 별도 탭에서 유지합니다. 공개 GitHub 주소만으로 실제 HTTP 200까지 확인했고 Chromium 전체 4개가 통과했습니다. aurashop은 실제 Git 재검사에서도 Spring Boot/Java 미지원으로 거부되었습니다.

- STATIC/VITE_STATIC P0를 보존하며 NODE_SERVER를 구현했습니다. npm lockfile/start 자동 감지, 제한된 builder의 npm ci/build, 고정 Dockerfile을 통한 immutable image 생성, 전용 internal network의 비root runtime, gateway HTTP proxy가 연결됩니다.
- 실제 STARTING/HEALTH_CHECK, 별도 build/runtime 로그, 운영 전환 전 재검사, image 재사용 rollback, 소유 자원 보존/정리, startup reconciliation을 구현했습니다. 복구 실패는 UNAVAILABLE/503으로 표시합니다.
- 검증: 변경 전 45개 → 확장 pytest 73개 PASS; P0 HTTP smoke/robustness PASS; Chromium STATIC/Vite/Node 3개 PASS. 실제 Node 이미지·no host port·promote 실패 보존·image 누락 rollback 실패 보존·기존 image rollback·runtime 실패·서비스/전체 Compose 복구·소유 자원 격리 PASS.
- Ubuntu systemd 자동 시작 설치 스크립트와 EBS/Elastic IP/비용/보안그룹/종료 체크리스트를 제공했습니다. Bash 구문은 Linux에서 검증했습니다. AWS 변경, Ubuntu 실제 systemd boot, EC2 stop/start는 실행하지 않았습니다.
- GitHub 업로드 대상: 사용자 승인 `https://github.com/eliet9999/Campus-Deploy.git`. 비밀/운영 데이터/의존성 설치물은 제외하고 코드·샘플·문서·선별 증적을 업로드합니다.
- 추가 복구 장애 시험 PASS: 운영 이미지 누락 후 startup에서 UNAVAILABLE/503 및 정적 사이트 유지, 이미지 복원 후 추가 명령/재빌드 없이 RUNNING 자동 복구.
- 남은 위험: 신뢰된 팀용, runtime 공유 network, builder egress/metadata 차단 미구현, 전체 보존 quota/TTL 부재, 장시간 부하·공급망 전수 감사 미검증. 자세한 한계는 SECURITY.md/VERIFICATION.md.

## P0 이력

2026-10-07 (Asia/Seoul)

- 사전 확인: D:\CampusDeploy 비어 있음, 기존 Git 저장소/AGENTS.md 없음.
- 전용 폴더 campus-deploy 생성. 다른 프로젝트 변경 없음.
- Python 3.13.2, Node 24.11.1, npm 11.6.2, Docker Engine 29.8.1 Linux, Compose 5.5.1 확인.
- 3000/8080 포트 사용 없음. Docker는 sandbox 밖의 로컬 엔진 접근으로 확인.
- 1단계 완료: STATIC ZIP 입력→영속 배포→실제 gateway HTTP, 첫 통합 테스트 통과.
- 2단계 완료: Node 24 Docker SDK Vite 빌드, 실제 로그/실패 exit 7, 소유 컨테이너·tmpfs 정리 검증.
- 3단계 완료: 한국어 React UI, 로그인/CSRF, Preview/운영/롤백, 영속 로그. 실제 Chromium 2개 E2E 통과.
- 4단계 완료: 공개 GitHub 기본 브랜치/SHA, 위험 URL/ZIP/tar, 중복/단일 큐, timeout/취소/SIGKILL 복구/삭제. pytest 45개 통과, Compose 수명주기 테스트 통과.
- 5단계 완료: Windows setup/doctor/start/stop 실제 실행, Ubuntu/AWS 절차 및 시연/검증/보안 문서 제공.
- 전용 폴더에 로컬 Git 저장소 초기화. 원격 설정/커밋/푸시는 하지 않았으며 비밀·운영 데이터·설치 의존성은 gitignore로 제외.
- 현재 API/gateway/worker는 로컬 실행 중. 관리 localhost:3000, 사이트 8080. 둘 다 기본 loopback 바인딩.
- 최종 감사: SQLite integrity ok, gateway 읽기 전용/socket 분리, staging/work 비어 있음, 임시 빌드 컨테이너·볼륨 각 0. 정상 stop/start 뒤 운영·로그 보존.
- DNS: OS getaddrinfo는 초기 실패 후 최종 검사 성공. Chromium 실제 서브도메인 접속은 모든 검사 성공. 세부 증적은 VERIFICATION.md.
- AWS/외부 장비/네트워크 접속, Ubuntu 네이티브 설치는 미검증. 자동 HTTPS/webhook 등 P1은 구현하지 않음.
- 보안 한계: 신뢰된 팀 시연용, build egress/metadata 완전 차단·다중 사용자 quota 미구현. SECURITY.md 참조.
