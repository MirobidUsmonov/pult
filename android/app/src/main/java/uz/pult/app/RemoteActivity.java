package uz.pult.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceError;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * Kompyuter ekrani ko'rsatiladigan oyna.
 *
 * Ichida oddiy WebView bor va butun boshqaruv veb-ilovaning o'zida
 * qoladi - shu tufayli ilova bilan brauzer versiyasi hech qachon
 * bir-biridan orqada qolmaydi.
 *
 * Ilovaning brauzerdan asosiy farqi ikkitasi: o'z-o'zini imzolagan
 * sertifikatni bir marta so'rab eslab qoladi, va brauzerning manzil
 * qatori, tugmalari kabi keraksiz qismlari yo'q.
 */
public class RemoteActivity extends Activity {

    public static final String EXTRA_URL = "url";

    private WebView web;
    private Hosts hosts;
    private String serverUrl = "";
    private TextView status;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        hosts = new Hosts(this);

        String full = getIntent().getStringExtra(EXTRA_URL);
        if (full == null || full.isEmpty()) {
            finish();
            return;
        }
        Hosts.Host parsed = Hosts.parse(full);
        serverUrl = parsed == null ? full : parsed.url;

        // Ekran o'chib qolmasin: boshqarish paytida foydalanuvchi
        // ekranga uzoq vaqt tegmasligi mumkin.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        // Kesilgan burchakli telefonlarda rasm butun ekranni egallasin
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            // getAttributes() nusxa qaytaradi, shuning uchun o'zgartirib
            // qaytarib qo'yish kerak - aks holda sozlama qo'llanmaydi.
            WindowManager.LayoutParams lp = getWindow().getAttributes();
            lp.layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            getWindow().setAttributes(lp);
        }

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        web = new WebView(this);
        setupWebView();
        root.addView(web, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        status = new TextView(this);
        status.setTextColor(0xFF8B97A6);
        status.setTextSize(13);
        status.setPadding(48, 48, 48, 48);
        status.setVisibility(View.GONE);
        FrameLayout.LayoutParams sp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT);
        sp.gravity = android.view.Gravity.CENTER_VERTICAL;
        root.addView(status, sp);

        setContentView(root);
        hideBars();
        web.loadUrl(full);
    }

    private void setupWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        // Veb-ilova sozlamalarni va kalitni localStorage'da saqlaydi
        s.setDomStorageEnabled(true);
        // Video foydalanuvchi tegmasdan boshlanishi kerak
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setSupportZoom(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);

        web.setBackgroundColor(Color.BLACK);
        web.setKeepScreenOn(true);
        // Uzun bosishda tizimning matn tanlash oynasi chiqmasin -
        // uzun bosish bizda "ushlab sudrash" imo-ishorasi.
        web.setLongClickable(false);
        web.setOnLongClickListener(v -> true);
        web.setHapticFeedbackEnabled(true);

        if (BuildConfig.DEBUG) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handleSslError(handler, error);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req != null && req.isForMainFrame()) {
                    showStatus("Kompyuterga ulanib bo‘lmadi.\n\n"
                            + "• Kompyuter yoniqmi va Pult ishlayaptimi?\n"
                            + "• Telefon o‘sha Wi-Fi tarmog‘idami?\n\n"
                            + serverUrl);
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                status.setVisibility(View.GONE);
            }
        });
    }

    /**
     * Sertifikat xatosi: birinchi marta so'raymiz, keyin eslab qolamiz.
     *
     * Agent o'z-o'zini imzolagan sertifikat ishlatadi (brauzerlar video
     * dekodlashni faqat HTTPS'da beradi), shuning uchun bu xato har
     * safar keladi. Uni ko'r-ko'rona o'tkazib yuborish xavfli bo'lardi:
     * o'rtadagi odam boshqa sertifikat bilan ulanib olishi mumkin.
     * Shuning uchun izni saqlab qo'yamiz va keyin faqat aynan o'shanga
     * ruxsat beramiz - bu brauzerdagi "baribir davom etish" dan
     * xavfsizroq.
     */
    private void handleSslError(SslErrorHandler handler, SslError error) {
        String fp = Fingerprint.of(error.getCertificate());
        Hosts.Host known = hosts.find(serverUrl);

        if (known != null && !known.pin.isEmpty()) {
            if (known.pin.equals(fp)) {
                handler.proceed();
            } else {
                handler.cancel();
                showStatus("Sertifikat o‘zgargan.\n\n"
                        + "Kutilgan: " + Fingerprint.shortForm(known.pin) + "\n"
                        + "Kelgan:   " + Fingerprint.shortForm(fp) + "\n\n"
                        + "Bu kompyuterda Pult qayta o‘rnatilgan yoki tarmoq manzili "
                        + "o‘zgargan bo‘lsa normal holat: ro‘yxatdan o‘chirib qayta "
                        + "qo‘shing. Aks holda ulanmang.");
            }
            return;
        }

        new AlertDialog.Builder(this)
                .setTitle("Birinchi ulanish")
                .setMessage("Bu kompyuterni ilk marta ko‘ryapman.\n\n"
                        + serverUrl + "\n\n"
                        + "Sertifikat izi:\n" + Fingerprint.shortForm(fp) + "\n\n"
                        + "Kompyuterdagi Pult ham shu izni ko‘rsatayotgan bo‘lsa "
                        + "ishonish mumkin. Keyin bu savol qayta berilmaydi.")
                .setCancelable(false)
                .setPositiveButton("Ishonaman", (d, w) -> {
                    hosts.rememberPin(serverUrl, fp);
                    handler.proceed();
                })
                .setNegativeButton("Bekor", (d, w) -> {
                    handler.cancel();
                    finish();
                })
                .show();
    }

    private void showStatus(String text) {
        status.setText(text);
        status.setVisibility(View.VISIBLE);
    }

    /** Holat qatori va boshqaruv tugmalarini yashiradi. */
    private void hideBars() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(
                        WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            web.setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
        }
    }

    @Override
    public void onWindowFocusChanged(boolean has) {
        super.onWindowFocusChanged(has);
        if (has) hideBars();
    }

    private long lastBack = 0;

    @Override
    public void onBackPressed() {
        // Tasodifan chiqib ketmaslik uchun ikki marta bosish kerak:
        // orqaga tugmasi boshqaruv paytida oson bosilib ketadi.
        long now = System.currentTimeMillis();
        if (now - lastBack < 2000) {
            super.onBackPressed();
            return;
        }
        lastBack = now;
        Toast.makeText(this, "Chiqish uchun yana bosing", Toast.LENGTH_SHORT).show();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (web != null) web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
        hideBars();
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.loadUrl("about:blank");
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
