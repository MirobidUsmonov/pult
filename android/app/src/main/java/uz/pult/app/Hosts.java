package uz.pult.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Ulanadigan kompyuterlar ro'yxati.
 *
 * Har bir yozuvda manzil, kalit va sertifikat izi saqlanadi. Sertifikat
 * izi eng muhimi: agent o'z-o'zini imzolagan sertifikat ishlatadi va
 * brauzer har safar ogohlantiradi. Ilova esa uni bir marta so'rab
 * eslab qoladi - keyin ulanish jim va, aslida, brauzerdagidan
 * xavfsizroq bo'ladi: faqat aynan o'sha sertifikat qabul qilinadi.
 */
public class Hosts {

    public static class Host {
        public String name = "";
        public String url = "";        // https://ip:port
        public String token = "";
        public String pin = "";        // sertifikat SHA-256 izi (hex)

        public String display() {
            if (!name.isEmpty()) return name;
            try {
                Uri u = Uri.parse(url);
                return u.getHost() == null ? url : u.getHost();
            } catch (Exception e) {
                return url;
            }
        }

        /** Brauzerga beriladigan to'liq manzil. */
        public String fullUrl() {
            String base = url;
            while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
            return base + "/#k=" + Uri.encode(token);
        }
    }

    private static final String PREFS = "pult";
    private static final String KEY = "hosts";

    private final SharedPreferences prefs;

    public Hosts(Context ctx) {
        prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public List<Host> all() {
        List<Host> out = new ArrayList<>();
        try {
            JSONArray arr = new JSONArray(prefs.getString(KEY, "[]"));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.getJSONObject(i);
                Host h = new Host();
                h.name = o.optString("name", "");
                h.url = o.optString("url", "");
                h.token = o.optString("token", "");
                h.pin = o.optString("pin", "");
                if (!h.url.isEmpty()) out.add(h);
            }
        } catch (Exception ignored) {
            // Buzilgan ro'yxat ilovani ishga tushirmay qo'ymasligi kerak
        }
        return out;
    }

    public void save(List<Host> hosts) {
        JSONArray arr = new JSONArray();
        try {
            for (Host h : hosts) {
                JSONObject o = new JSONObject();
                o.put("name", h.name);
                o.put("url", h.url);
                o.put("token", h.token);
                o.put("pin", h.pin);
                arr.put(o);
            }
        } catch (Exception ignored) {
        }
        prefs.edit().putString(KEY, arr.toString()).apply();
    }

    /** Manzil bo'yicha topib, sertifikat izini yangilaydi. */
    public void rememberPin(String url, String pin) {
        List<Host> list = all();
        for (Host h : list) {
            if (sameServer(h.url, url)) {
                h.pin = pin;
                save(list);
                return;
            }
        }
    }

    public Host find(String url) {
        for (Host h : all()) {
            if (sameServer(h.url, url)) return h;
        }
        return null;
    }

    public void add(Host h) {
        List<Host> list = all();
        for (int i = 0; i < list.size(); i++) {
            if (sameServer(list.get(i).url, h.url)) {
                // Bir xil kompyuter qayta qo'shilsa - yangilaymiz.
                // Yangi kalit kelgan bo'lsa eskisi ishlamaydi, shuning
                // uchun ustiga yozish to'g'ri xatti-harakat.
                if (h.pin.isEmpty()) h.pin = list.get(i).pin;
                list.set(i, h);
                save(list);
                return;
            }
        }
        list.add(h);
        save(list);
    }

    public void remove(String url) {
        List<Host> list = all();
        for (int i = 0; i < list.size(); i++) {
            if (sameServer(list.get(i).url, url)) {
                list.remove(i);
                break;
            }
        }
        save(list);
    }

    /** Ikkita manzil bitta serverni bildiradimi (sxema, host, port). */
    public static boolean sameServer(String a, String b) {
        try {
            Uri x = Uri.parse(a), y = Uri.parse(b);
            return eq(x.getScheme(), y.getScheme())
                    && eq(x.getHost(), y.getHost())
                    && port(x) == port(y);
        } catch (Exception e) {
            return a.equals(b);
        }
    }

    private static boolean eq(String a, String b) {
        return a == null ? b == null : a.equalsIgnoreCase(b);
    }

    private static int port(Uri u) {
        int p = u.getPort();
        if (p != -1) return p;
        return "http".equalsIgnoreCase(u.getScheme()) ? 80 : 443;
    }

    /**
     * Havoladan kompyuter yozuvini yasaydi.
     *
     * Kutilgan ko'rinish: https://192.168.1.5:8787/#k=KALIT
     * Kalit ataylab fragmentda ("#" dan keyin): u serverga yuborilmaydi
     * va veb-server loglarida qolib ketmaydi.
     */
    public static Host parse(String link) {
        if (link == null) return null;
        link = link.trim();
        if (link.isEmpty()) return null;

        // Ilova havolasi: pult://ochish?u=<kodlangan manzil>
        if (link.startsWith("pult://")) {
            try {
                Uri u = Uri.parse(link);
                String inner = u.getQueryParameter("u");
                if (inner != null && !inner.isEmpty()) link = inner;
            } catch (Exception ignored) {
            }
        }

        if (!link.startsWith("http://") && !link.startsWith("https://")) {
            link = "https://" + link;
        }

        String token = "";
        int hash = link.indexOf('#');
        if (hash >= 0) {
            String frag = link.substring(hash + 1);
            link = link.substring(0, hash);
            for (String part : frag.split("&")) {
                if (part.startsWith("k=")) token = Uri.decode(part.substring(2));
            }
        }
        // Kalit so'rov qismida bo'lsa ham qabul qilamiz
        try {
            Uri u = Uri.parse(link);
            if (token.isEmpty()) {
                String q = u.getQueryParameter("k");
                if (q != null) token = q;
            }
            if (u.getHost() == null || u.getHost().isEmpty()) return null;

            Host h = new Host();
            int port = u.getPort();
            h.url = u.getScheme() + "://" + u.getHost() + (port == -1 ? "" : ":" + port);
            h.token = token;
            return h;
        } catch (Exception e) {
            return null;
        }
    }
}
