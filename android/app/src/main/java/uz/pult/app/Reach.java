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
 * Works out which address reaches the computer fastest.
 *
 * One computer has several addresses: local network IPs and a tunnel
 * address. A local address is much faster - the traffic never leaves the
 * router. The tunnel works from anywhere, but the data detours through
 * Cloudflare's servers and that slows things down noticeably.
 *
 * We do not ask the user "which network are you on?" - asking for
 * something unknowable is bad interface design. Instead all of them are
 * tried at once and the first one to answer wins. Local addresses go
 * first, so while they work the tunnel never gets a turn.
 */
public final class Reach {

    private static final String TAG = "PultReach";

    /** A local network answers quickly - waiting longer buys nothing. */
    private static final int LAN_MS = 1500;
    /** The tunnel takes longer: the round trip to Cloudflare adds up. */
    private static final int FAR_MS = 9000;

    private Reach() {
    }

    /** A short summary about the agent. */
    public static class Info {
        public String id = "";
        public String name = "";
        public List<String> addresses = new ArrayList<>();
    }

    /**
     * Finds a working address, or null when none of them answers.
     *
     * This is network work, so it is never called from the main thread.
     */
    public static String pick(Hosts.Host h) {
        return pick(h, null);
    }

    /**
     * Given a Context, the network type is taken into account too: on
     * mobile data there is no point trying local addresses, and they
     * add a needless wait to every connection.
     */
    public static String pick(Hosts.Host h, android.content.Context ctx) {
        List<String> all = h.candidates();
        List<String> lan = new ArrayList<>();
        List<String> far = new ArrayList<>();
        for (String u : all) {
            if (Hosts.isLocal(u)) lan.add(u); else far.add(u);
        }
        if (ctx != null && !far.isEmpty() && !onLocalNetwork(ctx)) {
            Log.i(TAG, "mobile data - local addresses skipped");
            lan.clear();
        }

        String found = race(lan, h.id, LAN_MS);
        if (found != null) {
            Log.i(TAG, "a local address worked: " + found);
            return found;
        }
        found = race(far, h.id, FAR_MS);
        if (found != null) {
            Log.i(TAG, "an external address worked: " + found);
            return found;
        }
        return null;
    }

    /**
     * Tries the addresses at once and takes the first one to answer.
     *
     * Trying them in sequence does not work: a dead address simply
     * makes you wait, and the timeouts add up one after another. Tried
     * together, the total time equals the fastest one rather than the
     * slowest.
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
                    // invokeAny only counts a task that throws as
                    // "failed", so simply returning null is not an
                    // option
                    throw new Exception("no answer: " + u);
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
     * Whether the address answers, and whether it is that computer.
     *
     * The key is deliberately not sent: /api/info gives the computer's
     * name and id without one, which is enough to recognise it. That is
     * also why skipping certificate validation here is safe - no secret
     * is being sent.
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
            // With the id known, make sure it really is that computer.
            // On another network the same IP may belong to a completely
            // different device.
            return wantId == null || wantId.isEmpty() || id.isEmpty() || wantId.equals(id);
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /**
     * Asks the agent for all of its addresses.
     *
     * The key is sent here, so the certificate really is checked: for a
     * local address against the stored fingerprint, for a tunnel
     * address against the system's usual trust store (where the
     * certificate is genuine).
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
                // The certificate is issued to an IP address, so the
                // name does not match - but a checked fingerprint is a
                // stronger guarantee here than a name.
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
            Log.i(TAG, "info not fetched: " + e);
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /**
     * Whether the phone is on a local network (Wi-Fi or wired).
     *
     * When it cannot be determined we assume yes: trying a local
     * address needlessly costs a second and a half at worst, while
     * skipping it wrongly gives up the fast connection entirely.
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

    // ------------------------------------------------------------ helpers

    private static String read(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        // The answer is small, but a broken server could send forever -
        // so there is a cap
        while (out.size() < 64 * 1024 && (n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close();
        return out.toString("UTF-8");
    }

    private static SSLSocketFactory insecure;

    /** Probing only, for requests that carry no secret. */
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

    /** Accepts only the certificate whose fingerprint matches. */
    private static SSLSocketFactory pinnedFactory(final String pin) throws Exception {
        TrustManager[] tm = {new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] c, String a) {
            }

            public void checkServerTrusted(X509Certificate[] chain, String a)
                    throws CertificateException {
                if (chain == null || chain.length == 0) {
                    throw new CertificateException("no certificate");
                }
                try {
                    byte[] hash = MessageDigest.getInstance("SHA-256")
                            .digest(chain[0].getEncoded());
                    StringBuilder sb = new StringBuilder(hash.length * 2);
                    for (byte b : hash) sb.append(String.format("%02X", b));
                    if (!sb.toString().equalsIgnoreCase(pin)) {
                        throw new CertificateException("the certificate fingerprint did not match");
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
