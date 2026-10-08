"""Generate original Java samples using checked-in, official wrapper files.

Wrapper downloads are an explicit --download-wrappers maintainer step; ordinary
setup/samples.py uses the committed samples without network or host Java.
"""
import argparse
import io
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'samples'
GRADLE = '''plugins {
    id 'java'
    id 'org.springframework.boot' version '4.0.5'
    id 'io.spring.dependency-management' version '1.1.7'
}
group = 'example'
version = '1.0.0'
java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }
repositories { mavenCentral() }
dependencies { implementation 'org.springframework.boot:spring-boot-starter-web' }
'''
POM = '''<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>4.0.5</version><relativePath/></parent>
  <groupId>example</groupId><artifactId>campus-api</artifactId><version>1.0.0</version>
  <properties><java.version>21</java.version></properties>
  <dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies>
  <build><plugins><plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build>
</project>
'''
APP = '''package example;
import java.util.Map;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.*;

@SpringBootApplication
@RestController
public class Application {
    private final String version;
    public Application(@Value("${demo.version:v1}") String version,
                       @Value("${demo.fail:false}") boolean fail) {
        if (fail) throw new IllegalStateException("deliberate sample runtime failure");
        this.version = version;
    }
    public static void main(String[] args) { SpringApplication.run(Application.class, args); }
    @GetMapping("/api/hello") public Map<String,String> hello() { return Map.of("message", "Campus Spring", "version", version); }
    @PostMapping("/api/echo") public Map<String,String> echo(@RequestBody Map<String,String> body) { return body; }
}
'''


def write(folder, rel, text):
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, 'utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download-wrappers', action='store_true')
    args = parser.parse_args()
    gradle, maven = BASE / 'spring-gradle-api', BASE / 'spring-maven-api'
    for sample in (gradle, maven):
        write(sample, 'src/main/java/example/Application.java', APP)
        write(sample, 'src/main/resources/application.properties', 'demo.version=v1\n')
    write(gradle, 'build.gradle', GRADLE)
    write(gradle, 'settings.gradle', "rootProject.name = 'campus-api'\n")
    write(maven, 'pom.xml', POM)
    properties = gradle / 'gradle/wrapper/gradle-wrapper.properties'
    checksum_lines = [line for line in properties.read_text().splitlines() if line.startswith('distributionSha256Sum=')] if properties.exists() else []
    write(gradle, 'gradle/wrapper/gradle-wrapper.properties', 'distributionUrl=https\\://services.gradle.org/distributions/gradle-9.4.1-bin.zip\nnetworkTimeout=120000\n' + (checksum_lines[0]+'\n' if checksum_lines and not args.download_wrappers else ''))
    write(maven, '.mvn/wrapper/maven-wrapper.properties', 'wrapperVersion=3.3.4\ndistributionType=only-script\ndistributionUrl=https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.9/apache-maven-3.9.9-bin.zip\n')
    if args.download_wrappers:
        for folder, rel, url in (
            (gradle, 'gradlew', 'https://raw.githubusercontent.com/gradle/gradle/v9.4.1/gradlew'),
            (gradle, 'gradle/wrapper/gradle-wrapper.jar', 'https://raw.githubusercontent.com/gradle/gradle/v9.4.1/gradle/wrapper/gradle-wrapper.jar'),
        ):
            with urllib.request.urlopen(url, timeout=60) as response:
                (folder / rel).write_bytes(response.read())
        with urllib.request.urlopen('https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper-distribution/3.3.4/maven-wrapper-distribution-3.3.4-only-script.zip', timeout=60) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                (maven / 'mvnw').write_bytes(archive.read('mvnw'))
        with urllib.request.urlopen('https://services.gradle.org/distributions/gradle-9.4.1-bin.zip.sha256', timeout=60) as response:
            checksum = response.read().decode().strip()
        with (gradle / 'gradle/wrapper/gradle-wrapper.properties').open('a', encoding='utf-8') as out:
            out.write('distributionSha256Sum=' + checksum + '\n')
    web = BASE / 'spring-gradle-web'
    shutil.copytree(gradle, web, dirs_exist_ok=True)
    write(web, 'src/main/resources/static/index.html', '<!doctype html><html lang="ko"><meta charset="utf-8"><title>Campus Spring JAR</title><h1>JAR 안에서 제공하는 웹사이트</h1><button id="hello">서버에 인사하기</button><p id="result"></p><script src="/app.js"></script></html>')
    write(web, 'src/main/resources/static/app.js', 'document.querySelector("#hello").onclick=async()=>{const r=await fetch("/api/hello");document.querySelector("#result").textContent=(await r.json()).message;};')
    missing = BASE / 'spring-no-wrapper'
    write(missing, 'build.gradle', GRADLE)
    write(missing, 'src/main/java/example/Application.java', APP)
    multi = BASE / 'spring-multimodule'
    write(multi, 'build.gradle', GRADLE)
    write(multi, 'settings.gradle', "include 'api'\n")
    write(multi, 'src/main/java/example/Application.java', APP)
    ambiguous = BASE / 'spring-ambiguous-jars'
    shutil.copytree(gradle, ambiguous, dirs_exist_ok=True)
    write(ambiguous, 'build.gradle', GRADLE + '''
tasks.register('duplicateJar', Copy) {
    from tasks.bootJar.archiveFile
    into layout.buildDirectory.dir('libs')
    rename { 'second-application.jar' }
}
tasks.bootJar.finalizedBy tasks.duplicateJar
''')
    managed = BASE / 'spring-gradle-services'
    shutil.copytree(gradle, managed, dirs_exist_ok=True)
    write(managed, 'build.gradle', GRADLE + '''
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-jdbc'
    implementation 'org.springframework.boot:spring-boot-starter-data-redis'
    runtimeOnly 'com.mysql:mysql-connector-j'
}
''')
    write(managed, 'campus-deploy.yaml', '''version: 1
runtime: spring-boot
root: .
java: 21
buildTool: gradle
artifact: build/libs/*.jar
port: 8080
health: {path: /api/health}
services:
  mysql: {enabled: true}
  redis: {enabled: true}
  storage: {enabled: true, mountPath: /app/state}
''')
    write(managed, 'src/main/java/example/Application.java', '''package example;
import java.util.Map;
import java.nio.file.*;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.bind.annotation.*;
@SpringBootApplication
@RestController
public class Application {
    private final JdbcTemplate db;
    private final StringRedisTemplate redis;
    private final Path file = Path.of("/app/state/value.txt");
    public Application(JdbcTemplate db, StringRedisTemplate redis, @Value("${demo.fail:false}") boolean fail) {
        if (fail) throw new IllegalStateException("deliberate managed runtime failure");
        this.db=db; this.redis=redis;
        // Explicit application-owned sample schema, not platform source rewriting or migration.
        db.execute("CREATE TABLE IF NOT EXISTS campus_demo (id INT PRIMARY KEY, value_text VARCHAR(200))");
    }
    public static void main(String[] args) { SpringApplication.run(Application.class, args); }
    @GetMapping("/api/health") public Map<String,Object> health() {
        return Map.of("mysql",db.queryForObject("SELECT 1",Integer.class),"redis",redis.getConnectionFactory().getConnection().ping());
    }
    @PostMapping("/api/value") public Map<String,String> write(@RequestBody Map<String,String> input) throws Exception {
        String value=input.get("value");
        db.update("INSERT INTO campus_demo(id,value_text) VALUES(1,?) ON DUPLICATE KEY UPDATE value_text=?",value,value);
        redis.opsForValue().set("campus-demo",value);
        Files.writeString(file,value);
        return read();
    }
    @GetMapping("/api/value") public Map<String,String> read() throws Exception {
        return Map.of("mysql",db.queryForObject("SELECT value_text FROM campus_demo WHERE id=1",String.class),
                      "redis",redis.opsForValue().get("campus-demo"),"storage",Files.readString(file));
    }
}
''')
    write(BASE / 'spring-aurashop-regression', 'README.md', 'Regression source: https://github.com/Gandalem/aurashop.git\nRun scripts/spring_smoke.py, frontend/e2e/spring.spec.ts and scripts/spring_lifecycle.py.\nSource SHA is captured in each verification result; no copy of upstream code is committed here.\n')
    print('Spring Java sample sources prepared.')


if __name__ == '__main__':
    main()
