import com.moss.mobile.AndroidRelease;
import java.util.List;

/** Run against the actual compiled AndroidRelease.class with JDK 17 source-file mode. */
class UpdatePolicyCheck {
    static int checks = 0;
    static AndroidRelease candidate(long code, long size, String url, String hash) {
        return new AndroidRelease("com.moss.mobile", "0.3.7", code, 26, url, size, hash, List.of("测试更新"));
    }
    static void expect(boolean result) { if (!result) throw new AssertionError(); checks++; }
    static void reject(Runnable action) {
        try { action.run(); } catch (IllegalArgumentException expected) { checks++; return; }
        throw new AssertionError("Unsafe metadata accepted");
    }
    public static void main(String[] args) {
        String url = "https://github.com/example/releases/download/v0.3.7/app.apk";
        String hash = "a".repeat(64);
        var newer = candidate(17, 12345, url, hash).validate();
        expect(newer.isNewerThan(16));
        expect(!newer.isNewerThan(17));
        expect(!newer.isNewerThan(18));
        reject(() -> candidate(0, 12345, url, hash).validate());
        reject(() -> candidate(2100000001L, 12345, url, hash).validate());
        reject(() -> candidate(17, 0, url, hash).validate());
        reject(() -> candidate(17, 209715201, url, hash).validate());
        reject(() -> candidate(17, 12345, url, "bad").validate());
        for (String bad : List.of("http://example.com/app.apk", "file:///tmp/app.apk",
                "https://user:secret@example.com/app.apk", "https://example.com:444/app.apk",
                "https://example.com/app.apk#fragment", "https:///app.apk", "https://example.com/\napp.apk")) {
            reject(() -> candidate(17, 12345, bad, hash).validate());
        }
        // Signed GitHub object-storage URLs are allowed; queries are required by that channel.
        candidate(17, 12345, "https://release-assets.githubusercontent.com/a?token=example", hash).validate(); checks++;
        reject(() -> new AndroidRelease("other.app", "0.3.7",17,26,url,12345,hash,List.of("更新")).validate());
        reject(() -> new AndroidRelease("com.moss.mobile", "0.3.7",17,25,url,12345,hash,List.of("更新")).validate());
        reject(() -> new AndroidRelease("com.moss.mobile", "0.3.7",17,26,url,12345,hash,List.of()).validate());
        System.out.println("PASS: " + checks + " compiled Android update policy checks");
    }
}
