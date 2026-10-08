# 샘플 Wrapper 출처

Java 샘플의 애플리케이션 코드는 이 저장소에서 작성했습니다. 아래 Wrapper 파일은 upstream Apache License 2.0 파일이며 원래 고지를 보존합니다. 라이선스 사본은 [APACHE-2.0.txt](APACHE-2.0.txt)에 있습니다.

| 파일 | 고정 출처 | SHA-256 |
|---|---|---|
| Gradle 샘플 gradlew | https://github.com/gradle/gradle/blob/v9.4.1/gradlew | e712dc38715e260bf046106bf3b121b0021a4b2f83e3999913e435332a60e9f6 |
| gradle/wrapper/gradle-wrapper.jar | https://github.com/gradle/gradle/blob/v9.4.1/gradle/wrapper/gradle-wrapper.jar | 55243ef57851f12b070ad14f7f5bb8302daceeebc5bce5ece5fa6edb23e1145c |
| Maven 샘플 mvnw | https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper-distribution/3.3.4/maven-wrapper-distribution-3.3.4-only-script.zip | cae96cef89ebea3531221f4ae17c23cf8edf67d00eae8306d4186ae1bbed4d02 |

Gradle 배포판 9.4.1의 공식 SHA-256은 wrapper properties에 포함합니다. Maven 샘플 배포판은 3.9.9입니다. 일반 사용자 프로젝트는 자신이 제출한 Wrapper 버전을 따릅니다. 위 checksum은 샘플 출처 확인용이며 임의 사용자 Wrapper의 신뢰성을 인증하는 allowlist가 아닙니다. Wrapper는 제한된 builder 내부에서만 실행합니다.
