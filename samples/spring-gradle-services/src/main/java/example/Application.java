package example;
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
