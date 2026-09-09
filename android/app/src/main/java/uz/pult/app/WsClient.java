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
 * A small WebSocket client.
 *
 * Why it is written by hand: Android only ships a WebSocket client from
 * version 13 on, and adding a library would have blown a 69 KB app up to
 * several megabytes. All we need is the handshake, sending text and
 * binary frames, and answering pings - a small corner of RFC 6455.
 *
 * Certificate pinning lives here too: only the certificate the app
 * remembered is accepted, and a connection with any other is refused.
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

    // --------------------------------------------------------- connection

    private void run() {
        // Track whether anyone was told: when readLoop ends normally
        // (the server closed it) no exception is thrown, and nobody used
        // to be notified. The screen-sharing service then kept running
        // and Android kept showing the red indicator at the top.
        boolean told = false;
        try {
            Uri u = Uri.parse(url);
            boolean secure = "wss".equalsIgnoreCase(u.getScheme())
                    || "https".equalsIgnoreCase(u.getScheme());
            int port = u.getPort();
            if (port == -1) port = secure ? 443 : 80;
            String host = u.getHost();

            SocketFactory factory = secure ? pinnedFactory() : SocketFactory.getDefault();
            socket = factory.createSocket(host, port);
            socket.setTcpNoDelay(true);          // frames go out without delay
            socket.setSoTimeout(0);

            in = new BufferedInputStream(socket.getInputStream(), 16 * 1024);
            out = new DataOutputStream(socket.getOutputStream());

            handshake(u, host, port);
            running.set(true);
            listener.onOpen();
            readLoop();
        } catch (Exception e) {
            Log.w(TAG, "the connection dropped: " + e);
            running.set(false);
            told = true;
            listener.onClosed(String.valueOf(e.getMessage()));
        } finally {
            running.set(false);
            try {
                if (socket != null) socket.close();
            } catch (IOException ignored) {
            }
            if (!told) listener.onClosed("the connection was closed");
        }
    }

    /**
     * A connection that accepts only the remembered certificate.
     *
     * A plain "trust everything" would be dangerous: on a local network
     * someone could pose as the computer and take over all control.
     * With the fingerprint compared, such a connection is refused.
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
                    throw new CertificateException("no certificate");
                }
                if (expected.isEmpty()) {
                    // No fingerprint stored yet - refuse the connection.
                    // The fingerprint is taken on the first connection
                    // through the WebView.
                    throw new CertificateException("the certificate fingerprint is not confirmed yet");
                }
                String actual = sha256(chain[0].getEncoded());
                if (!expected.equalsIgnoreCase(actual)) {
                    throw new CertificateException("the certificate did not match");
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
        // Read the remaining headers up to the blank line and drop them
        String line;
        while ((line = readLine()) != null && !line.isEmpty()) {
            // we have no use for the headers
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
                case 0x1:  // text
                    listener.onText(new String(payload, StandardCharsets.UTF_8));
                    break;
                case 0x8:  // close
                    running.set(false);
                    return;
                case 0x9:  // ping -> pong
                    sendFrame(0xA, payload, 0, payload.length);
                    break;
                default:
                    // binary and the rest never reach us
                    break;
            }
        }
    }

    private void readFully(byte[] buf) throws IOException {
        int off = 0;
        while (off < buf.length) {
            int n = in.read(buf, off, buf.length - off);
            if (n < 0) throw new IOException("the connection dropped");
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
            // Frames going from client to server have to be masked
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
