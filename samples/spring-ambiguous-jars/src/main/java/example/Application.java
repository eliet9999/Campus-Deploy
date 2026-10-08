package example;
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
