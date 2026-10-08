# 진행 상황

## P1 (2026-10-08, Asia/Seoul)

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
