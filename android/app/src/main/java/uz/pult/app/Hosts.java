package uz.pult.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * The list of computers to connect to.
 *
 * Each entry stores several addresses for a computer, not one. The
 * reason is speed: on the same Wi-Fi a local address is several times
 * faster than the tunnel, while from another network only the tunnel
 * works. Which one works right now cannot be known in advance, so the
 * app tries them all before connecting ({@link Reach}).
 *
 * The certificate fingerprint lives here too: on a local network the
 * agent uses a self-signed certificate and the browser warns every
 * time. The app asks once and remembers - after that the connection is
 * quiet and, in fact, safer than in a browser: only that exact
 * certificate is accepted.
 */
public class Hosts {

    public static class Host {
        /** The agent's own id. It survives an address change. */
        public String id = "";
        public String name = "";
        public String url = "";        // the last address that worked
        public String token = "";
        public String pin = "";        // the certificate SHA-256 fingerprint (hex)
        /** Every known address: the local IPs and the tunnel address. */
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

        /** The full address handed to the browser (the last working one). */
        public String fullUrl() {
            return fullUrl(url);
        }

        /** The full link built on a given address. */
        public String fullUrl(String base) {
            while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
            return base + "/#k=" + Uri.encode(token);
        }

        /**
         * The addresses to try, fastest first.
         *
         * Local addresses come first: if they work, the tunnel should
         * not be used at all - it detours the traffic through
         * Cloudflare and that slows things down noticeably.
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

        /** Adds a new address to the list, avoiding duplicates. */
        public void learn(String u) {
            if (u == null || u.isEmpty()) return;
            while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
            if (!urls.contains(u)) urls.add(u);
        }
    }

    /**
     * Tells whether an address is on the local network (and so fast).
     *
     * Only private IPv4 ranges count. A domain name means a tunnel or
     * some other external address.
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
            // A corrupt list must not stop the app from starting
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

    /** Finds the entry by address and updates its fingerprint. */
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
     * Called after a successful connection: it stores which address
     * worked and the other addresses the agent advertised.
     *
     * The tunnel address is new on every start, so the list has to be
     * refreshed on every connection - otherwise the phone would keep
     * trying a stale address forever.
     */
    public void remember(String hostId, String working, List<String> advertised) {
        List<Host> list = all();
        for (Host h : list) {
            if (!matches(h, hostId, working)) continue;
            if (!hostId.isEmpty()) h.id = hostId;
            h.url = strip(working);
            if (advertised != null && !advertised.isEmpty()) {
                // The agent's list is the source of truth: stale
                // addresses are dropped and the working one stays
                // first.
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
            // One computer must not be added twice. Its address may
            // have changed (the tunnel gives a new one every time), so
            // we compare by key first - that is the computer's stable
            // mark.
            boolean same = (!h.id.isEmpty() && h.id.equals(old.id))
                    || (!h.token.isEmpty() && h.token.equals(old.token))
                    || sameServer(old.url, h.url);
            if (!same) continue;

            if (h.pin.isEmpty()) h.pin = old.pin;
            if (h.id.isEmpty()) h.id = old.id;
            if (h.name.isEmpty()) h.name = old.name;
            // The old addresses are kept: even when the new link is the
            // tunnel's, the local address will be needed again.
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

    /** Whether two addresses mean the same server (scheme, host, port). */
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
     * Builds a computer entry out of a link.
     *
     * The expected shape is https://192.168.1.5:8787/#k=KEY
     * The key is deliberately in the fragment (after the "#"): it is
     * never sent to the server and cannot end up in web server logs.
     */
    public static Host parse(String link) {
        if (link == null) return null;
        link = link.trim();
        if (link.isEmpty()) return null;

        // The app link: pult://add?u=<encoded address>
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
                // The computer's id: this is what says which entry to
                // update when the address changes
                else if (part.startsWith("h=")) id = Uri.decode(part.substring(2));
            }
        }
        // A key in the query string is accepted too
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
