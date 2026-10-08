# 적용한 통제와 한계

이 플랫폼은 **신뢰된 팀 소스의 통제된 시연**을 위한 STATIC/VITE_STATIC, NODE_SERVER, SPRING_BOOT_JAR/SPRING_BOOT_VITE 구현입니다. 악성 코드에 대한 완전한 격리, 불특정 다수 SaaS, 무중단·CDN·Vercel 동등 성능을 보장하지 않습니다.

Spring의 프로젝트 DB/Redis 격리, 영속 데이터/비밀 관리, 공유 Preview·코드 롤백 한계는 [SPRING_BOOT.md](SPRING_BOOT.md)를 참조하세요. Node 프리셋의 기존 DB/Redis 금지 계약은 유지합니다. Spring 지원이 사용자 앱의 권한/결제/비즈니스 로직 보안성을 보증하지는 않습니다.

## 적용한 통제

- Spring manifest는 16KiB, 허용된 선언 키/타입, 상대 root·제한된 JAR 패턴·HTTP 경로만 받습니다. YAML tag/anchor/alias/중복키, traversal, host mount, 자유 형식 build/start 명령을 거부합니다. 실제 Wrapper와 build script는 사용자 코드로 취급해 제한된 builder 안에서만 실행합니다. 일반 JAR은 소스 보정을 하지 않습니다.
- `.mvn`에서 Wrapper properties/JAR/downloader와 maven.config/jvm.config만 보존합니다. settings.xml·자격증명 파일·임의 dotfile은 소스 snapshot에 포함하지 않습니다. Maven Wrapper 실행 배포판은 임시 작업 볼륨에 두며 runtime 이미지에는 포함하지 않습니다.
- Spring의 명시 health는 2xx, 일반 JAR의 기본 `/`는 2xx/404 HTTP 연결만 확인합니다. UI·로그에 정책을 표시하며 실제 앱 기능/DB 정합성 검사로 표현하지 않습니다. 프로젝트 DB는 Preview/Production 공유이고 코드 롤백은 DB 복원이 아닙니다.

- 단일 계정, Argon2 암호 해시, itsdangerous 서명 + 서버 DB의 만료/폐기 가능한 세션. 쿠키는 host-only, HttpOnly, SameSite Strict입니다. HTTPS 관리 origin이면 Secure도 설정합니다. 로컬 HTTP에서는 TLS가 없으므로 Secure를 켜지 않습니다.
- 로그인 포함 모든 변경 요청에 정확한 관리 Origin 검사, 인증된 변경 요청에 세션 CSRF 토큰 검사. 10회/5분/IP 로그인 시도 제한은 SQLite에 유지됩니다. 프록시 전달 헤더를 신뢰하지 않습니다.
- 관리 listener는 127.0.0.1:3000, 기본 사이트 listener도 127.0.0.1:8080입니다. gateway Host 검증과 관리 Host 검증을 별도로 적용합니다. CORS를 열지 않습니다. 사이트 Host의 `/api`는 관리 API를 반환하지 않습니다.
- 공개 github.com HTTPS root URL만 허용합니다. credentials, port, fragment, query, 내부 주소, ssh/file protocol, 옵션처럼 시작하는 owner를 거부합니다. 고정 HTTPS API/codeload origin에서 redirects 없이 읽습니다. PAT·GitHub OAuth는 저장하지 않습니다.
- ZIP/tar 경로에서 절대 경로, traversal, 역슬래시/드라이브/ADS, Windows 장치명, 중복 경로, symlink/hardlink/특수 파일, 과도한 깊이/길이를 거부합니다. ZIP 압축 해제 전 정보와 실제 읽은 byte 누적 모두 검사합니다.
- `.git`, 모든 dotfile(따라서 `.env*`/`.npmrc`), node_modules, 대표 개인키 확장자/이름, 플랫폼 DB 파일은 빌드 입력/정적 산출물에서 제외합니다. 로그는 실제 stdout/stderr의 제한된 기록이며 React가 텍스트로 출력합니다.
- 빌드 컨테이너는 uid/gid 1000, read-only root, 1 CPU, 2GiB memory+swap, 256 PID, cap-drop ALL, no-new-privileges, 작업 tmpfs 용량 제한, timeout을 적용합니다. 호스트·다른 배포 폴더·관리 비밀·SSH/AWS 자격증명·socket은 전달하지 않습니다.
- 빌드는 기본 Docker `bridge`에만 연결합니다. Compose 관리 네트워크에 붙이지 않습니다. 전송은 Docker SDK archive/명명된 볼륨이며 사용자 입력으로 shell 명령을 조립하지 않습니다. npm script 자체는 컨테이너 안에서 실행됩니다.
- worker만 socket을 받습니다. API·gateway·builder는 받지 않습니다. 컨테이너와 볼륨에는 installation/project/deployment 소유 라벨을 붙입니다. 정리는 해당 라벨/검증된 자식 디렉터리만 대상으로 합니다.
- gateway DB·산출물은 read-only mount입니다. 준비 파일은 public 디렉터리로 원자 이동합니다. 실패/취소/재시작 시 기존 운영을 보존합니다. 산출물 경로 대신 배포 ID만 DB에서 신뢰합니다.

## P1 Node 통제

- builder의 npm 설치/선택적 build만 사용자 코드를 실행합니다. 이미지 조립은 플랫폼의 고정 Dockerfile로 수행하며 RUN과 사용자 Dockerfile 해석은 없습니다. Docker 이미지 생성은 검증된 tar의 COPY 단계만 수행합니다.
- runtime tar는 host filesystem에 extract하지 않습니다. 절대/상위/중복 경로, hardlink·장치·FIFO, 외부/dangling/chained symlink와 symlink 하위 entry를 거부합니다. npm `.bin`에 필요한 내부 직접 symlink는 허용하며 새 TarInfo로 owner/mode를 정규화합니다. source ZIP과 정적 artifact의 링크 금지 정책은 그대로입니다.
- runtime은 uid/gid 1000, read-only root, 0.5 CPU/512MiB/128 PID, cap-drop ALL/no-new-privileges, 64MiB noexec tmpfs입니다. Docker socket, host bind, AWS credentials, SSH key, 관리 비밀을 전달하지 않으며 host port를 publish하지 않습니다.
- runtime 전용 internal network는 일반 외부 egress 경로를 제공하지 않습니다. gateway만 관리/runtime 양쪽 network에 있고 API·worker·builder는 runtime network에 붙이지 않습니다. gateway는 검증된 32자리 배포 ID에서만 upstream authority를 만듭니다. URL/Host/헤더를 통해 관리 API를 upstream으로 선택할 수 없습니다.
- HTTP proxy는 Connection이 지정한 헤더를 포함한 hop-by-hop 헤더를 제거하고 외부 Forwarded/X-Forwarded 값을 다시 구성합니다. 중복 Set-Cookie는 유지합니다. Node의 `/api`는 사용자 앱 API이며 관리 endpoint는 아닙니다. WebSocket은 426으로 거부합니다.
- 실행 상태와 역사적 READY를 구분합니다. promote/rollback은 health check 이후 포인터를 변경합니다. 복구 실패는 UNAVAILABLE/503, 오류 로그로 나타내며 포인터·image ID를 보존합니다. startup 실패를 정상 READY로 표시하지 않습니다.
- 이미지·runtime 정리는 instance/project/deployment/source SHA label을 검사합니다. 같은 이름을 다른 소유자가 사용하는 경우 실패시키며 임의 자원을 강제 제거하지 않습니다. 프로젝트 삭제 외에는 READY 이미지를 자동 삭제하지 않습니다.

## 남아 있는 경계

- Docker socket을 가진 worker 침해는 호스트 침해로 이어질 수 있습니다. worker/API/gateway 프로세스는 현재 컨테이너 root이며 cap-drop/read-only root를 적용했습니다. build/runtime은 비root입니다.
- build 네트워크의 **egress/내부망/클라우드 metadata 차단을 별도 방화벽으로 구현하지 않았습니다**. Docker bridge 분리만으로 169.254.169.254, 호스트, 인터넷의 모든 접근을 막지 못합니다. VM에 불필요한 IAM 역할을 부여하지 말고 운영자가 IMDSv2, hop limit, egress 정책을 검토해야 합니다. 여기서 AWS 설정은 변경하지 않았습니다.
- npm registry 다운로드와 GitHub API/codeload, 시연 시 sslip.io에 외부 의존성이 있습니다. 인증서 검증을 끄지 않습니다. 외부 서비스 가용성/rate limit을 보장하지 않습니다.
- tmpfs 작업 공간은 hard size bound이지만 보존 소스·DB·artifact 볼륨에는 프로젝트별 disk quota가 없습니다. 검사하지 않고 남겨 둔 소스 snapshot의 TTL 정리/사용자 quota도 아직 없습니다. 인증된 운영자만 입력하고 디스크를 관찰해야 합니다. 소스/산출물 단건 제한과 여유 공간 검사는 적용했습니다.
- 코드에 직접 적은 비밀을 완전히 탐지하지 않습니다. 알려진 파일명을 제외하는 것과 비밀 보호 보장은 다릅니다. npm script가 스스로 stdout에 쓴 비밀도 로그에 남을 수 있습니다. 무민감 샘플만 사용하세요.
- cache no-store는 앱이 직접 설치한 service worker/외부 캐시까지 통제하지 않습니다. P0 샘플은 service worker를 사용하지 않습니다.
- 공급망 취약점 전수 감사, 악성 Docker/kernel 탈출 테스트, 다중 사용자 권한 격리, 장시간 부하/가용성 시험은 하지 않았습니다. SQLite API와 worker는 한 호스트의 동일 볼륨을 전제로 합니다.
- runtime들은 하나의 내부 network를 공유하므로 서로의 HTTP 서버와 gateway에 접근할 수 있습니다. 이 설계는 악성 다중 tenant 격리 환경이 아닙니다. 장시간/대량 동시 HTTP 부하의 설치 전체 제한은 별도 필요합니다. 응답 한도 초과 시 stream이 종료되며 SSE/긴 연결은 보장하지 않습니다.
- health는 HTTP 응답과 process 생존 검사이며 전체 사용자 기능의 정합성을 증명하지 않습니다. Node의 DB/worker 미지원은 알려진 dependency 검사와 실행 계약이며 동적 import·하드코딩된 외부 의존성을 모두 분석하지는 않습니다. readonly filesystem과 network 조건에 맞지 않는 앱은 실패하며 일반 소스를 자동 수정하지 않습니다.
- runtime 로그는 별도 2MiB 저장 한도와 Docker 2MiB 회전 한도가 있습니다. poll 사이의 폭주(최근 1,000줄 초과), daemon 장애, 회전으로 과거 로그가 유실될 수 있습니다. 완전한 감사 로그 시스템은 아닙니다. image/소스 보존 전체 quota가 없으므로 장기 운영에는 별도 용량 정책이 필요합니다.
- Gateway 검사는 대표 로컬 자원 HTTP 검증입니다. 모든 경로·상호작용을 대신 검증하지 않습니다. 제공 React/STATIC 샘플은 별도로 Playwright 클릭/새로고침/콘솔/네트워크를 검사했습니다.
- Windows NTFS 권한은 setup.ps1에서 `.secrets`에 현재 사용자와 SYSTEM만 허용하도록 설정합니다. Linux는 디렉터리 700, 파일 600입니다. 저장소에는 비밀을 포함하지 않습니다. 스크린샷/로그 증적에 시험용 소스 URL·SHA는 포함될 수 있습니다.

공용 HTTP에서는 관리 로그인/API를 노출하지 않습니다. 운영자 접근은 SSH 터널을 사용합니다. P1 자동 HTTPS/웹훅을 이 보안 경계의 대안으로 구현하지 않았습니다.
