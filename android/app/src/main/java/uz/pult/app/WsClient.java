package uz.pult.app;

import android.net.Uri;
import android.util.Log;

import java.io.BufferedInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.Random;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.net.SocketFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Kichik WebSocket mijozi.
 *
 * Nega qo'lda yozilgan: Android'da tayyor WebSocket mijozi faqat 13-versiyadan
 * boshlab bor, kutubxona qo'shish esa 69 KB lik ilovani bir necha megabaytga
 * shishirardi. Bizga kerak bo'lgani - qo'l siqish, matn va ikkilik kadr
 * yuborish, ping'ga javob berish. Bu RFC 6455 ning kichik bir qismi.
 *
 * Sertifikat bog'lash ham shu yerda: faqat ilova eslab qolgan sertifikat
 * qabul qilinadi, boshqasi bilan ulanish rad etiladi.
 */
public class WsClient {

    private static final String TAG = "PultWs";

    public interface Listener {
        void onOpen();
        void onText(String message);
        void onClosed(String reason);
    }

    private final String url;
    private final String pin;
    private final Listener listener;

    private Socket socket;
    private InputStream in;
    private DataOutputStream out;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final Random random = new Random();
    private Thread reader;

    public WsClient(String url, String pin, Listener listener) {
        this.url = url;
        this.pin = pin == null ? "" : pin;
        this.listener = listener;
    }

    public boolean isOpen() {
        return running.get();
    }

    public void start() {
        if (running.get()) return;
        reader = new Thread(this::run, "pult-ws");
        reader.setDaemon(true);
        reader.start();
    }

    public void close() {
        running.set(false);
        try {
            if (socket != null) socket.close();
        } catch (IOException ignored) {
        }
    }

    // ------------------------------------------------------------ ulanish

    private void run() {
        try {
            Uri u = Uri.parse(url);
            boolean secure = "wss".equalsIgnoreCase(u.getScheme())
                    || "https".equalsIgnoreCase(u.getScheme());
            int port = u.getPort();
            if (port == -1) port = secure ? 443 : 80;
            String host = u.getHost();

            SocketFactory factory = secure ? pinnedFactory() : SocketFactory.getDefault();
            socket = factory.createSocket(host, port);
            socket.setTcpNoDelay(true);          // kadrlar kechikmasdan ketsin
            socket.setSoTimeout(0);

            in = new BufferedInputStream(socket.getInputStream(), 16 * 1024);
            out = new DataOutputStream(socket.getOutputStream());

            handshake(u, host, port);
            running.set(true);
            listener.onOpen();
            readLoop();
        } catch (Exception e) {
            Log.w(TAG, "ulanish uzildi: " + e);
            running.set(false);
            listener.onClosed(String.valueOf(e.getMessage()));
        } finally {
            running.set(false);
            try {
                if (socket != null) socket.close();
            } catch (IOException ignored) {
            }
        }
    }

    /**
     * Faqat eslab qolingan sertifikatni qabul qiladigan ulanish.
     *
     * Oddiy "hammasiga ishonish" xavfli bo'lardi: mahalliy tarmoqda kimdir
     * o'zini kompyuter deb ko'rsatib, butun boshqaruvni qo'lga olishi
     * mumkin. Iz solishtirilgani uchun bunday ulanish rad etiladi.
     */
    private SSLSocketFactory pinnedFactory() throws Exception {
        final String expected = pin;
        TrustManager tm = new X509TrustManager() {
            @Override
            public void checkClientTrusted(X509Certificate[] chain, String authType) {
            }

            @Override
            public void checkServerTrusted(X509Certificate[] chain, String authType)
                    throws CertificateException {
                if (chain == null || chain.length == 0) {
                    throw new CertificateException("sertifikat yo'q");
                }
                if (expected.isEmpty()) {
                    // Iz hali saqlanmagan - ulanishga ruxsat bermaymiz.
                    // Iz WebView orqali birinchi ulanishda olinadi.
                    throw new CertificateException("sertifikat izi hali tasdiqlanmagan");
                }
                String actual = sha256(chain[0].getEncoded());
                if (!expected.equalsIgnoreCase(actual)) {
                    throw new CertificateException("sertifikat mos kelmadi");
                }
            }

            @Override
            public X509Certificate[] getAcceptedIssuers() {
                return new X509Certificate[0];
            }
        };
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, new TrustManager[]{tm}, new SecureRandom());
        return ctx.getSocketFactory();
    }

    private static String sha256(byte[] data) throws CertificateException {
        try {
            byte[] h = MessageDigest.getInstance("SHA-256").digest(data);
            StringBuilder sb = new StringBuilder(h.length * 2);
            for (byte b : h) sb.append(String.format("%02X", b));
            return sb.toString();
        } catch (Exception e) {
            throw new CertificateException(e);
        }
    }

    private void handshake(Uri u, String host, int port) throws IOException {
        byte[] nonce = new byte[16];
        random.nextBytes(nonce);
        String key = base64(nonce);

        String path = u.getPath() == null || u.getPath().isEmpty() ? "/" : u.getPath();
        if (u.getQuery() != null) path += "?" + u.getQuery();

        String req = "GET " + path + " HTTP/1.1\r\n"
                + "Host: " + host + ":" + port + "\r\n"
                + "Upgrade: websocket\r\n"
                + "Connection: Upgrade\r\n"
                + "Sec-WebSocket-Key: " + key + "\r\n"
                + "Sec-WebSocket-Version: 13\r\n"
                + "\r\n";
        out.write(req.getBytes(StandardCharsets.ISO_8859_1));
        out.flush();

        String statusLine = readLine();
        if (statusLine == null || !statusLine.contains(" 101")) {
            throw new IOException("qo'l siqish rad etildi: " + statusLine);
        }
        // Qolgan sarlavhalarni bo'sh qatorgacha o'qib tashlaymiz
        String line;
        while ((line = readLine()) != null && !line.isEmpty()) {
            // sarlavhalar bizga kerak emas
        }
    }

    private String readLine() throws IOException {
        StringBuilder sb = new StringBuilder();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\r') continue;
            if (c == '\n') return sb.toString();
            sb.append((char) c);
        }
        return null;
    }

    // ---------------------------------------------------------- o'qish

    private void readLoop() throws IOException {
        while (running.get()) {
            int b0 = in.read();
            if (b0 < 0) break;
            int b1 = in.read();
            if (b1 < 0) break;

            int opcode = b0 & 0x0F;
            boolean masked = (b1 & 0x80) != 0;
            long len = b1 & 0x7F;
            if (len == 126) {
                len = ((long) in.read() << 8) | in.read();
            } else if (len == 127) {
                len = 0;
                for (int i = 0; i < 8; i++) len = (len << 8) | in.read();
            }
            byte[] mask = null;
            if (masked) {
                mask = new byte[4];
                readFully(mask);
            }
            byte[] payload = new byte[(int) len];
            readFully(payload);
            if (mask != null) {
                for (int i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];
            }

            switch (opcode) {
                case 0x1:  // matn
                    listener.onText(new String(payload, StandardCharsets.UTF_8));
                    break;
                case 0x8:  // yopish
                    running.set(false);
                    return;
                case 0x9:  // ping -> pong
                    sendFrame(0xA, payload, 0, payload.length);
                    break;
                default:
                    // ikkilik va boshqalar bizga kelmaydi
                    break;
            }
        }
    }

    private void readFully(byte[] buf) throws IOException {
        int off = 0;
        while (off < buf.length) {
            int n = in.read(buf, off, buf.length - off);
            if (n < 0) throw new IOException("ulanish uzildi");
            off += n;
        }
    }

    // ---------------------------------------------------------- yozish

    public void sendText(String text) {
        byte[] data = text.getBytes(StandardCharsets.UTF_8);
        sendFrame(0x1, data, 0, data.length);
    }

    public void sendBinary(byte[] data, int off, int len) {
        sendFrame(0x2, data, off, len);
    }

    private synchronized void sendFrame(int opcode, byte[] data, int off, int len) {
        if (!running.get() || out == null) return;
        try {
            out.write(0x80 | opcode);
            // Mijozdan serverga ketadigan kadrlar niqoblanishi shart
            if (len < 126) {
                out.write(0x80 | len);
            } else if (len < 65536) {
                out.write(0x80 | 126);
                out.write((len >> 8) & 0xFF);
                out.write(len & 0xFF);
            } else {
                out.write(0x80 | 127);
                for (int i = 7; i >= 0; i--) out.write((int) ((long) len >> (8 * i)) & 0xFF);
            }
            byte[] mask = new byte[4];
            random.nextBytes(mask);
            out.write(mask);

            byte[] masked = new byte[len];
            for (int i = 0; i < len; i++) masked[i] = (byte) (data[off + i] ^ mask[i & 3]);
            out.write(masked);
            out.flush();
        } catch (IOException e) {
            Log.w(TAG, "yuborilmadi: " + e);
            running.set(false);
        }
    }

    private static String base64(byte[] data) {
        return android.util.Base64.encodeToString(data, android.util.Base64.NO_WRAP);
    }
}
