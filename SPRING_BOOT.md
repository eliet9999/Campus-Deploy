# Spring Boot 배포 계약과 운영

사용자 승인(2026-10-08)으로 aurashop을 실행할 수 있게 P1의 Java/DB/Redis 제외 범위를 확장했습니다. Python/Maven/일반 monorepo/사용자 Dockerfile 실행은 여전히 미지원입니다.

## 소스와 빌드

- 루트 단일 Spring Boot Gradle, Java 21, `frontend/`의 Vite/npm lockfile v2/v3. 일반 레시피는 `/api/health`가 공개 2xx를 응답해야 합니다. aurashop은 `/api/products`로 앱 및 DB 연결을 검사합니다.
- GitHub 실제 기본 branch의 SHA를 고정한 snapshot을 읽습니다. 제출 Dockerfile/Compose/gradlew를 실행하지 않습니다. Gradle build script와 npm scripts는 각각 제한된 비root builder에서 실행합니다.
- frontend는 `npm ci && npm run build`, `VITE_API_BASE_URL=/api`. backend는 고정 Gradle 9.4.1/JDK 21 이미지에서 `bootJar -x test`를 수행합니다. 앱 자체 테스트 실행을 생략하는 빌드 계약이며 플랫폼 테스트 통과와 혼동하지 않습니다.
- 일반 파일 app.jar 하나만 검증된 tar context로 포장합니다. 플랫폼 Dockerfile은 고정 Java base image ID/COPY/USER/CMD만 포함하고 RUN이 없습니다. 최종 실행은 배포별 image ID에 고정됩니다. 프런트 파일은 기존 immutable artifact 경로에 저장됩니다.
- Gradle 의존성은 저장소 선언을 따르며 Gradle dependency lock을 강제하지 않습니다. 같은 SHA 재빌드의 byte 동일성은 보장하지 않으며, 운영 롤백은 재빌드 없이 보존 image ID를 사용합니다.
- aurashop-v1 레시피는 작업 복사본만 보정합니다: API 접두사 혼용/localhost refresh를 같은 origin으로 정규화, 업로드 상대 경로 반환, 무작위 재생성 JWT key를 프로젝트별 영속 key로 변경. 원본 SHA는 보존하고 파일별 변경 내용을 settings/build 로그에 기록합니다. 예상 파일 패턴이 달라지면 중단합니다. 일반 Spring 앱에는 이 보정을 적용하지 않습니다.

## 서비스와 보안 경계

Java는 uid/gid 1000, read-only root, cap-drop ALL, no-new-privileges, 0.5 CPU/1GiB/128 PID입니다. 쓰기는 `/tmp`와 프로젝트 uploads 명명 볼륨만 가능합니다. host port/bind/socket/관리 비밀은 전달하지 않습니다.

MySQL 8.4.8(768MiB), Redis 7.4.8(192MiB)은 각각 0.5 CPU/128 PID/nonroot/read-only root로 실행되며 공개 포트가 없습니다. 프로젝트 전용 internal data network에는 해당 프로젝트 Java runtime만 추가됩니다. API/worker/gateway/builder는 연결되지 않습니다. Java는 기존 gateway용 runtime network에도 연결됩니다. 기존 공유 runtime network의 다중 tenant 격리 한계는 유지됩니다.

고정 운영자 이미지의 짧은 초기화 helper만 network none, CHOWN/FOWNER로 볼륨 루트 uid를 설정합니다. Docker volume no_copy로 이미지의 기본 uid가 초기화 권한을 덮어쓰지 않게 합니다. 소유 instance/project/kind label이 다르면 기존 컨테이너/볼륨/network를 사용하거나 삭제하지 않습니다.

생성한 DB/Redis 암호와 JWT key, 최초 서비스 image ID는 `/data/services/<project-id>.json`(600)에 원자 저장합니다. gateway가 받는 metadata/artifacts에는 없으며 관리 API에 반환하지 않습니다. 앱에는 전용 database 한 개의 계정만 주고 MySQL root 암호는 주지 않습니다. 로그에서 생성된 비밀의 정확한 문자열은 마스킹합니다. 앱이 변형/인코딩한 비밀 유출까지 막는 기능은 아닙니다.

## 데이터·Preview·롤백

**Preview와 운영은 프로젝트 DB·Redis·uploads를 공유합니다.** Preview에서 작성한 데이터도 운영에 보입니다. 코드 롤백은 보존 이미지와 현재 DB를 연결하며 데이터의 과거 시점 복원이 아닙니다.

처음 Production이 없는 경우 Hibernate `update`, 이후 `validate`를 주입합니다. 새 버전 배포가 기존 운영 schema를 자동 변경하지 않지만 앱 자체 SQL/사용자 요청에 의한 쓰기를 격리하지는 않습니다. 스키마 변경이 필요한 소스는 validate 실패로 표시됩니다. migration 자동화·DB snapshot/복원·프로젝트 disk quota는 미지원입니다.

재배포/코드 rollback/서비스 재시작은 프로젝트 데이터를 보존합니다. 실패/취소도 데이터 볼륨을 지우지 않습니다. 프로젝트 삭제만 서비스·볼륨·network·비밀을 영구 제거하며 화면에 명시적으로 표시합니다. MySQL/Redis 서비스는 실패 후 재시도용으로 남을 수 있습니다.

## 복구와 백업

worker는 시작 시 누락된 MySQL/Redis 컨테이너를 기존 볼륨/자격증명/서비스 image ID로 복원하고 SELECT 1 및 인증된 PING을 확인합니다. 보존 Java image와 uploads로 runtime을 시작하여 API health 후 RUNNING으로 표시합니다. 실패 시 UNAVAILABLE/503이며 원래 production pointer와 데이터를 유지합니다. `scripts/manage.py stop`은 이 설치의 runtime/MySQL/Redis만 정지합니다.

운영 백업에는 SQLite/소스/artifacts, Docker 이미지, 세 개 서비스 볼륨, `/data/services` 자격증명을 함께 포함해야 합니다. 볼륨만 남기고 자격증명을 잃으면 자동 복구할 수 없습니다. Docker data-root와 모든 볼륨을 영속 디스크에 보존하세요. 자동 백업/복원과 DB 엔진 업그레이드는 구현하지 않았습니다. 기본 이미지 tag 변경만으로 기존 서비스 image ID가 바뀌지는 않습니다.

## 한계와 검증

gateway HTTP body 2MiB/response 50MiB 한도를 적용하므로 원래 aurashop의 100MB 업로드 설정보다 제한적입니다. 외부 CDN·주소 검색·결제 등은 네트워크/서비스 제공자에 의존합니다. 실제 결제/메일 발송/공인 HTTPS 운영은 검증 범위 밖입니다. 앱 권한 및 비즈니스 로직은 원본 앱 책임입니다.

재현: `scripts/spring_smoke.py` → frontend `npm run test:e2e` → `scripts/spring_lifecycle.py`. 마지막 스크립트는 로컬 Campus Deploy 서비스를 잠시 멈추고 브라우저 검증용 별도 프로젝트를 삭제합니다. 실제 결과와 실패/수정 이력은 [VERIFICATION.md](VERIFICATION.md)를 참조하세요. AWS 자원은 변경하지 않았습니다.
