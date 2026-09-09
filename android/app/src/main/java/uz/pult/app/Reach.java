package uz.pult.app;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Kompyuterga qaysi manzil orqali yetish tez ekanini aniqlaydi.
 *
 * Bitta kompyuterda bir nechta manzil bo'ladi: mahalliy tarmoq IP'lari
 * va tunnel manzili. Mahalliy manzil ancha tez - trafik router'dan
 * nariga chiqmaydi. Tunnel esa har joydan ishlaydi, lekin ma'lumot
 * Cloudflare serverlari orqali aylanib o'tadi va bu sezilarli
 * sekinlashtiradi.
 *
 * Foydalanuvchidan "qaysi tarmoqdasan?" deb so'ramaymiz - bilib
 * bo'lmaydigan narsani so'rash yomon interfeys. Buning o'rniga
 * hammasini bir vaqtda urinib ko'ramiz va birinchi javob berganini
 * olamiz. Mahalliy manzillar oldin sinaladi, shuning uchun ular
 * ishlayotgan bo'lsa tunnelga umuman navbat kelmaydi.
 */
public final class Reach {

    private static final String TAG = "PultReach";

    /** Mahalliy tarmoq javobi tez keladi - uzoq kutishning ma'nosi yo'q. */
    private static final int LAN_MS = 1500;
    /** Tunnel uzoqroq: Cloudflare'ga chiqish va qaytish vaqti qo'shiladi. */
    private static final int FAR_MS = 9000;

    private Reach() {
    }

    /** Agent haqidagi qisqa ma'lumot. */
    public static class Info {
        public String id = "";
        public String name = "";
        public List<String> addresses = new ArrayList<>();
    }

    /**
     * Ishlayotgan manzilni topadi. Hech biri javob bermasa - null.
     *
     * Tarmoq ishi bo'lgani uchun asosiy oqimdan chaqirilmaydi.
     */
    public static String pick(Hosts.Host h) {
        return pick(h, null);
    }

    /**
     * Context berilsa tarmoq turi ham hisobga olinadi: mobil
     * internetda mahalliy manzillarni sinashning ma'nosi yo'q va
     * ular har ulanishga ortiqcha kutish qo'shadi.
     */
    public static String pick(Hosts.Host h, android.content.Context ctx) {
        List<String> all = h.candidates();
        List<String> lan = new ArrayList<>();
        List<String> far = new ArrayList<>();
        for (String u : all) {
            if (Hosts.isLocal(u)) lan.add(u); else far.add(u);
        }
        if (ctx != null && !far.isEmpty() && !onLocalNetwork(ctx)) {
            Log.i(TAG, "mobil internet - mahalliy manzillar o'tkazib yuborildi");
            lan.clear();
        }

        String found = race(lan, h.id, LAN_MS);
        if (found != null) {
            Log.i(TAG, "mahalliy manzil ishladi: " + found);
            return found;
        }
        found = race(far, h.id, FAR_MS);
        if (found != null) {
            Log.i(TAG, "tashqi manzil ishladi: " + found);
            return found;
        }
        return null;
    }

    /**
     * Manzillarni bir vaqtda sinaydi va birinchi javob berganini oladi.
     *
     * Ketma-ket sinash yaramaydi: o'chgan manzil javob bermay
     * kutdiradi va har biri uchun kutish vaqti qo'shilib ketadi.
     * Bir vaqtda sinaganda esa umumiy vaqt eng sekinniki emas, eng
     * tezinikiga teng bo'ladi.
     */
    private static String race(List<String> urls, String wantId, int timeoutMs) {
        if (urls.isEmpty()) return null;
        if (urls.size() == 1) {
            return alive(urls.get(0), wantId, timeoutMs) ? urls.get(0) : null;
        }

        ExecutorService pool = Executors.newFixedThreadPool(Math.min(6, urls.size()));
        try {
            List<Callable<String>> tasks = new ArrayList<>();
            for (final String u : urls) {
                tasks.add(() -> {
                    if (alive(u, wantId, timeoutMs)) return u;
                    // invokeAny faqat xato tashlagan vazifani
                    // "muvaffaqiyatsiz" deb biladi, shuning uchun
                    // shunchaki null qaytarib bo'lmaydi
                    throw new Exception("javob yo'q: " + u);
                });
            }
            return pool.invokeAny(tasks, timeoutMs + 500L, TimeUnit.MILLISECONDS);
        } catch (Exception e) {
            return null;
        } finally {
            pool.shutdownNow();
        }
    }

    /**
     * Manzil javob beryaptimi va bu o'sha kompyutermi.
     *
     * Kalit ataylab yuborilmaydi: /api/info kalitsiz ham kompyuter
     * nomi va raqamini beradi, bu esa tanish uchun yetarli. Shu
     * sababli tekshirishda sertifikatni tekshirmaslik xavfsiz -
     * hech qanday sir uzatilmayapti.
     */
    private static boolean alive(String base, String wantId, int timeoutMs) {
        HttpURLConnection c = null;
        try {
            URL url = new URL(Hosts.strip(base) + "/api/info");
            c = (HttpURLConnection) url.openConnection();
            if (c instanceof HttpsURLConnection) {
                HttpsURLConnection s = (HttpsURLConnection) c;
                s.setSSLSocketFactory(insecureFactory());
                s.setHostnameVerifier((hostname, session) -> true);
            }
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            c.setRequestMethod("GET");
            if (c.getResponseCode() != 200) return false;
            JSONObject o = new JSONObject(read(c.getInputStream()));
            if (!o.optBoolean("ok", false)) return false;
            String id = o.optString("id", "");
            // Raqam ma'lum bo'lsa - aynan o'sha kompyuter ekaniga
            // ishonch hosil qilamiz. Boshqa tarmoqda o'sha IP'da
            // butunlay boshqa qurilma turishi mumkin.
            return wantId == null || wantId.isEmpty() || id.isEmpty() || wantId.equals(id);
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /**
     * Agentdan uning barcha manzillarini so'raydi.
     *
     * Bu yerda kalit yuboriladi, shuning uchun sertifikat haqiqatan
     * tekshiriladi: mahalliy manzil uchun saqlangan iz bo'yicha,
     * tunnel manzili uchun tizimning odatiy ishonch ro'yxati bo'yicha
     * (u yerda sertifikat haqiqiy).
     */
    public static Info info(String base, String token, String pin) {
        HttpURLConnection c = null;
        try {
            URL url = new URL(Hosts.strip(base) + "/api/info?k="
                    + android.net.Uri.encode(token));
            c = (HttpURLConnection) url.openConnection();
            if (c instanceof HttpsURLConnection && pin != null && !pin.isEmpty()) {
                HttpsURLConnection s = (HttpsURLConnection) c;
                s.setSSLSocketFactory(pinnedFactory(pin));
                // Sertifikat IP manzilga berilgani uchun nom mos
                // kelmaydi - lekin izi tekshirilgani bu yerda
                // nomdan kuchliroq kafolat.
                s.setHostnameVerifier((hostname, session) -> true);
            }
            c.setConnectTimeout(FAR_MS);
            c.setReadTimeout(FAR_MS);
            if (c.getResponseCode() != 200) return null;
            JSONObject o = new JSONObject(read(c.getInputStream()));
            Info info = new Info();
            info.id = o.optString("id", "");
            info.name = o.optString("name", "");
            JSONObject host = o.optJSONObject("host");
            JSONArray arr = host == null ? null : host.optJSONArray("addresses");
            if (arr != null) {
                for (int i = 0; i < arr.length(); i++) {
                    String u = arr.optString(i, "");
                    if (!u.isEmpty()) info.addresses.add(Hosts.strip(u));
                }
            }
            return info;
        } catch (Exception e) {
            Log.i(TAG, "ma'lumot olinmadi: " + e);
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /**
     * Telefon mahalliy tarmoqdami (Wi-Fi yoki simli).
     *
     * Aniqlab bo'lmasa "ha" deb hisoblaymiz: mahalliy manzilni
     * ortiqcha sinash eng yomoni bir yarim soniya yo'qotadi, uni
     * noto'g'ri o'tkazib yuborish esa tez ulanishdan mahrum qiladi.
     */
    private static boolean onLocalNetwork(android.content.Context ctx) {
        try {
            android.net.ConnectivityManager cm =
                    (android.net.ConnectivityManager) ctx.getSystemService(
                            android.content.Context.CONNECTIVITY_SERVICE);
            if (cm == null) return true;
            android.net.Network n = cm.getActiveNetwork();
            if (n == null) return true;
            android.net.NetworkCapabilities caps = cm.getNetworkCapabilities(n);
            if (caps == null) return true;
            return caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI)
                    || caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_ETHERNET);
        } catch (Exception e) {
            return true;
        }
    }

    // ---------------------------------------------------------- yordamchi

    private static String read(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        // Javob kichkina, lekin buzilgan server cheksiz yuborishi
        // mumkin - chegara qo'yamiz
        while (out.size() < 64 * 1024 && (n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close();
        return out.toString("UTF-8");
    }

    private static SSLSocketFactory insecure;

    /** Faqat tekshirish uchun: hech qanday sir uzatilmaydigan so'rovlarda. */
    private static synchronized SSLSocketFactory insecureFactory() throws Exception {
        if (insecure != null) return insecure;
        TrustManager[] tm = {new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] c, String a) {
            }

            public void checkServerTrusted(X509Certificate[] c, String a) {
            }

            public X509Certificate[] getAcceptedIssuers() {
                return new X509Certificate[0];
            }
        }};
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, tm, new java.security.SecureRandom());
        insecure = ctx.getSocketFactory();
        return insecure;
    }

    /** Faqat izi mos keladigan sertifikatni qabul qiladi. */
    private static SSLSocketFactory pinnedFactory(final String pin) throws Exception {
        TrustManager[] tm = {new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] c, String a) {
            }

            public void checkServerTrusted(X509Certificate[] chain, String a)
                    throws CertificateException {
                if (chain == null || chain.length == 0) {
                    throw new CertificateException("sertifikat yo'q");
                }
                try {
                    byte[] hash = MessageDigest.getInstance("SHA-256")
                            .digest(chain[0].getEncoded());
                    StringBuilder sb = new StringBuilder(hash.length * 2);
                    for (byte b : hash) sb.append(String.format("%02X", b));
                    if (!sb.toString().equalsIgnoreCase(pin)) {
                        throw new CertificateException("sertifikat izi mos kelmadi");
                    }
                } catch (CertificateException e) {
                    throw e;
                } catch (Exception e) {
                    throw new CertificateException(e);
                }
            }

            public X509Certificate[] getAcceptedIssuers() {
                return new X509Certificate[0];
            }
        }};
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, tm, new java.security.SecureRandom());
        return ctx.getSocketFactory();
    }
}
