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
 * Telefon ekranini kompyuterga uzatadi va kelgan buyruqlarni bajaradi.
 *
 * Ekran MediaProjection orqali olinadi va MediaCodec bilan telefonning
 * o'z apparat kodlagichida H.264 ga o'giriladi - protsessor deyarli
 * ishlatilmaydi va batareya tejaladi. Natija kompyuterdagi oqim bilan
 * bir xil ko'rinishda uzatiladi, shuning uchun kompyuter tarafida
 * alohida kod kerak emas.
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

    private byte[] csd;              // SPS va PPS
    private String codecString = "";
    private int outW, outH;
    private String hostName = "Telefon";

    // Ulanish ma'lumotlari saqlanadi: uzilganda qayta ulanish kerak
    private String baseUrl, token, pin;
    private int attempt = 0;
    /** Foydalanuvchi to'xtatganmi. Shunda qayta ulanmaymiz. */
    private volatile boolean stopped = false;

    // Ekran o'chganda tizim protsessorni va Wi-Fi ni uxlatadi -
    // ulanish uziladi. Qulflar shuni to'xtatadi.
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
            stopEverything();
            return START_NOT_STICKY;
        }

        // Android 14 dan boshlab ekran olishdan OLDIN xizmat oldingi
        // planda bo'lishi shart, aks holda tizim ruxsat bermaydi.
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
                        Log.i(TAG, "ekran olish tizim tomonidan to'xtatildi");
                        stopEverything();
                    }
                }, main);
            }
        }

        running = true;
        stopped = false;
        attempt = 0;
        holdLocks();
        // Manzilni shu yerda tanlaymiz, ro'yxatdagi yozuvdan emas:
        // ekran uzatish uzoq davom etadi va sekin yo'ldan ketishi
        // ayniqsa qimmatga tushadi. Tekshiruv tarmoq ishi bo'lgani
        // uchun alohida oqimda.
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
        stopEverything();
        super.onDestroy();
    }

    // ------------------------------------------------------------ ulanish

    /** Uzilgan ulanishni qayta tiklaydi - vaqti asta uzayadi. */
    private void scheduleReconnect(String reason) {
        attempt++;
        // Umuman tiklanmasa ham cheksiz urinmaymiz: Android tepada
        // "ekran uzatilmoqda" belgisini ko'rsatib turadi va hech narsa
        // uzatilmayotgan bo'lsa bu faqat chalg'itadi.
        if (attempt > 20) {
            stopEverything();
            doneNotice("Aloqa tiklanmadi — ekran uzatish to‘xtadi");
            return;
        }
        long delay = Math.min(1000L * attempt, 15000L);
        notice("Aloqa uzildi, qayta ulanmoqda… (" + reason + ")");
        Log.i(TAG, "qayta ulanish " + attempt + ", " + delay + " ms dan keyin");
        main.postDelayed(() -> {
            if (!running || stopped) return;
            connect(baseUrl, token, pin);
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
                Log.i(TAG, "kompyuterga ulandi");
                attempt = 0;
                notice("Kompyuterga ulandi");
                sendHello();
            }

            @Override
            public void onText(String message) {
                handle(message);
            }

            @Override
            public void onClosed(String reason) {
                Log.i(TAG, "ulanish yopildi: " + reason);
                if (!running || stopped) return;
                // Uzilish ko'pincha vaqtinchalik: telefon ekrani
                // o'chganda tarmoq bir lahzaga uxlaydi. Darhol
                // to'xtatib qo'ysak, ekranni bir marta o'chirib
                // yoqishning o'zi uzatishni butunlay tugatardi.
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
            Log.w(TAG, "hello yuborilmadi: " + e);
        }
    }

    // ------------------------------------------------------- kiruvchi xabarlar

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
            Log.w(TAG, "xabar tushunilmadi: " + e);
        }
    }

    private interface InputAction {
        void run(InputService s);
    }

    private void input(InputAction action) {
        InputService s = InputService.get();
        if (s == null) {
            notice("Boshqarish uchun maxsus imkoniyatlar xizmatini yoqing");
            return;
        }
        try {
            action.run(s);
        } catch (Exception e) {
            Log.w(TAG, "kiritish bajarilmadi: " + e);
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

    // ---------------------------------------------------------- ekran oqimi

    private void startStream(int maxSide, int fps, int bitrateKbps) {
        if (streaming) return;
        if (projection == null) {
            notice("Ekran olishga ruxsat berilmagan");
            return;
        }
        try {
            DisplayMetrics dm = new DisplayMetrics();
            ((WindowManager) getSystemService(WINDOW_SERVICE)).getDefaultDisplay()
                    .getRealMetrics(dm);
            int sw = dm.widthPixels, sh = dm.heightPixels;

            // Uzun tomonni so'ralgan o'lchamga tushiramiz. 16 ga
            // yaxlitlash kerak: ba'zi kodlagichlar boshqa o'lchamda
            // ishlamaydi yoki rasmni buzadi.
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
            Log.i(TAG, "oqim boshlandi " + outW + "x" + outH + " " + fps + " k/s");
            notice("Ekran uzatilmoqda");
        } catch (Exception e) {
            Log.e(TAG, "oqim boshlanmadi", e);
            notice("Ekranni uzatib bo'lmadi");
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
     * Kodlagichdan chiqqan kadrlarni o'qib, kompyuterga yuboradi.
     *
     * Sarlavha kompyuterdagi bilan bir xil: tur, bayroqlar, vaqt. Shu
     * tufayli kompyuter tarafida telefon oqimi uchun alohida kod yozish
     * kerak bo'lmadi.
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

                // Kalit kadrga SPS/PPS ni oldiga qo'shamiz: yangi
                // tomoshabin ular bilan dekoderni sozlay oladi.
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
                break;   // kodlagich to'xtatildi
            } catch (Exception e) {
                Log.w(TAG, "kadr yuborilmadi: " + e);
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
                    .put("encoder", "Telefon (apparat)")
                    .toString());
        } catch (Exception ignored) {
        }
    }

    // ------------------------------------------------------------ xizmat

    private void startForegroundNotice() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL, "Ekran uzatish", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Telefon ekrani kompyuterga uzatilayotganda ko'rinadi");
            nm.createNotificationChannel(ch);
        }
        startForeground(NOTIF_ID, buildNotification("Kompyuterga ulanmoqda…"));
    }

    private Notification buildNotification(String text) {
        Intent stop = new Intent(this, ScreenService.class).setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(
                this, 0, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        return b.setContentTitle("Pult — ekran uzatish")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_menu_view)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(
                        null, "To'xtatish", stopPi).build())
                .build();
    }

    /**
     * Uzatish tugaganini bildiradi.
     *
     * Alohida raqam bilan: stopForeground asosiy xabarnomani o'chirib
     * yuboradi, shuning uchun uni o'sha raqamga yozib bo'lmaydi. Bu
     * xabar o'tkinchi - bosilsa yo'qoladi.
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
     * Ekran o'chganda ham ishlashda davom etish uchun qulflar.
     *
     * Telefon ekrani o'chishi bilan tizim protsessorni to'xtatadi va
     * Wi-Fi ni uxlatadi - ulanish uziladi. Bu qulflar shuni to'xtatadi.
     * Ular batareyani ko'proq yeydi, lekin ekran uzatish allaqachon
     * batareya yeydigan ish va u faqat foydalanuvchi yoqqanda ishlaydi.
     *
     * Diqqat: bu ekranni yoqib turmaydi - Android bunga ruxsat
     * bermaydi. Ekran o'chgan payt kompyuterga qora tasvir borishi
     * mumkin, lekin ULANISH uzilmaydi va ekran yonishi bilan tasvir
     * qaytadi.
     */
    private void holdLocks() {
        try {
            if (wakeLock == null) {
                android.os.PowerManager pm =
                        (android.os.PowerManager) getSystemService(Context.POWER_SERVICE);
                wakeLock = pm.newWakeLock(
                        android.os.PowerManager.PARTIAL_WAKE_LOCK, "pult:ekran");
                wakeLock.setReferenceCounted(false);
            }
            if (!wakeLock.isHeld()) wakeLock.acquire();
        } catch (Exception e) {
            Log.w(TAG, "protsessor qulfi olinmadi: " + e);
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
            Log.w(TAG, "Wi-Fi qulfi olinmadi: " + e);
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
