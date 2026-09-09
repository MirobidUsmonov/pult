package uz.pult.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.Surface;
import android.view.WindowManager;

import org.json.JSONObject;

import java.nio.ByteBuffer;

/**
 * Shares the phone screen with the computer and carries out the commands
 * that come back.
 *
 * The screen is captured through MediaProjection and turned into H.264
 * by MediaCodec on the phone's own hardware encoder - the CPU is barely
 * touched and battery is saved. The result is sent in exactly the same
 * shape as the computer's own stream, so the computer side needs no
 * separate code.
 */
public class ScreenService extends Service {

    private static final String TAG = "PultScreen";
    private static final String CHANNEL = "pult_screen";
    private static final int NOTIF_ID = 1;

    public static final String EXTRA_URL = "url";
    public static final String EXTRA_TOKEN = "token";
    public static final String EXTRA_PIN = "pin";
    public static final String EXTRA_NAME = "name";
    public static final String EXTRA_RESULT_CODE = "resultCode";
    public static final String EXTRA_RESULT_DATA = "resultData";
    public static final String ACTION_STOP = "uz.pult.app.STOP";

    private static volatile boolean running = false;

    public static boolean isRunning() {
        return running;
    }

    private WsClient ws;
    private MediaProjection projection;
    private VirtualDisplay display;
    private MediaCodec encoder;
    private Surface inputSurface;
    private Thread drainThread;
    private volatile boolean streaming = false;

    private byte[] csd;              // SPS and PPS
    private String codecString = "";
    private int outW, outH;
    private String hostName = "Phone";

    // The connection details are kept: a drop means reconnecting
    private String baseUrl, token, pin;
    private int attempt = 0;
    /** Whether the user stopped it. Then we do not reconnect. */
    private volatile boolean stopped = false;

    // With the screen off the system puts the CPU and Wi-Fi to sleep
    // and the connection drops. These locks prevent that.
    private android.os.PowerManager.WakeLock wakeLock;
    private android.net.wifi.WifiManager.WifiLock wifiLock;

    private final Handler main = new Handler(Looper.getMainLooper());

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_STOP.equals(intent.getAction())) {
            report("the user stopped it");
            stopEverything();
            return START_NOT_STICKY;
        }

        // From Android 14 on, the service has to be in the foreground
        // BEFORE capture starts, or the system refuses.
        startForegroundNotice();

        String url = intent.getStringExtra(EXTRA_URL);
        String token = intent.getStringExtra(EXTRA_TOKEN);
        String pin = intent.getStringExtra(EXTRA_PIN);
        hostName = intent.getStringExtra(EXTRA_NAME);
        if (hostName == null || hostName.isEmpty()) hostName = Build.MODEL;

        int resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0);
        Intent resultData = intent.getParcelableExtra(EXTRA_RESULT_DATA);
        if (resultData != null) {
            MediaProjectionManager mpm =
                    (MediaProjectionManager) getSystemService(Context.MEDIA_PROJECTION_SERVICE);
            projection = mpm.getMediaProjection(resultCode, resultData);
            if (projection != null) {
                projection.registerCallback(new MediaProjection.Callback() {
                    @Override
                    public void onStop() {
                        Log.i(TAG, "capture was stopped by the system");
                        // Tell the computer why: reading the phone's log
                        // needs a cable and developer mode, while the
                        // computer's log is always at hand
                        report("the system stopped capture (MediaProjection.onStop)");
                        stopEverything();
                    }
                }, main);
            }
        }

        running = true;
        stopped = false;
        attempt = 0;
        holdLocks();
        // The address is picked here rather than taken from the stored
        // entry: screen sharing lasts a long time and going down a slow
        // route is especially costly. The check is network work, so it
        // runs on its own thread.
        final String fallback = url;
        new Thread(() -> {
            String best = fallback;
            Hosts.Host known = new Hosts(this).find(fallback);
            if (known != null && known.candidates().size() > 1) {
                String picked = Reach.pick(known, this);
                if (picked != null) best = picked;
            }
            final String chosen = best;
            main.post(() -> {
                if (running) connect(chosen, token, pin);
            });
        }, "pult-reach-src").start();
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        // The service may be being killed by the system - that is what
        // battery saving does on Samsung. This message is the only way
        // to learn the reason.
        if (running) report("the service is stopping (onDestroy)");
        stopEverything();
        super.onDestroy();
    }

    // --------------------------------------------------------- connection

    /**
     * Reports an event that matters to the computer, where it shows up
     * in the computer's log.
     *
     * It is hard to tell what is happening on the phone: reading its log
     * needs a cable and developer mode. The computer's log is always
     * open, so the reasons are sent there.
     */
    private void report(String text) {
        try {
            if (ws != null) {
                ws.sendText(new JSONObject().put("t", "note")
                        .put("msg", text).toString());
            }
        } catch (Exception ignored) {
        }
    }

    /** Restores a dropped connection, waiting a little longer each time. */
    private void scheduleReconnect(String reason) {
        attempt++;
        // Do not retry forever when it never comes back: Android keeps
        // showing the "sharing your screen" indicator at the top, and
        // with nothing being shared that is only misleading.
        if (attempt > 20) {
            stopEverything();
            doneNotice("The connection did not come back \u2014 sharing stopped");
            return;
        }
        long delay = Math.min(1000L * attempt, 15000L);
        notice("The connection dropped, reconnecting\u2026 (" + reason + ")");
        Log.i(TAG, "reconnect " + attempt + ", in " + delay + " ms");
        main.postDelayed(() -> {
            if (!running || stopped) return;
            // The address is chosen afresh; we do not cling to the old
            // one. With the screen off the phone may move from Wi-Fi to
            // mobile data, and then the local address will never work
            // again and retrying it is wasted effort.
            final String last = baseUrl;
            new Thread(() -> {
                String best = last;
                try {
                    Hosts.Host known = new Hosts(this).find(last);
                    if (known != null && known.candidates().size() > 1) {
                        String picked = Reach.pick(known, this);
                        if (picked != null) best = picked;
                    }
                } catch (Exception e) {
                    Log.w(TAG, "no address chosen: " + e);
                }
                final String chosen = best;
                main.post(() -> {
                    if (!running || stopped) return;
                    if (!chosen.equals(last)) {
                        Log.i(TAG, "moving to another address: " + chosen);
                    }
                    connect(chosen, token, pin);
                });
            }, "pult-reconnect").start();
        }, delay);
    }

    private void connect(String base, String token, String pin) {
        this.baseUrl = base;
        this.token = token;
        this.pin = pin;
        String wsUrl = base.replaceFirst("^http", "ws") + "/ws?k=" + android.net.Uri.encode(token);
        ws = new WsClient(wsUrl, pin, new WsClient.Listener() {
            @Override
            public void onOpen() {
                Log.i(TAG, "connected to the computer");
                attempt = 0;
                notice("Connected to the computer");
                sendHello();
            }

            @Override
            public void onText(String message) {
                handle(message);
            }

            @Override
            public void onClosed(String reason) {
                Log.i(TAG, "connection closed: " + reason);
                if (!running || stopped) return;
                // A drop is usually temporary: the network dozes for a
                // moment when the phone screen turns off. Stopping
                // immediately meant that switching the screen off and
                // on once ended sharing for good.
                main.post(() -> {
                    if (!running || stopped) return;
                    stopStream();
                    scheduleReconnect(reason);
                });
            }
        });
        ws.start();
    }

    private void sendHello() {
        try {
            DisplayMetrics dm = new DisplayMetrics();
            ((WindowManager) getSystemService(WINDOW_SERVICE)).getDefaultDisplay()
                    .getRealMetrics(dm);
            JSONObject info = new JSONObject()
                    .put("kind", "phone")
                    .put("w", dm.widthPixels)
                    .put("h", dm.heightPixels)
                    .put("input", InputService.get() != null);
            ws.sendText(new JSONObject()
                    .put("t", "hello")
                    .put("role", "source")
                    .put("name", hostName)
                    .put("info", info)
                    .toString());
        } catch (Exception e) {
            Log.w(TAG, "hello not sent: " + e);
        }
    }

    // ---------------------------------------------------- incoming messages

    private void handle(String text) {
        try {
            JSONObject m = new JSONObject(text);
            String t = m.optString("t");
            switch (t) {
                case "stream_start":
                    main.post(() -> startStream(
                            m.optInt("width", 1280),
                            m.optInt("fps", 30),
                            m.optInt("bitrate", 4000)));
                    break;
                case "stream_stop":
                    main.post(this::stopStream);
                    break;
                case "mouse":
                    onMouse(m);
                    break;
                case "scroll":
                    input(s -> s.scroll(m.optDouble("dy", 0)));
                    break;
                case "text":
                    input(s -> s.typeText(m.optString("s", "")));
                    break;
                case "key":
                    onKey(m);
                    break;
                case "cmd":
                    input(s -> s.global(m.optString("name", "")));
                    break;
                default:
                    break;
            }
        } catch (Exception e) {
            Log.w(TAG, "message not understood: " + e);
        }
    }

    private interface InputAction {
        void run(InputService s);
    }

    private void input(InputAction action) {
        InputService s = InputService.get();
        if (s == null) {
            notice("Turn on the accessibility service to allow control");
            return;
        }
        try {
            action.run(s);
        } catch (Exception e) {
            Log.w(TAG, "input not carried out: " + e);
        }
    }

    private void onMouse(JSONObject m) {
        String a = m.optString("a", "");
        final double x = m.optDouble("x", 0.5);
        final double y = m.optDouble("y", 0.5);
        final boolean hasPoint = m.has("x") && m.has("y");
        input(s -> {
            switch (a) {
                case "click":
                    if (hasPoint) {
                        if ("right".equals(m.optString("b"))) s.longPress(x, y);
                        else s.tap(x, y);
                    }
                    break;
                case "dblclick":
                    if (hasPoint) s.doubleTap(x, y);
                    break;
                case "down":
                    if (hasPoint) s.dragStart(x, y);
                    break;
                case "move":
                    if (hasPoint) s.dragMove(x, y);
                    break;
                case "up":
                    s.dragEnd();
                    break;
                default:
                    break;
            }
        });
    }

    private void onKey(JSONObject m) {
        String k = m.optString("k", "");
        String action = m.optString("a", "tap");
        if (!"tap".equals(action) && !"down".equals(action)) return;
        input(s -> {
            switch (k) {
                case "Backspace": s.backspace(); break;
                case "Escape":    s.global("back"); break;
                case "Enter":     s.typeText("\n"); break;
                case "Home":      s.global("home"); break;
                default:
                    if (k.length() == 1) s.typeText(k);
                    break;
            }
        });
    }

    // -------------------------------------------------------- screen stream

    private void startStream(int maxSide, int fps, int bitrateKbps) {
        if (streaming) return;
        if (projection == null) {
            notice("Screen capture was not allowed");
            return;
        }
        try {
            DisplayMetrics dm = new DisplayMetrics();
            ((WindowManager) getSystemService(WINDOW_SERVICE)).getDefaultDisplay()
                    .getRealMetrics(dm);
            int sw = dm.widthPixels, sh = dm.heightPixels;

            // Bring the long side down to the requested size. Rounding
            // to 16 is necessary: some encoders refuse other sizes or
            // corrupt the picture.
            if (maxSide <= 0) maxSide = Math.max(sw, sh);
            double scale = Math.min(1.0, (double) maxSide / Math.max(sw, sh));
            outW = round16((int) (sw * scale));
            outH = round16((int) (sh * scale));

            MediaFormat fmt = MediaFormat.createVideoFormat("video/avc", outW, outH);
            fmt.setInteger(MediaFormat.KEY_COLOR_FORMAT,
                    MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
            fmt.setInteger(MediaFormat.KEY_BIT_RATE, bitrateKbps * 1000);
            fmt.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
            fmt.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                fmt.setInteger(MediaFormat.KEY_LATENCY, 1);
            }

            encoder = MediaCodec.createEncoderByType("video/avc");
            encoder.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            inputSurface = encoder.createInputSurface();
            encoder.start();

            display = projection.createVirtualDisplay(
                    "pult",
                    outW, outH, dm.densityDpi,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    inputSurface, null, null);

            streaming = true;
            csd = null;
            codecString = "";
            drainThread = new Thread(this::drain, "pult-encoder");
            drainThread.setDaemon(true);
            drainThread.start();
            Log.i(TAG, "stream started " + outW + "x" + outH + " " + fps + " fps");
            notice("Sharing the screen");
        } catch (Exception e) {
            Log.e(TAG, "the stream did not start", e);
            notice("The screen could not be shared");
            stopStream();
        }
    }

    private static int round16(int v) {
        int r = Math.max(160, (v / 16) * 16);
        return r;
    }

    private void stopStream() {
        streaming = false;
        try {
            if (display != null) display.release();
        } catch (Exception ignored) {
        }
        display = null;
        try {
            if (encoder != null) {
                encoder.stop();
                encoder.release();
            }
        } catch (Exception ignored) {
        }
        encoder = null;
        if (inputSurface != null) {
            inputSurface.release();
            inputSurface = null;
        }
    }

    /**
     * Reads the frames coming out of the encoder and sends them to the
     * computer.
     *
     * The header is the same as the computer's: kind, flags, timestamp.
     * That is why the computer side needed no separate code for the
     * phone's stream.
     */
    private void drain() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        long startedAt = System.currentTimeMillis();
        while (streaming) {
            try {
                int index = encoder.dequeueOutputBuffer(info, 100_000);
                if (index == MediaCodec.INFO_TRY_AGAIN_LATER) continue;
                if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat f = encoder.getOutputFormat();
                    ByteBuffer sps = f.getByteBuffer("csd-0");
                    ByteBuffer pps = f.getByteBuffer("csd-1");
                    if (sps != null && pps != null) {
                        byte[] a = new byte[sps.remaining()];
                        sps.get(a);
                        byte[] b = new byte[pps.remaining()];
                        pps.get(b);
                        csd = new byte[a.length + b.length];
                        System.arraycopy(a, 0, csd, 0, a.length);
                        System.arraycopy(b, 0, csd, a.length, b.length);
                        codecString = H264.codecString(csd);
                        announceStream();
                    }
                    continue;
                }
                if (index < 0) continue;

                ByteBuffer buf = encoder.getOutputBuffer(index);
                if (buf == null) {
                    encoder.releaseOutputBuffer(index, false);
                    continue;
                }
                buf.position(info.offset);
                buf.limit(info.offset + info.size);

                boolean isConfig =
                        (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;
                boolean isKey =
                        (info.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0;

                if (isConfig) {
                    byte[] data = new byte[info.size];
                    buf.get(data);
                    csd = data;
                    codecString = H264.codecString(csd);
                    announceStream();
                    encoder.releaseOutputBuffer(index, false);
                    continue;
                }

                // Prepend the SPS/PPS to a key frame: a new viewer can
                // configure its decoder from them.
                int extra = (isKey && csd != null) ? csd.length : 0;
                byte[] out = new byte[8 + extra + info.size];
                int ts = (int) (System.currentTimeMillis() - startedAt);
                out[0] = 1;                        // video
                out[1] = (byte) (isKey ? 1 : 0);
                out[2] = 0;
                out[3] = 0;
                out[4] = (byte) (ts >>> 24);
                out[5] = (byte) (ts >>> 16);
                out[6] = (byte) (ts >>> 8);
                out[7] = (byte) ts;
                if (extra > 0) System.arraycopy(csd, 0, out, 8, extra);
                buf.get(out, 8 + extra, info.size);

                if (ws != null && ws.isOpen()) {
                    ws.sendBinary(out, 0, out.length);
                }
                encoder.releaseOutputBuffer(index, false);
            } catch (IllegalStateException e) {
                break;   // the encoder was stopped
            } catch (Exception e) {
                Log.w(TAG, "frame not sent: " + e);
            }
        }
    }

    private void announceStream() {
        if (ws == null || !ws.isOpen() || codecString.isEmpty()) return;
        try {
            ws.sendText(new JSONObject()
                    .put("t", "stream")
                    .put("codec", codecString)
                    .put("w", outW)
                    .put("h", outH)
                    .put("fps", 30)
                    .put("encoder", "Phone (hardware)")
                    .toString());
        } catch (Exception ignored) {
        }
    }

    // ------------------------------------------------------------ service

    private void startForegroundNotice() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL, "Screen sharing", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Shown while the phone screen is being shared "
                    + "with the computer");
            nm.createNotificationChannel(ch);
        }
        startForeground(NOTIF_ID, buildNotification("Connecting to the computer\u2026"));
    }

    private Notification buildNotification(String text) {
        Intent stop = new Intent(this, ScreenService.class).setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(
                this, 0, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        return b.setContentTitle("Pult \u2014 screen sharing")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_menu_view)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(
                        null, "Stop", stopPi).build())
                .build();
    }

    /**
     * Says that sharing has finished.
     *
     * Under a separate id: stopForeground removes the main notification,
     * so this cannot be written to the same id. The message is
     * transient - tapping it dismisses it.
     */
    private void doneNotice(String text) {
        try {
            NotificationManager nm = getSystemService(NotificationManager.class);
            Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                    ? new Notification.Builder(this, CHANNEL)
                    : new Notification.Builder(this);
            nm.notify(NOTIF_ID + 1, b
                    .setContentTitle("Pult")
                    .setContentText(text)
                    .setSmallIcon(android.R.drawable.ic_menu_view)
                    .setAutoCancel(true)
                    .build());
        } catch (Exception ignored) {
        }
    }

    private void notice(String text) {
        try {
            NotificationManager nm = getSystemService(NotificationManager.class);
            nm.notify(NOTIF_ID, buildNotification(text));
        } catch (Exception ignored) {
        }
    }

    /**
     * The locks that keep it working with the screen off.
     *
     * The moment the phone screen turns off, the system stops the CPU
     * and puts Wi-Fi to sleep, and the connection drops. These locks
     * prevent that. They cost more battery, but screen sharing is
     * already a battery-hungry job and it only runs when the user turns
     * it on.
     *
     * Note: this does not keep the screen lit - Android does not allow
     * that. While the screen is off the computer may receive a black
     * picture, but the CONNECTION survives and the picture comes back
     * as soon as the screen lights up.
     */
    private void holdLocks() {
        try {
            if (wakeLock == null) {
                android.os.PowerManager pm =
                        (android.os.PowerManager) getSystemService(Context.POWER_SERVICE);
                wakeLock = pm.newWakeLock(
                        android.os.PowerManager.PARTIAL_WAKE_LOCK, "pult:screen");
                wakeLock.setReferenceCounted(false);
            }
            if (!wakeLock.isHeld()) wakeLock.acquire();
        } catch (Exception e) {
            Log.w(TAG, "CPU lock not acquired: " + e);
        }
        try {
            if (wifiLock == null) {
                android.net.wifi.WifiManager wm = (android.net.wifi.WifiManager)
                        getApplicationContext().getSystemService(Context.WIFI_SERVICE);
                wifiLock = wm.createWifiLock(
                        android.net.wifi.WifiManager.WIFI_MODE_FULL_HIGH_PERF, "pult:ws");
                wifiLock.setReferenceCounted(false);
            }
            if (!wifiLock.isHeld()) wifiLock.acquire();
        } catch (Exception e) {
            Log.w(TAG, "Wi-Fi lock not acquired: " + e);
        }
    }

    private void releaseLocks() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        } catch (Exception ignored) {
        }
        try {
            if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        } catch (Exception ignored) {
        }
    }

    private void stopEverything() {
        running = false;
        stopped = true;
        releaseLocks();
        stopStream();
        if (ws != null) {
            ws.close();
            ws = null;
        }
        if (projection != null) {
            try {
                projection.stop();
            } catch (Exception ignored) {
            }
            projection = null;
        }
        stopForeground(true);
        stopSelf();
    }
}
