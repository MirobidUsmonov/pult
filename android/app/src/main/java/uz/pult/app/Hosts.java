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
 * Har bir yozuvda kompyuterning bir nechta manzili saqlanadi, bitta
 * emas. Sababi tezlik: bitta Wi-Fi ichida mahalliy manzil tunneldan
 * bir necha barobar tez, boshqa tarmoqdan esa faqat tunnel ishlaydi.
 * Qaysi biri hozir ishlashini oldindan bilib bo'lmaydi, shuning uchun
 * ilova ulanish oldidan hammasini tekshirib ko'radi ({@link Reach}).
 *
 * Sertifikat izi ham shu yerda: agent mahalliy tarmoqda o'z-o'zini
 * imzolagan sertifikat ishlatadi va brauzer har safar ogohlantiradi.
 * Ilova uni bir marta so'rab eslab qoladi - keyin ulanish jim va,
 * aslida, brauzerdagidan xavfsizroq bo'ladi: faqat aynan o'sha
 * sertifikat qabul qilinadi.
 */
public class Hosts {

    public static class Host {
        /** Agentning o'z raqami. Manzil o'zgarsa ham shu o'zgarmaydi. */
        public String id = "";
        public String name = "";
        public String url = "";        // oxirgi ishlagan manzil
        public String token = "";
        public String pin = "";        // sertifikat SHA-256 izi (hex)
        /** Ma'lum barcha manzillar: mahalliy IP'lar va tunnel manzili. */
        public List<String> urls = new ArrayList<>();

        public String display() {
            if (!name.isEmpty()) return name;
            try {
                Uri u = Uri.parse(url);
                return u.getHost() == null ? url : u.getHost();
            } catch (Exception e) {
                return url;
            }
        }

        /** Brauzerga beriladigan to'liq manzil (oxirgi ishlagani). */
        public String fullUrl() {
            return fullUrl(url);
        }

        /** Berilgan manzil asosida to'liq havola. */
        public String fullUrl(String base) {
            while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
            return base + "/#k=" + Uri.encode(token);
        }

        /**
         * Tekshiriladigan manzillar, tez-sekin tartibida.
         *
         * Mahalliy manzillar oldinda turadi: ular ishlasa tunnelni
         * umuman ishlatmaslik kerak - tunnel trafikni Cloudflare
         * orqali aylantiradi va bu sezilarli sekinlashtiradi.
         */
        public List<String> candidates() {
            List<String> all = new ArrayList<>();
            for (String u : urls) {
                if (u != null && !u.isEmpty() && !all.contains(u)) all.add(u);
            }
            if (!url.isEmpty() && !all.contains(url)) all.add(url);

            List<String> lan = new ArrayList<>();
            List<String> rest = new ArrayList<>();
            for (String u : all) {
                if (isLocal(u)) lan.add(u); else rest.add(u);
            }
            lan.addAll(rest);
            return lan;
        }

        /** Yangi manzilni ro'yxatga qo'shadi (takrorlanmasin). */
        public void learn(String u) {
            if (u == null || u.isEmpty()) return;
            while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
            if (!urls.contains(u)) urls.add(u);
        }
    }

    /**
     * Manzil mahalliy tarmoqniki (ya'ni tez) ekanini aniqlaydi.
     *
     * Faqat xususiy IPv4 oraliqlari hisobga olinadi. Domen nomi
     * bo'lsa - bu tunnel yoki tashqi manzil.
     */
    public static boolean isLocal(String url) {
        try {
            String host = Uri.parse(url).getHost();
            if (host == null) return false;
            if (host.equals("localhost") || host.startsWith("127.")) return true;
            String[] p = host.split("\\.");
            if (p.length != 4) return false;
            int a = Integer.parseInt(p[0]), b = Integer.parseInt(p[1]);
            if (a == 10) return true;
            if (a == 192 && b == 168) return true;
            if (a == 172 && b >= 16 && b <= 31) return true;
            if (a == 169 && b == 254) return true;
            return false;
        } catch (Exception e) {
            return false;
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
                h.id = o.optString("id", "");
                h.name = o.optString("name", "");
                h.url = o.optString("url", "");
                h.token = o.optString("token", "");
                h.pin = o.optString("pin", "");
                JSONArray us = o.optJSONArray("urls");
                if (us != null) {
                    for (int k = 0; k < us.length(); k++) h.learn(us.optString(k, ""));
                }
                if (!h.url.isEmpty()) {
                    h.learn(h.url);
                    out.add(h);
                }
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
                o.put("id", h.id);
                o.put("name", h.name);
                o.put("url", h.url);
                o.put("token", h.token);
                o.put("pin", h.pin);
                JSONArray us = new JSONArray();
                for (String u : h.urls) us.put(u);
                o.put("urls", us);
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
            if (h.urls.contains(strip(url)) || sameServer(h.url, url)) {
                h.pin = pin;
                save(list);
                return;
            }
        }
    }

    /**
     * Ulanish muvaffaqiyatli bo'lgach chaqiriladi: qaysi manzil
     * ishlagani va agent aytgan boshqa manzillar saqlanadi.
     *
     * Tunnel manzili har ishga tushganda yangi bo'ladi, shuning uchun
     * ro'yxatni har ulanishda yangilab turish shart - aks holda
     * telefon eskirgan manzilni cheksiz sinab yurardi.
     */
    public void remember(String hostId, String working, List<String> advertised) {
        List<Host> list = all();
        for (Host h : list) {
            if (!matches(h, hostId, working)) continue;
            if (!hostId.isEmpty()) h.id = hostId;
            h.url = strip(working);
            if (advertised != null && !advertised.isEmpty()) {
                // Agent aytgan ro'yxat haqiqatning manbai: eskirgan
                // manzillar o'chadi, ishlagani esa oldinda qoladi.
                List<String> fresh = new ArrayList<>();
                fresh.add(h.url);
                for (String u : advertised) {
                    String s = strip(u);
                    if (!s.isEmpty() && !fresh.contains(s)) fresh.add(s);
                }
                h.urls = fresh;
            } else {
                h.learn(h.url);
            }
            save(list);
            return;
        }
    }

    private static boolean matches(Host h, String hostId, String url) {
        if (!hostId.isEmpty() && !h.id.isEmpty()) return h.id.equals(hostId);
        return h.urls.contains(strip(url)) || sameServer(h.url, url);
    }

    static String strip(String u) {
        if (u == null) return "";
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        return u;
    }

    public Host find(String url) {
        for (Host h : all()) {
            if (h.urls.contains(strip(url)) || sameServer(h.url, url)) return h;
        }
        return null;
    }

    public void add(Host h) {
        List<Host> list = all();
        for (int i = 0; i < list.size(); i++) {
            Host old = list.get(i);
            // Bitta kompyuter ikki marta qo'shilmasin. Manzil o'zgargan
            // bo'lishi mumkin (tunnel har safar yangi manzil beradi),
            // shuning uchun avval kalit bo'yicha solishtiramiz - u
            // kompyuterning o'zgarmas belgisi.
            boolean same = (!h.id.isEmpty() && h.id.equals(old.id))
                    || (!h.token.isEmpty() && h.token.equals(old.token))
                    || sameServer(old.url, h.url);
            if (!same) continue;

            if (h.pin.isEmpty()) h.pin = old.pin;
            if (h.id.isEmpty()) h.id = old.id;
            if (h.name.isEmpty()) h.name = old.name;
            // Eski manzillar saqlanadi: yangi havola tunnelniki bo'lsa
            // ham mahalliy manzil keyin yana kerak bo'ladi.
            for (String u : old.urls) h.learn(u);
            h.learn(h.url);
            list.set(i, h);
            save(list);
            return;
        }
        h.learn(h.url);
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
        String id = "";
        int hash = link.indexOf('#');
        if (hash >= 0) {
            String frag = link.substring(hash + 1);
            link = link.substring(0, hash);
            for (String part : frag.split("&")) {
                if (part.startsWith("k=")) token = Uri.decode(part.substring(2));
                // Kompyuter raqami: manzil o'zgarganda ham qaysi yozuvni
                // yangilash kerakligini shu aytadi
                else if (part.startsWith("h=")) id = Uri.decode(part.substring(2));
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
            h.id = id;
            h.learn(h.url);
            return h;
        } catch (Exception e) {
            return null;
        }
    }
}
