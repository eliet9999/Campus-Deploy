# Spring Boot 배포 계약과 운영

Spring Boot 지원은 `SPRING_BOOT_JAR`와 `SPRING_BOOT_VITE`로 나뉩니다. 기존 `SPRING_BOOT` 기록·보존 이미지·프로젝트 데이터는 Vite 호환 경로로 계속 읽습니다. 지원 조건이 모든 Spring 프로젝트의 실행을 보증하는 것은 아닙니다. 실제 결과는 [VERIFICATION.md](VERIFICATION.md)에 별도 기록합니다.

## 소스와 빌드

- `SPRING_BOOT_JAR`: Java 21, 단일 모듈 Spring Boot, `src/main`, Gradle 또는 Maven Wrapper. frontend/package.json/npm lockfile은 필요하지 않습니다. JAR 내부 정적 웹 리소스·서버 렌더링과 API는 같은 HTTP 프록시를 사용합니다. 이번 실제 웹 샘플은 JAR 내부 정적 HTML/JS입니다.
- Gradle은 `build.gradle` 또는 `build.gradle.kts`, Spring Boot plugin, `gradlew`, `gradle/wrapper/gradle-wrapper.jar`와 properties가 필요합니다. `./gradlew bootJar --no-daemon --max-workers=2 -Dorg.gradle.jvmargs=-Xmx1024m`를 실행합니다.
- Maven은 `pom.xml`, Spring Boot plugin/dependency, `mvnw`, `.mvn/wrapper/maven-wrapper.properties`가 필요합니다. `./mvnw -DskipTests package`를 실행합니다. repackage 등 실행 JAR 설정은 소스가 제공해야 합니다. only-script/JAR 방식의 Wrapper 파일을 snapshot에 보존합니다.
- `SPRING_BOOT_VITE`: 기존 Gradle/Java 21 + 별도 Vite/npm lockfile v2/v3 구조입니다. frontend root는 Vite 의존성으로 찾으며 폴더명 목록으로 추측하지 않습니다. `npm ci && npm run build`, `VITE_API_BASE_URL=/api`, backend는 기존 고정 Gradle 9.4.1/JDK 21의 `gradle --no-daemon --max-workers=2 -Dorg.gradle.jvmargs=-Xmx1024m bootJar -x test`를 사용합니다. 이 호환 경로에서는 Wrapper를 실행하지 않습니다. Maven+별도 Vite는 이번 범위가 아닙니다.
- GitHub 실제 기본 branch의 SHA를 고정한 snapshot을 읽습니다. 사용자 Dockerfile/Compose는 실행하지 않습니다. Wrapper/build script/npm script는 제한된 비root builder 안에서만 실행합니다. 일반 JAR 프로필은 소스나 application.yml을 수정하지 않습니다. Maven/Vite에서 앱 자체 테스트를 생략하는 빌드 계약과 플랫폼 테스트 통과를 구분합니다.
- `build/libs` 또는 `target` archive에서 artifact 패턴을 검사합니다. Gradle `*-plain.jar`는 제외하고 Boot Main-Class/Start-Class, loader class, BOOT-INF/classes를 가진 실행 JAR 정확히 하나만 선택합니다. 0개/2개 이상이면 오류와 manifest artifact 지정 안내를 제공합니다. 선택 경로와 JAR SHA-256도 기록합니다.
- 일반 파일 app.jar 하나만 검증한 tar context로 포장합니다. 플랫폼 Dockerfile은 고정 JRE base image ID/COPY/USER/CMD만 포함하고 RUN이 없습니다. 앱 runtime에는 JRE와 실행 JAR만 들어갑니다. 실제 실행은 배포별 image ID에 고정됩니다. Vite 프런트는 기존 immutable artifact 경로에 저장합니다.
- Gradle 의존성은 저장소 선언을 따르며 Gradle dependency lock을 강제하지 않습니다. 같은 SHA 재빌드의 byte 동일성은 보장하지 않으며, 운영 롤백은 재빌드 없이 보존 image ID를 사용합니다.
- 기존 `spring_adapters.aurashop_v1`은 Vite 계약의 별도 호환 모듈입니다. 기존 ProductController 소스 구조가 있는 Vite 앱에만 적용하며 두 번째 저장소 이름/URL 기반 처리는 추가하지 않았습니다. 작업 복사본의 API prefix/localhost refresh·업로드 URL·JWT key만 기존 방식대로 보정합니다. 원본 SHA와 변경 목록을 기록하고 예상 패턴이 다르면 중단합니다. 일반 JAR 프로필은 adapter가 none이며 소스 보정을 하지 않습니다.

## 분석 facts와 배포 plan

`spring_plan.analyze`는 Spring Boot 여부, backend root 후보, Gradle/Maven, Wrapper 유무/누락 파일, multi-module/WAR, frontend root/Vite/lockfile, MySQL·Redis·Actuator 의존성 hint를 각각 기록합니다. `plan`은 facts와 검증한 manifest로 프로필·root·build tool·artifact·health·서비스를 확정합니다.

결과는 `sources.analysis`/`sources.plan`에 JSON으로 저장하고 API에서 반환합니다. 배포 시 `deployments.settings`로 복사해 소스 SHA와 연결합니다. 빌드 직전 보존 소스를 다시 분석하고 계획이 달라지면 중단합니다. UI에 root·빌드 도구·JAR 패턴·health 정책·활성 서비스·힌트를 표시합니다.

저장소 루트의 흔한 단일 모듈은 manifest 없이 감지합니다. 중첩 root, 여러 backend/build tool, 여러 frontend로 모호하면 manifest를 요구합니다. 선언된 Gradle/Maven 멀티모듈은 하위 root 지정으로 우회할 수 없습니다.

## 선택적인 campus-deploy.yaml

저장소 루트에 둡니다. root/frontend.root는 저장소 기준이고 artifact는 backend root 기준입니다.

```yaml
version: 1
runtime: spring-boot
profile: SPRING_BOOT_JAR  # 생략 가능; JAR 또는 VITE
root: .
java: 21
buildTool: gradle        # gradle 또는 maven
artifact: build/libs/*.jar  # Maven은 target/*.jar
port: 8080
health:
  path: /api/hello
services:
  mysql:
    enabled: false
  redis:
    enabled: false
  storage:
    enabled: false
    mountPath: /app/uploads
```

Vite root 선택은 `profile: SPRING_BOOT_VITE`, `frontend: {root: web}`으로 지정합니다. 안전한 상대 root와 출력 디렉터리 안의 제한된 JAR glob만 허용합니다. 자유 build/start command, 알 수 없는 키, traversal/절대 root/host 경로, YAML tag/anchor/alias/중복 키는 거부합니다. 파일은 16KiB 이하입니다. storage mount는 `/app/` 아래이며 실행 JAR을 가릴 수 없습니다. 실제 대상은 항상 프로젝트 소유 명명 볼륨입니다.

## 상태 확인 정책

| 조건 | 정책 |
|---|---|
| manifest health.path 지정 | 해당 경로의 2xx 필요; redirect/401/403/404/5xx는 미통과 |
| 기존 Vite 프로필 | `/api/health` 또는 기존 aurashop `/api/products`의 2xx |
| JAR, health 미지정 | `/`의 2xx 또는 404로 HTTP 연결만 확인; UI·로그·validation에 http-reachable 표시 |

API-only 앱은 정상 실행 중에도 `/`가 404일 수 있습니다. 기본 연결 확인은 API/DB 기능 검증이 아닙니다. 실제 health 경로는 manifest로 지정합니다. Actuator 의존성은 hint로만 기록하며 보안 설정/노출을 확정하지 못하므로 자동 선택하지 않습니다. `/actuator/health`를 명시할 수 있습니다.

컨테이너 종료(exit), gateway/upstream 연결 실패, 응답 timeout, HTTP 상태와 전체 120초 deadline을 구분해 기록합니다. gateway는 upstream 연결 실패 502, timeout 504를 반환합니다. health 성공 전 READY/운영 포인터를 변경하지 않습니다. Vite health `/`는 frontend와 충돌하므로 거부합니다.

## 서비스와 보안 경계

Java는 uid/gid 1000, read-only root, cap-drop ALL, no-new-privileges, 0.5 CPU/1GiB/128 PID입니다. 쓰기는 `/tmp`와 선택한 프로젝트 storage 명명 볼륨만 가능합니다. host port/bind/socket/관리 비밀은 전달하지 않습니다. SERVER_PORT=8080/SERVER_ADDRESS=0.0.0.0을 주입합니다. 일반 JAR의 모든 HTTP 경로는 기존 gateway를 통해 Java로 전달합니다.

일반 JAR은 manifest에서 활성화한 서비스만 만듭니다. 의존성 hint만으로 서비스를 생성하지 않습니다. Vite는 기존 계약의 MySQL·Redis·storage 기본값을 유지하고 manifest로 개별 선택을 덮어쓸 수 있습니다. 서비스 없는 JAR에는 DB 암호·data network·volume을 생성하지 않습니다. storage만 선택하면 data network도 만들지 않습니다. 선택 서비스의 Spring 표준 SPRING_DATASOURCE_*/SPRING_DATA_REDIS_*만 주입합니다.

MySQL 8.4.8(768MiB), Redis 7.4.8(192MiB)은 각각 0.5 CPU/128 PID/nonroot/read-only root로 실행되며 공개 포트가 없습니다. 프로젝트 전용 internal data network에는 해당 프로젝트 Java runtime만 추가됩니다. API/worker/gateway/builder는 연결되지 않습니다. Java는 기존 gateway용 runtime network에도 연결됩니다. 기존 공유 runtime network의 다중 tenant 격리 한계는 유지됩니다.

고정 운영자 이미지의 짧은 초기화 helper만 network none, CHOWN/FOWNER로 볼륨 루트 uid를 설정합니다. Docker volume no_copy로 이미지의 기본 uid가 초기화 권한을 덮어쓰지 않게 합니다. 소유 instance/project/kind label이 다르면 기존 컨테이너/볼륨/network를 사용하거나 삭제하지 않습니다.

생성한 DB/Redis 암호와 JWT key, 최초 서비스 image ID는 `/data/services/<project-id>.json`(600)에 원자 저장합니다. gateway가 받는 metadata/artifacts에는 없으며 관리 API에 반환하지 않습니다. 앱에는 전용 database 한 개의 계정만 주고 MySQL root 암호는 주지 않습니다. 로그에서 생성된 비밀의 정확한 문자열은 마스킹합니다. 앱이 변형/인코딩한 비밀 유출까지 막는 기능은 아닙니다.

## 데이터·Preview·롤백

**Preview와 운영은 프로젝트 DB·Redis·uploads를 공유합니다.** Preview에서 작성한 데이터도 운영에 보입니다. 코드 롤백은 보존 이미지와 현재 DB를 연결하며 데이터의 과거 시점 복원이 아닙니다.

기존 Vite는 처음 Production이 없는 경우 Hibernate `update`, 이후 `validate`를 유지합니다. 일반 JAR의 MySQL은 항상 `validate`이며 스키마 초기화는 앱 운영자가 준비해야 합니다. 앱 자체 SQL/사용자 요청에 의한 쓰기를 격리하지는 않습니다. migration 자동화·DB snapshot/복원·프로젝트 disk quota는 미지원입니다.

재배포/코드 rollback/서비스 재시작은 프로젝트 데이터를 보존합니다. 실패/취소도 데이터 볼륨을 지우지 않습니다. 프로젝트 삭제만 서비스·볼륨·network·비밀을 영구 제거하며 화면에 명시적으로 표시합니다. MySQL/Redis 서비스는 실패 후 재시도용으로 남을 수 있습니다.

## 복구와 백업

worker는 시작 시 누락된 MySQL/Redis 컨테이너를 기존 볼륨/자격증명/서비스 image ID로 복원하고 SELECT 1 및 인증된 PING을 확인합니다. 보존 Java image와 uploads로 runtime을 시작하여 API health 후 RUNNING으로 표시합니다. 실패 시 UNAVAILABLE/503이며 원래 production pointer와 데이터를 유지합니다. `scripts/manage.py stop`은 이 설치의 runtime/MySQL/Redis만 정지합니다.

운영 백업에는 SQLite/소스/artifacts, Docker 이미지, 세 개 서비스 볼륨, `/data/services` 자격증명을 함께 포함해야 합니다. 볼륨만 남기고 자격증명을 잃으면 자동 복구할 수 없습니다. Docker data-root와 모든 볼륨을 영속 디스크에 보존하세요. 자동 백업/복원과 DB 엔진 업그레이드는 구현하지 않았습니다. 기본 이미지 tag 변경만으로 기존 서비스 image ID가 바뀌지는 않습니다.

## 한계와 검증

gateway HTTP body 2MiB/response 50MiB 한도를 적용하므로 원래 aurashop의 100MB 업로드 설정보다 제한적입니다. 외부 CDN·주소 검색·결제 등은 네트워크/서비스 제공자에 의존합니다. 실제 결제/메일 발송/공인 HTTPS 운영은 검증 범위 밖입니다. 앱 권한 및 비즈니스 로직은 원본 앱 책임입니다.

재현: `scripts/spring_smoke.py` → frontend `npm run test:e2e` → `scripts/spring_lifecycle.py`. 마지막 스크립트는 로컬 Campus Deploy 서비스를 잠시 멈추고 브라우저 검증용 별도 프로젝트를 삭제합니다. 실제 결과와 실패/수정 이력은 [VERIFICATION.md](VERIFICATION.md)를 참조하세요. AWS 자원은 변경하지 않았습니다.

일반 JAR 실제 검증은 `scripts/spring_jar_smoke.py`입니다. Gradle API/Maven API/JAR 내부 웹, Wrapper 누락/멀티모듈/복수 JAR 실패 샘플을 포함합니다. API 404 정책, 실제 API/POST/JS, Preview/운영/이미지 롤백, runtime 실패, Compose 재시작과 runtime 누락 복구, 소유 프로젝트 삭제를 검사합니다. Wrapper는 Gradle v9.4.1 공식 GitHub 파일과 Apache Maven Wrapper 3.3.4 공식 only-script 배포판에서 고정했습니다. 샘플 생성은 make_spring_samples.py, ZIP 생성은 samples.py입니다.

`spring-gradle-services`와 `scripts/spring_jar_services.py`는 일반 JAR에서 manifest로 활성화한 MySQL/Redis/storage와 `/app/state` 마운트, 실 데이터 읽기/쓰기, 실패 Preview의 데이터 보존, runtime/서비스 누락 복구, 프로젝트 삭제를 검사합니다. 플랫폼이 소스를 보정하지 않으며 샘플 자체가 명시적으로 시험 테이블을 준비합니다.

미지원: 멀티모듈, WAR, 임의 Java main JAR, Maven+별도 Vite, Python 앱, 사설 Git, 자유 build command, 사용자 Dockerfile/Compose, PostgreSQL/MongoDB/Kafka/RabbitMQ/Spring Cloud 운영, WebSocket/background worker/cron, 자동 DB 백업·migration·복원, 자동 HTTPS/webhook. **AWS 자원 생성·EC2 stop/start·Ubuntu systemd boot는 미실행입니다.** 로컬 Compose 복구 검증을 실제 AWS 검증으로 표현하지 않습니다.

참고: [Gradle Wrapper](https://docs.gradle.org/current/userguide/gradle_wrapper.html), [Apache Maven Wrapper](https://maven.apache.org/tools/wrapper/index.html), [Spring Boot 실행 JAR](https://docs.spring.io/spring-boot/3.4/specification/executable-jar/launching.html).
