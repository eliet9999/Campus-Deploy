# 승인 후 단일 EC2 시연 절차 — 이번 실행에서는 미실행

이 문서는 운영자용 절차입니다. **AWS 자원 생성·변경·보안그룹 개방·비용 발생 작업을 이번 실행에서 수행하지 않았습니다.** 계정/리전/예산/시연 종료 시간을 정한 운영자가 향후 적용하는 절차입니다. GitHub 코드 업로드와 AWS 사이트 배포는 별개입니다.

## 1. 운영자 준비

Spring 확장에서는 Java runtime 외에 프로젝트별 MySQL/Redis 메모리와 영속 저장소가 추가됩니다. 기존 Node 시연 사양을 많은 Spring 프로젝트의 용량 보장으로 해석하지 마세요. GitHub 소스 배포는 로컬에서 검증하며 AWS 생성/변경은 실행하지 않습니다. 데이터/복구 계약은 [SPRING_BOOT.md](SPRING_BOOT.md)를 참조하세요.

단일 **Ubuntu x86_64, 2 vCPU / 8GiB RAM / 40–60GiB EBS**를 시연 시작 사양으로 권장합니다. Docker Engine/Compose, Python 3.12+/venv를 준비합니다. 빌드 한 건이 최대 2GiB, runtime 하나가 최대 512MiB를 사용할 수 있으므로 프로젝트 수와 실측 부하에 따라 증설합니다. 무료 사용을 보장하지 않습니다. 1 CPU/2GiB는 builder 제한이며 VM 전체 최소 사양이 아닙니다.

업로드 소스, npm cache, 산출물, Docker 이미지의 디스크 사용량을 고려합니다. EBS를 넉넉히 준비하고 실제 여유를 doctor로 확인합니다. VM에 불필요한 IAM 역할·AWS access key를 넣지 않습니다. IMDSv2 요구와 hop limit/호스트 egress 방화벽을 검토합니다. 현재 빌드 egress는 metadata 차단을 보장하지 않습니다. [AWS IMDS 안내](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html)

승인된 운영자 IP에서만 SSH 22를 허용합니다. 외부 시연이 필요한 경우에만 시험 사용자 IP 범위에서 gateway 8080 접근을 허용합니다. **3000, DB, 2375/2376 Docker 관리 포트는 열지 않습니다.** 보안그룹 변경은 반드시 별도 승인 범위 안에서만 합니다.

## 2. 설치와 설정

SSH/SCP로 이 전용 프로젝트를 복사합니다. `.secrets`, `.env`, `.data`, node_modules, `.venv`, 테스트 증적은 다른 장비로 자동 복사하지 않습니다. 새 호스트에서 `bash scripts/setup.sh`로 암호와 키를 새로 생성합니다. SSH host key를 확인하며 확인 절차나 인증서 검증을 끄지 않습니다.

운영자가 확인한 **실제 공인 IPv4**를 `.env`에 반영합니다. 아래는 문서용 주소 예시이며 실서비스 주소가 아닙니다.

```dotenv
ADMIN_ORIGIN=http://localhost:3000
BASE_DOMAIN=203.0.113.10.sslip.io
SITE_SCHEME=http
SITE_PORT=8080
SITE_BIND=0.0.0.0
INSTANCE_ID=campus-deploy-demo-unique
```

`sslip.io`는 IP를 포함한 이름을 해석하는 **외부 DNS 서비스**입니다. 자체 DNS, 보장된 서비스, 자동 HTTPS가 아닙니다. 접속한 네트워크의 DNS 정책이나 공급자 상태에 따라 실패할 수 있으므로 실제 조회 결과를 확인합니다. [서비스 설명](https://sslip.io/)

```bash
bash scripts/start.sh
bash scripts/doctor.sh
sudo bash scripts/install-systemd.sh
getent ahostsv4 p-demo.203.0.113.10.sslip.io
```

초기 암호는 운영자가 암호 파일을 안전하게 확인하여 사용합니다. 공개 HTTP 페이지/명령 출력/저장소에 암호를 올리지 않습니다. 이 시연 모드에서는 무민감 샘플 사이트만 제공합니다.

## 3. 관리 접근은 SSH 터널

운영자 PC에서 (개인키 경로·실제 공인 IP 사용):

```bash
ssh -i /path/to/operator-key.pem -N -L 127.0.0.1:3000:127.0.0.1:3000 ubuntu@PUBLIC_IP
```

로컬 브라우저의 `http://localhost:3000`으로 로그인합니다. **공인 IP:3000으로 로그인하지 않습니다.** 관리 Origin은 이 주소와 정확히 같아야 합니다. 시험 사용자가 관리 UI에 접근해야 한다면 운영자가 준비한 제한된 터널 세션을 사용합니다. 일반 시험 사용자의 사이트 열기에 AWS/SSH/Dockerfile/Terraform은 필요 없습니다. [AWS SSH 연결 안내](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-connect-methods.html)

## 4. 반드시 분리해 기록할 검증

| 검사 위치 | 확인할 사실 |
|---|---|
| EC2 내부 | Compose health, worker, SQLite 지속성, Host를 지정한 gateway HTML·자원 응답 |
| 운영자 PC 터널 | 로그인/ZIP/Git/Preview/운영 반영/롤백 UI |
| EC2 외부의 별도 네트워크 브라우저 | 실제 sslip.io DNS, 공인 IP:8080 접근, CSS/JS와 클릭, `/about` 새로고침 |
| 다른 기기 | 동일 고정 URL에서 v1→v2→롤백 결과, 사용한 네트워크·브라우저·시각 |

예시 네트워크 진단:

```bash
nslookup p-demo.PUBLIC_IP.sslip.io
curl -I http://p-demo.PUBLIC_IP.sslip.io:8080/
curl -I http://p-demo.PUBLIC_IP.sslip.io:8080/assets/ACTUAL_FILE.js
```

`PUBLIC_IP`와 asset 이름은 실제 값으로 바꿉니다. HTTP 200만으로 브라우저 동작 성공이라고 적지 않습니다. VM 내부 성공을 외부 성공으로 대체하지 않습니다. DNS 성공과 보안그룹/방화벽/포트 성공은 별개입니다. 브라우저 콘솔·network failure·스크린샷을 `VERIFICATION.md`의 **외부 검증**에 추가합니다.

## 5. EBS 영속성과 자동 stop/start 복구

고정 주소가 필요한 시연은 운영자가 할당한 **Elastic IP**를 연결하고 해당 IP를 BASE_DOMAIN/DNS에 사용합니다. Elastic IP는 stop/start 후에도 유지됩니다. 자동 할당 공인 IPv4는 바뀔 수 있으므로 추가 명령 없는 주소 복구 조건을 충족하지 못합니다. 이 경우 운영자가 BASE_DOMAIN·DNS·터널 대상을 갱신해야 합니다. [AWS Elastic IP](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html), [AWS stop/start](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html)

보존 대상은 다음과 같습니다. Docker data-root를 instance store나 임시 디스크로 옮기지 마세요.

| EBS에 보존할 항목 | 내용 |
|---|---|
| Docker volume `campus-deploy_metadata` | SQLite DB/WAL, 프로젝트·배포·큐·포인터·전환·로그 |
| `campus-deploy_artifacts` | 기존 STATIC/VITE 산출물 |
| `campus-deploy_data` | 검증된 source snapshots, uploads/work, `services/<project-id>.json` DB/Redis/JWT 자격증명 |
| `<instance>-<project-id>-mysql-data`, `-redis-data`, `-uploads-data` | Spring 프로젝트별 MySQL·Redis·사용자 업로드 영속 데이터 |
| Docker image store (data-root 및 사용하는 경우 `/var/lib/containerd`) | 플랫폼·Node/Java/Gradle/MySQL/Redis base·배포별 immutable 이미지 |
| 설치 디렉터리 `.env`, `.secrets` | 도메인/instance 설정, 관리자 해시·서명키 |

최초 설치 때 start.sh로 이미지를 준비한 뒤 systemd 설치 스크립트를 **한 번** 실행합니다. 스크립트는 `/etc/systemd/system/campus-deploy.service`를 설치·검사·enable하고 Docker 시작 이후 Compose를 실행합니다. 기존의 무관한 동명 unit은 덮어쓰지 않습니다. 부팅할 때 GitHub/npm을 다시 설치하지 않습니다.

설계상 부팅 복구 순서는 Docker → systemd Compose → API/gateway/worker → lease 복구 → 보존 image로 Production runtime 생성 → HTTP health check입니다. Spring은 먼저 MySQL/Redis 컨테이너를 기존 볼륨/자격증명으로 복구하고 `/api/health`(aurashop `/api/products`)를 검사합니다. 정적 Production은 보존 artifact를 제공합니다. image/EBS/자격증명 누락 시 UNAVAILABLE/503이며 정상 READY라고 표시하지 않습니다. 시작·lease 만료·DB/HTTP health 지연 동안 접속 중단이 발생할 수 있습니다. 실제 EC2 부팅 검증은 미실행입니다.

중단 당시 RUNNING 빌드/소스 검사/운영 전환은 실패로 마감하며 기존 운영 포인터는 보존합니다. QUEUED 작업은 계속 실행하고 미완료 삭제는 재시도합니다. EBS/SQLite/image store의 복구 일관성을 위해 OS 정상 종료와 보존 정책을 사용하고, 백업 시 worker를 포함한 플랫폼을 멈춘 일관된 snapshot을 계획합니다. 강제 정지·디스크 손실의 완전 복구는 보장하지 않습니다.

향후 실제 EC2에서 수행할 검증: v1 정적+Node 운영과 v2 Preview 준비 → 정상 stop → start → 동일 URL의 응답, `systemctl status campus-deploy`, worker recovery 로그 확인. 이미지 ID와 build log가 이전과 같은지 확인합니다. **이번 실행은 로컬 전체 Compose stop/start와 운영 runtime 삭제 후 재생성만 검증했으며, 실제 Ubuntu systemd 부팅과 EC2 stop/start는 미실행입니다.**

## 6. 종료·비용·정리 체크리스트

- 시연 중에는 실제 리전/인스턴스 시간, EBS 크기·종류, 네트워크 전송, 공인 IPv4/Elastic IP 비용을 확인합니다. 고정 금액이나 무료를 약속하지 않습니다.
- stop과 terminate는 다릅니다. stop 후에도 EBS, 주소, 스냅샷 등 잔존 자원과 과금 가능성을 확인합니다.
- 사용 중이거나 idle인 public IPv4/Elastic IP에도 과금될 수 있으므로 EC2를 stop해도 EBS와 주소 비용 일부는 남습니다. 정확한 요율/리전/크레딧은 실제 계정에서 확인합니다. [AWS public IPv4 요금](https://aws.amazon.com/vpc/pricing/)
- 운영자가 보존할 증적과 데이터/암호의 보관 정책을 결정한 후 `bash scripts/stop.sh`로 플랫폼을 멈춥니다. 이 명령은 영속 데이터를 지우지 않습니다.
- 폐기 승인 후에만 EC2 terminate를 실행합니다. 루트/추가 EBS의 DeleteOnTermination 상태, 분리된 볼륨, 할당 주소, 스냅샷, 임시 보안그룹을 각각 확인합니다.
- 계정의 실제 자원 목록과 비용 화면에서 잔존 자원을 대조하고 종료 시각·처리자·남겨 둔 항목을 기록합니다. 다른 프로젝트의 자원을 일괄 삭제하지 않습니다.

이번 결과: **EC2 설치, AWS 보안그룹 변경, sslip.io 실제 공인 IP 조회, 다른 장비/네트워크 접속, AWS 비용 확인은 미검증입니다.**
