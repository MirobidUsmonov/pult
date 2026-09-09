package uz.pult.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.media.projection.MediaProjectionConfig;
import android.media.projection.MediaProjectionManager;
import android.provider.Settings;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;

/**
 * The first screen: the list of computers to connect to.
 *
 * The layout is built in code, not in XML. The reason is simple: the app
 * is tiny and consists of one list, while XML layout files drag in an
 * extra library (AndroidX). Without them the APK is half the size.
 */
public class HostsActivity extends Activity {

    // The colours match the web interface (the variables in style.css):
    // layered dark surfaces, one accent colour
    private static final int BG = 0xFF0A0C10;
    private static final int PANEL = 0xFF11151B;
    private static final int PANEL2 = 0xFF181D25;
    private static final int LINE = 0xFF222933;
    private static final int LINE2 = 0xFF2E3742;
    private static final int TEXT = 0xFFEEF2F7;
    private static final int MUTED = 0xFF93A0B1;
    private static final int ACCENT = 0xFF5AA9FF;
    private static final int ACCENT_INK = 0xFF061626;
    private static final int OK = 0xFF34D399;
    private static final int OK_BG = 0xFF0F2A21;

    private static final int REQ_PROJECTION = 41;

    private Hosts hosts;
    private LinearLayout list;

    // These three are static on purpose: while the user is away in the
    // system settings granting a permission, Android may destroy this
    // activity entirely and recreate it on return. As ordinary fields
    // they would lose their place, and people would be left wondering
    // why nothing happens.
    private static Hosts.Host pendingShare;
    /** The computer we sent the user to the settings for. */
    private static Hosts.Host pendingAccess;
    /** The computer we asked for a battery exemption for. */
    private static Hosts.Host pendingBattery;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        hosts = new Hosts(this);
        buildUi();

        handleIncomingLink(getIntent());

        // A single computer used to be opened immediately. That does
        // harm now: the app has two directions, and after a trip to the
        // system settings for a permission Android recreates the
        // activity - at which point the computer screen opened by
        // itself and the user could no longer share their own. The list
        // has two plainly labelled buttons; one press is not too much.
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIncomingLink(intent);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();

        // Back from the battery settings - carry on where we stopped
        if (pendingBattery != null) {
            Hosts.Host h = pendingBattery;
            pendingBattery = null;
            if (batteryFree()) {
                Toast.makeText(this, "Permission granted", Toast.LENGTH_SHORT).show();
            }
            requestProjection(h);
            return;
        }

        // Back from the settings - check what happened
        if (pendingAccess != null) {
            Hosts.Host h = pendingAccess;
            pendingAccess = null;
            if (InputService.get() != null) {
                // Granted - carry on where we stopped. Otherwise people
                // would have to go back to the card and press the button
                // again, with no clue why.
                Toast.makeText(this, "Permission granted", Toast.LENGTH_SHORT).show();
                requestProjection(h);
            } else {
                explainRestricted();
            }
        }
    }

    /**
     * The "restricted setting" barrier, added in Android 13.
     *
     * An app installed outside the Play Store is not allowed to turn on
     * accessibility services: the row is there in the settings, but it
     * is greyed out and unclickable, and an "App was denied access"
     * dialog appears.
     *
     * This cannot be solved from inside the app - it is a deliberate
     * protection, and only the user can lift it, from the menu on the
     * app's own settings page. All we can do is say exactly where to go
     * and open that page for them.
     */
    private void explainRestricted() {
        if (android.os.Build.VERSION.SDK_INT < 33) return;
        new AlertDialog.Builder(this)
                .setTitle("Android blocked it")
                .setMessage("The \u201crestricted setting\u201d protection "
                        + "kicked in \u2014 it stops apps installed outside "
                        + "the Play Store from turning on accessibility "
                        + "services.\n\n"
                        + "To unlock it:\n"
                        + "1. Press the button \u2014 the Pult page opens\n"
                        + "2. Tap the \u22ee menu in the top right\n"
                        + "3. Choose \u201cAllow restricted settings\u201d\n"
                        + "4. Come back and press \u201cTurn on\u201d again")
                .setPositiveButton("Open the Pult page", (d, w) -> openAppDetails())
                .setNegativeButton("Later", null)
                .show();
    }

    private void openAppDetails() {
        Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.fromParts("package", getPackageName(), null));
        if (!tryStart(i)) {
            Toast.makeText(this, "Could not open the app page",
                    Toast.LENGTH_LONG).show();
        }
    }

    // ------------------------------------------------------------ layout

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);
        int pad = dp(20);
        root.setPadding(pad, dp(36), pad, pad);

        TextView title = new TextView(this);
        title.setText("Pult");
        title.setTextColor(TEXT);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(title);

        TextView sub = new TextView(this);
        sub.setText("Computers");
        sub.setTextColor(MUTED);
        sub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        sub.setPadding(0, dp(2), 0, dp(18));
        root.addView(sub);

        ScrollView scroll = new ScrollView(this);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(list);
        root.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        TextView add = button("+  Add a computer", ACCENT, ACCENT_INK);
        add.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        add.setPadding(dp(16), dp(16), dp(16), dp(16));
        add.setOnClickListener(v -> askForLink());
        root.addView(add, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView hint = new TextView(this);
        hint.setText("\u201cControl\u201d \u2014 the computer screen on "
                + "the phone. \u201cShare my screen\u201d \u2014 the phone "
                + "screen on the computer.");
        hint.setTextColor(MUTED);
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        hint.setPadding(dp(4), dp(14), dp(4), 0);
        root.addView(hint);

        setContentView(root);
    }

    private void refresh() {
        list.removeAllViews();
        List<Hosts.Host> all = hosts.all();
        if (all.isEmpty()) {
            // An empty list is the app's first impression. Rather than
            // writing "empty", say what to do about it. Plain, with no
            // decoration - only the next step.
            TextView head = new TextView(this);
            head.setText("No computers yet");
            head.setTextColor(TEXT);
            head.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
            head.setTypeface(Typeface.DEFAULT_BOLD);
            head.setPadding(dp(2), dp(28), dp(2), dp(6));
            list.addView(head);

            TextView empty = new TextView(this);
            empty.setText("On the computer, click the Pult icon next to "
                    + "the clock \u2014 a QR code appears in the window. Scan "
                    + "it, or add the link with the button below.");
            empty.setTextColor(MUTED);
            empty.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
            empty.setLineSpacing(0, 1.3f);
            empty.setPadding(dp(2), 0, dp(2), 0);
            list.addView(empty);
            return;
        }
        // When sharing is on, say so plainly above the list: before,
        // only a small mark on the card changed and it was hard to
        // notice that sharing was running.
        if (ScreenService.isRunning()) {
            LinearLayout banner = new LinearLayout(this);
            banner.setOrientation(LinearLayout.HORIZONTAL);
            banner.setGravity(Gravity.CENTER_VERTICAL);
            banner.setBackground(rounded(PANEL, OK, 10));
            banner.setPadding(dp(14), dp(10), dp(8), dp(10));

            TextView text = new TextView(this);
            text.setText("\u25cf  Sharing the screen with the computer");
            text.setTextColor(OK);
            text.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
            text.setTypeface(Typeface.DEFAULT_BOLD);
            banner.addView(text, new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

            TextView stop = pill("Stop", OK, ACCENT_INK);
            stop.setOnClickListener(v -> stopShare());
            banner.addView(stop);

            LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            blp.bottomMargin = dp(12);
            banner.setLayoutParams(blp);
            list.addView(banner);
        }
        for (Hosts.Host h : all) {
            list.addView(card(h));
        }
    }

    /**
     * One computer's card.
     *
     * Two directions, two plainly labelled buttons: "Control" (the
     * computer screen on the phone) and "Share my screen" (the phone
     * screen on the computer). The second one used to be a small arrow
     * icon whose purpose was unguessable - you could not even tell
     * whether it had been pressed.
     */
    private View card(Hosts.Host h) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(rounded(PANEL, LINE, 10));
        card.setPadding(dp(16), dp(14), dp(16), dp(14));

        // The name row: a status dot plus the name
        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);

        TextView dot = new TextView(this);
        dot.setText("●");
        dot.setTextColor(h.pin.isEmpty() ? MUTED : OK);
        dot.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        dot.setPadding(0, 0, dp(8), 0);
        head.addView(dot);

        TextView name = new TextView(this);
        name.setText(h.display());
        name.setTextColor(TEXT);
        name.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        name.setTypeface(Typeface.DEFAULT_BOLD);
        head.addView(name);
        card.addView(head);

        TextView url = new TextView(this);
        String note = h.url;
        // When both routes are known, say so: people should know in
        // advance that it still works from another network, rather than
        // assuming it broke.
        int far = 0;
        for (String u : h.urls) {
            if (!Hosts.isLocal(u)) far++;
        }
        if (far > 0 && Hosts.isLocal(h.url)) note += "  \u00b7  over the internet too";
        if (h.pin.isEmpty()) note += "  \u00b7  not verified yet";
        url.setText(note);
        url.setTextColor(MUTED);
        url.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12.5f);
        url.setPadding(dp(18), dp(4), 0, dp(14));
        card.addView(url);

        // The button row
        LinearLayout acts = new LinearLayout(this);
        acts.setOrientation(LinearLayout.HORIZONTAL);

        TextView control = button("Control", ACCENT, ACCENT_INK);
        control.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        control.setPadding(dp(12), dp(11), dp(12), dp(11));
        control.setBackground(rounded(ACCENT, ACCENT, 8));
        control.setOnClickListener(v -> open(h));
        LinearLayout.LayoutParams l1 = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        l1.rightMargin = dp(8);
        acts.addView(control, l1);

        boolean on = ScreenService.isRunning();
        TextView share = button(on ? "Stop" : "Share my screen", PANEL2, on ? OK : TEXT);
        share.setBackground(rounded(PANEL2, on ? OK : LINE2, 8));
        share.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        share.setPadding(dp(12), dp(11), dp(12), dp(11));
        share.setOnClickListener(v -> {
            if (ScreenService.isRunning()) stopShare();
            else startShare(h);
        });
        acts.addView(share, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        card.addView(acts);

        // A long press removes it from the list
        card.setOnLongClickListener(v -> {
            confirmRemove(h);
            return true;
        });

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(12);
        card.setLayoutParams(lp);
        return card;
    }

    // ------------------------------------------------------ screen sharing

    private void startShare(Hosts.Host h) {
        if (h.pin.isEmpty()) {
            new AlertDialog.Builder(this)
                    .setTitle("Connect once first")
                    .setMessage("Sharing your screen needs the certificate "
                            + "fingerprint. Open the computer once \u2014 the "
                            + "fingerprint is stored and this will work.")
                    .setPositiveButton("Open", (d, w) -> open(h))
                    .setNegativeButton("Cancel", null)
                    .show();
            return;
        }
        if (InputService.get() == null) {
            askForAccessibility(h);
            return;
        }
        if (!batteryFree()) {
            askBattery(h);
            return;
        }
        requestProjection(h);
    }

    /** Whether the app is exempt from battery saving. */
    private boolean batteryFree() {
        try {
            android.os.PowerManager pm =
                    (android.os.PowerManager) getSystemService(Context.POWER_SERVICE);
            return pm != null && pm.isIgnoringBatteryOptimizations(getPackageName());
        } catch (Exception e) {
            // If it cannot be determined, do not get in the way
            return true;
        }
    }

    /**
     * Asks for an exemption from battery saving.
     *
     * This is the most common reason sharing stops once the screen goes
     * off: the system puts the app to sleep and the service is killed.
     * It cannot be handled from inside the app - only the user can
     * grant it. It is not mandatory: refused, sharing still works, it
     * may just drop when the screen turns off.
     */
    private void askBattery(Hosts.Host h) {
        new AlertDialog.Builder(this)
                .setTitle("So it keeps working with the screen off")
                .setMessage("Android puts apps to sleep to save battery, "
                        + "and then sharing stops the moment the screen "
                        + "goes off.\n\n"
                        + "Press the button and choose \u201cAllow\u201d.")
                .setPositiveButton("Ask for permission", (d, w) -> {
                    Intent i = new Intent(
                            Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                            Uri.fromParts("package", getPackageName(), null));
                    if (!tryStart(i)) {
                        // On some phones asking directly is blocked -
                        // open the general list instead
                        tryStart(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                    }
                    pendingBattery = h;
                })
                .setNeutralButton("Continue anyway", (d, w) -> requestProjection(h))
                .setNegativeButton("Cancel", null)
                .show();
    }

    /**
     * Asks for permission to capture the screen.
     *
     * Android asks every single time and there is no way to remember
     * the answer - a deliberate protection that cannot be worked
     * around.
     */
    private void requestProjection(Hosts.Host h) {
        pendingShare = h;
        MediaProjectionManager mpm =
                (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);

        Intent intent;
        if (android.os.Build.VERSION.SDK_INT >= 34) {
            // From Android 14 on, the system asks "the whole screen" or
            // "a single app" and shows an app chooser. Sharing one app
            // makes no sense for Pult: controlling the phone from the
            // computer needs the whole screen, otherwise neither the
            // home screen nor any other app is visible.
            //
            // Asked with createConfigForDefaultDisplay, the system does
            // not put that question at all.
            intent = mpm.createScreenCaptureIntent(
                    MediaProjectionConfig.createConfigForDefaultDisplay());
        } else {
            intent = mpm.createScreenCaptureIntent();
        }
        startActivityForResult(intent, REQ_PROJECTION);
    }

    /** The input service is off - send the user to the settings. */
    private void askForAccessibility(Hosts.Host h) {
        new AlertDialog.Builder(this)
                .setTitle("Control needs a permission")
                .setMessage("For the computer to tap on the phone, the "
                        + "\u201cPult \u2014 control this phone\u201d service "
                        + "has to be turned on.\n\n"
                        + "Press the button and that page opens \u2014 just "
                        + "turn it on and come back.\n\n"
                        + "This is an Android protection: no app may tap on "
                        + "other apps by itself.\n\n"
                        + "If you only want to show your screen, you can "
                        + "continue without it.")
                .setPositiveButton("Turn on", (d, w) -> {
                    // Let it carry on by itself when we come back
                    pendingAccess = h;
                    openAccessibilitySettings();
                })
                .setNeutralButton("Show only", (d, w) -> requestProjection(h))
                .setNegativeButton("Cancel", null)
                .show();
    }

    /**
     * Opens the settings page for the Pult service.
     *
     * A plain ACTION_ACCESSIBILITY_SETTINGS opens the general list and
     * leaves people to find Pult in it - on some phones it is buried
     * under "Downloaded services" and simply cannot be found.
     *
     * Three routes, in this order: the service's own page (Android
     * 12+), the list with the right row highlighted, and finally the
     * plain list.
     */
    private void openAccessibilitySettings() {
        String service = new ComponentName(this, InputService.class).flattenToString();

        // Strings rather than constants, so building against an older
        // SDK does not break compilation
        if (android.os.Build.VERSION.SDK_INT >= 31) {
            Intent direct = new Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS");
            direct.putExtra("android.intent.extra.COMPONENT_NAME", service);
            if (tryStart(direct)) return;
        }

        Intent highlighted = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
        Bundle args = new Bundle();
        args.putString(":settings:fragment_args_key", service);
        highlighted.putExtra(":settings:fragment_args_key", service);
        highlighted.putExtra(":settings:show_fragment_args", args);
        if (tryStart(highlighted)) return;

        if (tryStart(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))) return;
        Toast.makeText(this, "Could not open the settings", Toast.LENGTH_LONG).show();
    }

    private boolean tryStart(Intent intent) {
        try {
            startActivity(intent);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private void stopShare() {
        Intent i = new Intent(this, ScreenService.class).setAction(ScreenService.ACTION_STOP);
        startService(i);
        Toast.makeText(this, "Screen sharing stopped", Toast.LENGTH_SHORT).show();
        list.postDelayed(this::refresh, 400);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_PROJECTION) return;
        if (resultCode != RESULT_OK || data == null || pendingShare == null) {
            Toast.makeText(this, "Screen capture was not allowed", Toast.LENGTH_LONG).show();
            return;
        }
        Hosts.Host h = pendingShare;
        pendingShare = null;

        Intent svc = new Intent(this, ScreenService.class);
        svc.putExtra(ScreenService.EXTRA_URL, h.url);
        svc.putExtra(ScreenService.EXTRA_TOKEN, h.token);
        svc.putExtra(ScreenService.EXTRA_PIN, h.pin);
        svc.putExtra(ScreenService.EXTRA_NAME, android.os.Build.MODEL);
        svc.putExtra(ScreenService.EXTRA_RESULT_CODE, resultCode);
        svc.putExtra(ScreenService.EXTRA_RESULT_DATA, data);
        startForegroundService(svc);

        Toast.makeText(this, "Sharing the screen with the computer", Toast.LENGTH_LONG).show();
        list.postDelayed(this::refresh, 600);
    }

    // ------------------------------------------------------------ actions

    private void open(Hosts.Host h) {
        if (h.token.isEmpty()) {
            Toast.makeText(this, "This entry has no key, add it again",
                    Toast.LENGTH_LONG).show();
            return;
        }
        // Work out which address will work beforehand: on the same
        // Wi-Fi a local address is much faster than the tunnel, and
        // from another network only the tunnel works. The check is
        // network work, so it runs on its own thread.
        if (h.candidates().size() < 2) {
            launch(h, h.url);
            return;
        }
        Toast.makeText(this, "Choosing a route\u2026", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            String best = Reach.pick(h, this);
            runOnUiThread(() -> {
                if (best == null) {
                    // None of them answered. Open the last working
                    // address anyway - the error page there explains
                    // the reason better than we can.
                    launch(h, h.url);
                } else {
                    if (!Hosts.strip(best).equals(Hosts.strip(h.url))) {
                        Toast.makeText(this, Hosts.isLocal(best)
                                ? "Connecting over Wi-Fi" : "Connecting over the internet",
                                Toast.LENGTH_SHORT).show();
                    }
                    launch(h, best);
                }
            });
        }, "pult-reach").start();
    }

    private void launch(Hosts.Host h, String base) {
        Intent i = new Intent(this, RemoteActivity.class);
        i.putExtra(RemoteActivity.EXTRA_URL, h.fullUrl(base));
        startActivity(i);
    }

    private void confirmRemove(Hosts.Host h) {
        new AlertDialog.Builder(this)
                .setTitle(h.display())
                .setMessage("Remove it from the list?")
                .setPositiveButton("Remove", (d, w) -> {
                    hosts.remove(h.url);
                    refresh();
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void askForLink() {
        EditText input = new EditText(this);
        input.setHint("https://192.168.1.5:8787/#k=...");
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setTextColor(TEXT);
        input.setHintTextColor(MUTED);
        input.setPadding(dp(16), dp(14), dp(16), dp(14));

        // If the link is on the clipboard, fill it in - chances are it
        // was copied from the computer just now.
        String clip = clipboard();
        if (clip != null && (clip.contains("#k=") || clip.startsWith("pult://"))) {
            input.setText(clip);
        }

        new AlertDialog.Builder(this)
                .setTitle("Add a computer")
                // Not saying where to get the link was the single most
                // confusing thing: a dialog opens and there is no way to
                // tell what to type into it
                .setMessage("On the computer, click the Pult icon next to the "
                        + "clock \u2192 \u201cConnect a phone\u201d.\n\n"
                        + "\u2022 Scan the QR code with the camera, or\n"
                        + "\u2022 press \u201cSend to Telegram\u201d, then "
                        + "long-press the link and choose \u201cShare \u2192 "
                        + "Pult\u201d, or\n"
                        + "\u2022 copy the link and paste it here.")
                .setView(input)
                .setPositiveButton("Add", (d, w) -> addFromLink(input.getText().toString()))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private String clipboard() {
        try {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm != null && cm.hasPrimaryClip() && cm.getPrimaryClip() != null
                    && cm.getPrimaryClip().getItemCount() > 0) {
                CharSequence t = cm.getPrimaryClip().getItemAt(0).coerceToText(this);
                return t == null ? null : t.toString().trim();
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private void addFromLink(String link) {
        Hosts.Host h = Hosts.parse(link);
        if (h == null) {
            Toast.makeText(this, "The link makes no sense", Toast.LENGTH_LONG).show();
            return;
        }
        if (h.token.isEmpty()) {
            Toast.makeText(this, "The link has no key (the #k=... part)",
                    Toast.LENGTH_LONG).show();
            return;
        }
        hosts.add(h);
        refresh();
        open(h);
    }

    /** When opened with a pult:// or https:// link. */
    private boolean handleIncomingLink(Intent intent) {
        if (intent == null) return false;
        String action = intent.getAction();

        if (Intent.ACTION_VIEW.equals(action)) {
            Uri data = intent.getData();
            if (data == null) return false;
            addFromLink(data.toString());
            return true;
        }

        // Text arriving through "Share": in a Telegram message the link
        // comes surrounded by other words, so it is pulled out of the
        // text.
        if (Intent.ACTION_SEND.equals(action)) {
            String text = intent.getStringExtra(Intent.EXTRA_TEXT);
            String link = firstLink(text);
            if (link == null) {
                Toast.makeText(this, "No Pult link found in this text",
                        Toast.LENGTH_LONG).show();
                return true;
            }
            addFromLink(link);
            return true;
        }
        return false;
    }

    /** Returns the first http(s) link in the text. */
    private static String firstLink(String text) {
        if (text == null) return null;
        for (String word : text.split("\\s+")) {
            if (word.startsWith("http://") || word.startsWith("https://")) {
                return word;
            }
        }
        return null;
    }

    // ------------------------------------------------------------ helpers

    /** A filled button. */
    private TextView button(String text, int bg, int fg) {
        TextView b = new TextView(this);
        b.setText(text);
        b.setTextColor(fg);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(16), dp(14), dp(16), dp(14));
        b.setBackground(rounded(bg, bg, 8));
        b.setClickable(true);
        return b;
    }

    /** A small button, as used inside the banner. */
    private TextView pill(String text, int bg, int fg) {
        TextView b = new TextView(this);
        b.setText(text);
        b.setTextColor(fg);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(12), dp(7), dp(12), dp(7));
        b.setBackground(rounded(bg, bg, 6));
        b.setClickable(true);
        return b;
    }

    private GradientDrawable rounded(int fill, int stroke) {
        return rounded(fill, stroke, 10);
    }

    private GradientDrawable rounded(int fill, int stroke, int radiusDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(fill);
        d.setCornerRadius(dp(radiusDp));
        d.setStroke(dp(1), stroke);
        return d;
    }

    private int dp(float v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
