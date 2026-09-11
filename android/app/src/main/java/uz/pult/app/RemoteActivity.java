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
 * The window that shows the computer screen.
 *
 * Inside is a plain WebView, and all of the control logic stays in the
 * web app - which is why the app and the browser version can never fall
 * behind each other.
 *
 * The app differs from a browser in two ways: it asks about the
 * self-signed certificate once and remembers the answer, and it has
 * none of the browser's clutter - address bar, buttons and the rest.
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

        // Keep the screen on: while controlling, the user may not
        // touch the screen for a long time.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        // On phones with a notch, let the picture fill the whole screen
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            // getAttributes() returns a copy, so it has to be modified
            // and set back - otherwise the setting is not applied.
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
        // The web app keeps its settings and the key in localStorage
        s.setDomStorageEnabled(true);
        // Video has to start without the user touching anything
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setSupportZoom(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);

        web.setBackgroundColor(Color.BLACK);
        web.setKeepScreenOn(true);
        // Do not let a long press bring up the system's text selection -
        // here a long press is the "hold and drag" gesture.
        web.setLongClickable(false);
        web.setOnLongClickListener(v -> true);
        web.setHapticFeedbackEnabled(true);

        if (BuildConfig.DEBUG) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        web.addJavascriptInterface(new Bridge(), "PultNative");

        web.setWebChromeClient(new WebChromeClient() {
            /**
             * The page asks for the microphone, for dictation.
             *
             * A WebView refuses every such request unless the app
             * answers it, so without this the microphone button on the
             * page would simply do nothing. Only the microphone is ever
             * granted, and only after Android has asked the user for
             * it - anything else the page might ask for is refused.
             */
            @Override
            public void onPermissionRequest(final android.webkit.PermissionRequest request) {
                runOnUiThread(() -> {
                    for (String res : request.getResources()) {
                        if (!android.webkit.PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(res)) {
                            continue;
                        }
                        if (hasMicPermission()) {
                            request.grant(new String[]{res});
                        } else {
                            // Asked for now; the page will ask again on
                            // the next press, by which time the answer
                            // is known.
                            pendingMic = request;
                            askForMic();
                        }
                        return;
                    }
                    request.deny();
                });
            }
        });
        web.setWebViewClient(new WebViewClient() {

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handleSslError(handler, error);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req != null && req.isForMainFrame()) {
                    showStatus("Could not connect to the computer.\n\n"
                            + "\u2022 Is the computer on and is Pult running?\n"
                            + "\u2022 Is the phone on the same Wi-Fi?\n\n"
                            + serverUrl);
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                status.setVisibility(View.GONE);
                refreshAddresses();
            }
        });
    }

    /**
     * Asks the agent for all of its addresses and refreshes the list.
     *
     * The tunnel address changes every time the computer restarts.
     * Without a refresh the phone would try a stale address next time,
     * wait a long while and then fail to connect. The moment a
     * connection succeeds is exactly the right time to refresh.
     */
    private void refreshAddresses() {
        final Hosts.Host known = hosts.find(serverUrl);
        if (known == null || known.token.isEmpty()) return;
        new Thread(() -> {
            Reach.Info info = Reach.info(serverUrl, known.token, known.pin);
            if (info == null) return;
            hosts.remember(info.id, serverUrl, info.addresses);
        }, "pult-addresses").start();
    }

    /**
     * A certificate error: ask the first time, remember afterwards.
     *
     * The agent uses a self-signed certificate (browsers only allow
     * video decoding over HTTPS), so this error comes up every time.
     * Waving it through blindly would be dangerous: someone in the
     * middle could connect with a different certificate. So the
     * fingerprint is stored and only that exact one is allowed
     * afterwards - safer than the browser's "proceed anyway".
     */
    private void handleSslError(SslErrorHandler handler, SslError error) {
        String fp = Fingerprint.of(error.getCertificate());
        Hosts.Host known = hosts.find(serverUrl);

        if (known != null && !known.pin.isEmpty()) {
            if (known.pin.equals(fp)) {
                handler.proceed();
            } else {
                handler.cancel();
                showStatus("The certificate has changed.\n\n"
                        + "Expected: " + Fingerprint.shortForm(known.pin) + "\n"
                        + "Received: " + Fingerprint.shortForm(fp) + "\n\n"
                        + "That is normal if Pult was reinstalled on this "
                        + "computer or its network address changed: remove it "
                        + "from the list and add it again. Otherwise do not "
                        + "connect.");
            }
            return;
        }

        new AlertDialog.Builder(this)
                .setTitle("First connection")
                .setMessage("This is the first time I have seen this "
                        + "computer.\n\n"
                        + serverUrl + "\n\n"
                        + "Certificate fingerprint:\n"
                        + Fingerprint.shortForm(fp) + "\n\n"
                        + "If Pult on the computer shows the same "
                        + "fingerprint, it can be trusted. You will not be "
                        + "asked again.")
                .setCancelable(false)
                .setPositiveButton("Trust", (d, w) -> {
                    hosts.rememberPin(serverUrl, fp);
                    handler.proceed();
                })
                .setNegativeButton("Cancel", (d, w) -> {
                    handler.cancel();
                    finish();
                })
                .show();
    }

    // -- microphone ----------------------------------------------------------

    private static final int REQ_MIC = 71;
    /**
     * Static on purpose.
     *
     * Android may destroy this activity while the permission dialog is
     * up and build a new one when it closes. As an ordinary field the
     * pending request would be lost with it, the page would wait for an
     * answer that never came, and the microphone would appear broken.
     * The same trap was already hit on the computer list.
     */
    private static android.webkit.PermissionRequest pendingMic;

    /**
     * A small bridge the page can call.
     *
     * The microphone needs Android's permission before the WebView will
     * admit that a microphone exists at all: without it getUserMedia
     * does not ask, it simply reports that no device was found, which
     * reads on the page as a phone with no microphone. So the page asks
     * for the permission through here first, and only then records.
     */
    private class Bridge {
        @android.webkit.JavascriptInterface
        public boolean hasMic() {
            return hasMicPermission();
        }

        @android.webkit.JavascriptInterface
        public void requestMic() {
            runOnUiThread(() -> {
                if (!hasMicPermission()) askForMic();
            });
        }
    }

    private boolean hasMicPermission() {
        return checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    private void askForMic() {
        requestPermissions(new String[]{android.Manifest.permission.RECORD_AUDIO}, REQ_MIC);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        if (code != REQ_MIC) return;
        boolean granted = results.length > 0
                && results[0] == android.content.pm.PackageManager.PERMISSION_GRANTED;
        if (pendingMic != null) {
            // The page is still waiting on this one, so answer it
            // rather than leaving the request hanging.
            if (granted) {
                pendingMic.grant(new String[]{
                        android.webkit.PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            } else {
                pendingMic.deny();
            }
            pendingMic = null;
        }
        if (!granted) {
            Toast.makeText(this, "Without the microphone there is no dictation",
                    Toast.LENGTH_LONG).show();
        }
        // The page is waiting on the answer either way.
        if (web != null) {
            web.evaluateJavascript(
                    "window.__pultMic && window.__pultMic(" + granted + ")", null);
        }
    }

    private void showStatus(String text) {
        status.setText(text);
        status.setVisibility(View.VISIBLE);
    }

    /** Hides the status bar and the navigation buttons. */
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
        // Two presses are needed so nobody leaves by accident: the back
        // button is easy to hit while controlling.
        long now = System.currentTimeMillis();
        if (now - lastBack < 2000) {
            super.onBackPressed();
            return;
        }
        lastBack = now;
        Toast.makeText(this, "Press again to leave", Toast.LENGTH_SHORT).show();
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
