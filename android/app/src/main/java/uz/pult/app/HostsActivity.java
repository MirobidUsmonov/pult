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
 * Boshlang'ich oyna: ulanadigan kompyuterlar ro'yxati.
 *
 * Tartib XML'da emas, kodda quriladi. Sabab oddiy: ilova juda kichik va
 * bitta ro'yxatdan iborat, XML tartib fayllari esa qo'shimcha kutubxona
 * (AndroidX) tortib keladi. Ularsiz APK ikki barobar kichik chiqadi.
 */
public class HostsActivity extends Activity {

    // Ranglar veb-interfeys bilan bir xil (style.css dagi o'zgaruvchilar):
    // qatlamli qorong'i yuzalar, bitta urg'u rangi
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
    private Hosts.Host pendingShare;
    /** Sozlamalarga ruxsat so'rab yuborilgan kompyuter. */
    private Hosts.Host pendingAccess;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        hosts = new Hosts(this);
        buildUi();

        boolean handled = handleIncomingLink(getIntent());

        // Bitta kompyuter bo'lsa darrov ochamiz - kundalik ishlatishda
        // ortiqcha bosish kerak emas. Orqaga qaytilsa ro'yxat ko'rinadi.
        if (!handled && saved == null && hosts.all().size() == 1) {
            open(hosts.all().get(0));
        }
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

        // Sozlamalardan qaytdi - nima bo'lganini tekshiramiz
        if (pendingAccess != null) {
            Hosts.Host h = pendingAccess;
            pendingAccess = null;
            if (InputService.get() != null) {
                // Ruxsat berildi - to'xtagan joyidan davom etamiz. Aks
                // holda odam kartaga qaytib, ⇧ ni qaytadan bosishi
                // kerak bo'lardi va nima uchunligi tushunarsiz qolardi.
                Toast.makeText(this, "Ruxsat berildi", Toast.LENGTH_SHORT).show();
                requestProjection(h);
            } else {
                explainRestricted();
            }
        }
    }

    /**
     * Android 13 dan boshlab paydo bo'lgan "cheklangan sozlama" to'sig'i.
     *
     * Play Market'dan tashqarida o'rnatilgan ilovaga maxsus imkoniyatlarni
     * yoqishga ruxsat berilmaydi: sozlamalar sahifasida qator ko'rinadi,
     * lekin xira va bosilmaydi, "App was denied access" oynasi chiqadi.
     *
     * Buni ilova ichidan hal qilib bo'lmaydi - bu ataylab qo'yilgan
     * himoya va uni faqat foydalanuvchi ilova sahifasidagi menyudan
     * ocha oladi. Bizning qo'limizdan keladigani - qayerga borishni
     * aniq aytish va o'sha sahifani ochib berish.
     */
    private void explainRestricted() {
        if (android.os.Build.VERSION.SDK_INT < 33) return;
        new AlertDialog.Builder(this)
                .setTitle("Android yo‘l bermadi")
                .setMessage("«Cheklangan sozlama» himoyasi ishga tushdi — u "
                        + "Play Market’dan tashqarida o‘rnatilgan ilovalarga "
                        + "maxsus imkoniyatlarni yoqishga to‘sqinlik qiladi.\n\n"
                        + "Ochish uchun:\n"
                        + "1. Tugmani bosing — Pult sahifasi ochiladi\n"
                        + "2. O‘ng yuqoridagi ⋮ menyusini bosing\n"
                        + "3. «Allow restricted settings» ni tanlang\n"
                        + "4. Qaytib kelib, yana «Yoqish» ni bosing")
                .setPositiveButton("Pult sahifasini ochish", (d, w) -> openAppDetails())
                .setNegativeButton("Keyinroq", null)
                .show();
    }

    private void openAppDetails() {
        Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.fromParts("package", getPackageName(), null));
        if (!tryStart(i)) {
            Toast.makeText(this, "Ilova sahifasini ochib bo‘lmadi",
                    Toast.LENGTH_LONG).show();
        }
    }

    // ------------------------------------------------------------ tartib

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);
        int pad = dp(20);
        root.setPadding(pad, dp(36), pad, pad);

        TextView title = new TextView(this);
        title.setText("Pult");
        title.setTextColor(TEXT);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 30);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setLetterSpacing(-0.02f);
        root.addView(title);

        TextView sub = new TextView(this);
        sub.setText("Telefon ↔ kompyuter");
        sub.setTextColor(MUTED);
        sub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        sub.setPadding(0, dp(2), 0, dp(20));
        root.addView(sub);

        ScrollView scroll = new ScrollView(this);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(list);
        root.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        TextView add = button("+  Kompyuter qo‘shish", ACCENT, ACCENT_INK);
        add.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        add.setPadding(dp(16), dp(16), dp(16), dp(16));
        add.setOnClickListener(v -> askForLink());
        root.addView(add, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView hint = new TextView(this);
        hint.setText("«Boshqarish» — kompyuter ekrani telefonda. "
                + "«Ekranimni uzatish» — telefon ekrani kompyuterda.");
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
            // Bo'sh ro'yxat - ilovadagi birinchi ko'rinish. Shunchaki
            // "bo'sh" deb yozish o'rniga nima qilish kerakligini aytamiz.
            LinearLayout box = new LinearLayout(this);
            box.setOrientation(LinearLayout.VERTICAL);
            box.setGravity(Gravity.CENTER_HORIZONTAL);
            box.setBackground(rounded(PANEL, LINE, 20));
            box.setPadding(dp(24), dp(32), dp(24), dp(28));

            TextView icon = new TextView(this);
            icon.setText("🖥");
            icon.setTextSize(TypedValue.COMPLEX_UNIT_SP, 40);
            icon.setGravity(Gravity.CENTER);
            icon.setBackground(rounded(PANEL2, LINE2, 36));
            icon.setPadding(dp(18), dp(12), dp(18), dp(12));
            box.addView(icon);

            TextView head = new TextView(this);
            head.setText("Hali kompyuter yo‘q");
            head.setTextColor(TEXT);
            head.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
            head.setTypeface(Typeface.DEFAULT_BOLD);
            head.setGravity(Gravity.CENTER);
            head.setPadding(0, dp(18), 0, dp(8));
            box.addView(head);

            TextView empty = new TextView(this);
            empty.setText("Kompyuterda soat yonidagi Pult belgisini bosing — "
                    + "oynada QR kod chiqadi. Uni skanerlang yoki havolani "
                    + "pastdagi tugma orqali qo‘shing.");
            empty.setTextColor(MUTED);
            empty.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
            empty.setGravity(Gravity.CENTER);
            empty.setLineSpacing(0, 1.3f);
            box.addView(empty);

            LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            elp.topMargin = dp(8);
            box.setLayoutParams(elp);
            list.addView(box);
            return;
        }
        // Uzatish yoqilgan bo'lsa buni ro'yxat tepasida aniq ko'rsatamiz:
        // ilgari faqat kartadagi kichik belgi o'zgarardi va uzatish
        // ketayotganini payqash qiyin edi.
        if (ScreenService.isRunning()) {
            LinearLayout banner = new LinearLayout(this);
            banner.setOrientation(LinearLayout.HORIZONTAL);
            banner.setGravity(Gravity.CENTER_VERTICAL);
            banner.setBackground(rounded(OK_BG, OK, 16));
            banner.setPadding(dp(16), dp(12), dp(10), dp(12));

            TextView text = new TextView(this);
            text.setText("●  Ekran kompyuterga uzatilmoqda");
            text.setTextColor(OK);
            text.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
            text.setTypeface(Typeface.DEFAULT_BOLD);
            banner.addView(text, new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

            TextView stop = pill("To‘xtatish", OK, ACCENT_INK);
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
     * Bitta kompyuter kartasi.
     *
     * Ikkita yo'nalish ikkita ochiq yozilgan tugma bilan: "Boshqarish"
     * (kompyuter ekrani telefonda) va "Ekranimni uzatish" (telefon
     * ekrani kompyuterda). Ilgari ikkinchisi kichik ⇧ belgisi edi va
     * u nima qilishini bilib bo'lmasdi - belgi bosilgan-bosilmagani
     * ham sezilmasdi.
     */
    private View card(Hosts.Host h) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(rounded(PANEL, LINE, 18));
        card.setPadding(dp(18), dp(16), dp(18), dp(16));

        // Nom qatori: holat nuqtasi + nom
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
        name.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        name.setTypeface(Typeface.DEFAULT_BOLD);
        head.addView(name);
        card.addView(head);

        TextView url = new TextView(this);
        String note = h.url;
        // Ikkala yo'l ham ma'lum bo'lsa buni aytamiz: foydalanuvchi
        // boshqa tarmoqqa o'tganda ham ishlashini oldindan bilib
        // tursin, "ishlamay qoldi" deb o'ylamasin.
        int far = 0;
        for (String u : h.urls) {
            if (!Hosts.isLocal(u)) far++;
        }
        if (far > 0 && Hosts.isLocal(h.url)) note += "  ·  internet orqali ham";
        if (h.pin.isEmpty()) note += "  ·  hali tasdiqlanmagan";
        url.setText(note);
        url.setTextColor(MUTED);
        url.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12.5f);
        url.setPadding(dp(18), dp(4), 0, dp(14));
        card.addView(url);

        // Tugmalar qatori
        LinearLayout acts = new LinearLayout(this);
        acts.setOrientation(LinearLayout.HORIZONTAL);

        TextView control = button("Boshqarish", ACCENT, ACCENT_INK);
        control.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14.5f);
        control.setPadding(dp(12), dp(13), dp(12), dp(13));
        control.setOnClickListener(v -> open(h));
        LinearLayout.LayoutParams l1 = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        l1.rightMargin = dp(10);
        acts.addView(control, l1);

        boolean on = ScreenService.isRunning();
        TextView share = on
                ? button("◼  To‘xtatish", OK_BG, OK)
                : button("Ekranimni uzatish", PANEL2, TEXT);
        share.setBackground(rounded(on ? OK_BG : PANEL2, on ? OK : LINE2, 14));
        share.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14.5f);
        share.setPadding(dp(12), dp(13), dp(12), dp(13));
        share.setOnClickListener(v -> {
            if (ScreenService.isRunning()) stopShare();
            else startShare(h);
        });
        acts.addView(share, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        card.addView(acts);

        // Uzun bosish - ro'yxatdan o'chirish
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

    // -------------------------------------------------- ekranni ulashish

    private void startShare(Hosts.Host h) {
        if (h.pin.isEmpty()) {
            new AlertDialog.Builder(this)
                    .setTitle("Avval bir marta ulaning")
                    .setMessage("Ekranni uzatish uchun sertifikat izi kerak. "
                            + "Kompyuterni bir marta oching — iz saqlanadi, "
                            + "keyin bu ishlaydi.")
                    .setPositiveButton("Ochish", (d, w) -> open(h))
                    .setNegativeButton("Bekor", null)
                    .show();
            return;
        }
        if (InputService.get() == null) {
            askForAccessibility(h);
            return;
        }
        requestProjection(h);
    }

    /**
     * Ekranni ko'rsatish uchun ruxsat so'raydi.
     *
     * Android buni har safar so'raydi va uni saqlab qo'yishning iloji
     * yo'q - bu ataylab qo'yilgan himoya, chetlab o'tib bo'lmaydi.
     */
    private void requestProjection(Hosts.Host h) {
        pendingShare = h;
        MediaProjectionManager mpm =
                (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);

        Intent intent;
        if (android.os.Build.VERSION.SDK_INT >= 34) {
            // Android 14 dan boshlab tizim "butun ekran" yoki "bitta
            // ilova" deb so'raydi va ilova tanlash ro'yxatini
            // ko'rsatadi. Pult uchun bitta ilovani uzatishning ma'nosi
            // yo'q: kompyuterdan telefonni boshqarish uchun butun
            // ekran kerak, aks holda bosh ekran ham, boshqa ilovalar
            // ham ko'rinmaydi.
            //
            // createConfigForDefaultDisplay bilan so'ralganda tizim
            // ortiqcha savolni umuman bermaydi.
            intent = mpm.createScreenCaptureIntent(
                    MediaProjectionConfig.createConfigForDefaultDisplay());
        } else {
            intent = mpm.createScreenCaptureIntent();
        }
        startActivityForResult(intent, REQ_PROJECTION);
    }

    /** Boshqarish xizmati yoqilmagan - sozlamalarga yo'naltiramiz. */
    private void askForAccessibility(Hosts.Host h) {
        new AlertDialog.Builder(this)
                .setTitle("Boshqarish uchun ruxsat kerak")
                .setMessage("Kompyuter telefonga bosishi uchun «Pult — "
                        + "telefonni boshqarish» xizmatini yoqish kerak.\n\n"
                        + "Tugmani bossangiz o‘sha sahifa ochiladi — "
                        + "shunchaki yoqib, orqaga qayting.\n\n"
                        + "Bu Android himoyasi: hech bir ilova boshqa "
                        + "ilovalarga o‘zicha bosa olmaydi.\n\n"
                        + "Faqat ekranni ko‘rsatmoqchi bo‘lsangiz, "
                        + "yoqmasdan ham davom etish mumkin.")
                .setPositiveButton("Yoqish", (d, w) -> {
                    // Qaytib kelganda ishni o'zi davom ettirsin
                    pendingAccess = h;
                    openAccessibilitySettings();
                })
                .setNeutralButton("Faqat ko‘rsatish", (d, w) -> requestProjection(h))
                .setNegativeButton("Bekor", null)
                .show();
    }

    /**
     * Pult xizmatining sozlamalar sahifasini ochadi.
     *
     * Oddiy ACTION_ACCESSIBILITY_SETTINGS umumiy ro'yxatni ochadi va
     * odam Pult'ni o'sha ro'yxatdan qidirishi kerak bo'ladi - ba'zi
     * telefonlarda u "Yuklab olingan xizmatlar" ichida yashiringan
     * bo'ladi va topib bo'lmaydi.
     *
     * Uchta yo'l, shu tartibda: xizmatning o'z sahifasi (Android 12+),
     * ro'yxat ichida kerakli qatorni ajratib ko'rsatish, va oxirida
     * oddiy ro'yxat.
     */
    private void openAccessibilitySettings() {
        String service = new ComponentName(this, InputService.class).flattenToString();

        // Doimiy nomlar o'rniga satrlar: shunda eskiroq SDK bilan
        // yig'ilganda ham kompilyatsiya buzilmaydi
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
        Toast.makeText(this, "Sozlamalarni ochib bo‘lmadi", Toast.LENGTH_LONG).show();
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
        Toast.makeText(this, "Ekran uzatish to‘xtatildi", Toast.LENGTH_SHORT).show();
        list.postDelayed(this::refresh, 400);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_PROJECTION) return;
        if (resultCode != RESULT_OK || data == null || pendingShare == null) {
            Toast.makeText(this, "Ekran olishga ruxsat berilmadi", Toast.LENGTH_LONG).show();
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

        Toast.makeText(this, "Ekran kompyuterga uzatilmoqda", Toast.LENGTH_LONG).show();
        list.postDelayed(this::refresh, 600);
    }

    // ------------------------------------------------------------ amallar

    private void open(Hosts.Host h) {
        if (h.token.isEmpty()) {
            Toast.makeText(this, "Bu yozuvda kalit yo‘q, qayta qo‘shing",
                    Toast.LENGTH_LONG).show();
            return;
        }
        // Qaysi manzil ishlashini oldindan aniqlaymiz: bitta Wi-Fi
        // ichida mahalliy manzil tunneldan ancha tez, boshqa
        // tarmoqdan esa faqat tunnel ishlaydi. Tekshiruv tarmoq ishi
        // bo'lgani uchun alohida oqimda bajariladi.
        if (h.candidates().size() < 2) {
            launch(h, h.url);
            return;
        }
        Toast.makeText(this, "Ulanish yo‘li tanlanmoqda…", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            String best = Reach.pick(h, this);
            runOnUiThread(() -> {
                if (best == null) {
                    // Hech biri javob bermadi. Baribir oxirgi ishlagan
                    // manzilni ochamiz - u yerdagi xato oynasi
                    // sababini aniqroq tushuntiradi.
                    launch(h, h.url);
                } else {
                    if (!Hosts.strip(best).equals(Hosts.strip(h.url))) {
                        Toast.makeText(this, Hosts.isLocal(best)
                                ? "Wi-Fi orqali ulanmoqda" : "Internet orqali ulanmoqda",
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
                .setMessage("Ro‘yxatdan o‘chirilsinmi?")
                .setPositiveButton("O‘chirish", (d, w) -> {
                    hosts.remove(h.url);
                    refresh();
                })
                .setNegativeButton("Bekor", null)
                .show();
    }

    private void askForLink() {
        EditText input = new EditText(this);
        input.setHint("https://192.168.1.5:8787/#k=...");
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setTextColor(TEXT);
        input.setHintTextColor(MUTED);
        input.setPadding(dp(16), dp(14), dp(16), dp(14));

        // Havola almashish buferida bo'lsa oldindan qo'yamiz - odam uni
        // kompyuterdan nusxalab kelgan bo'lishi ehtimoli katta.
        String clip = clipboard();
        if (clip != null && (clip.contains("#k=") || clip.startsWith("pult://"))) {
            input.setText(clip);
        }

        new AlertDialog.Builder(this)
                .setTitle("Kompyuter qo‘shish")
                // Havolani qayerdan olishni aytmaslik eng ko'p adashtirgan
                // joy edi: oyna ochiladi-yu, nima yozishni bilib bo'lmaydi
                .setMessage("Kompyuterda soat yonidagi Pult belgisini bosing → "
                        + "«Telefonni ulash».\n\n"
                        + "• QR kodni kamera bilan skanerlang, yoki\n"
                        + "• «Telegramga yuborish» ni bosing va kelgan havolani "
                        + "bosib turib «Ulashish → Pult» qiling, yoki\n"
                        + "• havolani nusxalab shu yerga qo‘ying.")
                .setView(input)
                .setPositiveButton("Qo‘shish", (d, w) -> addFromLink(input.getText().toString()))
                .setNegativeButton("Bekor", null)
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
            Toast.makeText(this, "Havola tushunarsiz", Toast.LENGTH_LONG).show();
            return;
        }
        if (h.token.isEmpty()) {
            Toast.makeText(this, "Havolada kalit yo‘q (#k=... qismi kerak)",
                    Toast.LENGTH_LONG).show();
            return;
        }
        hosts.add(h);
        refresh();
        open(h);
    }

    /** pult:// yoki https:// havolasi bilan ochilgan bo'lsa. */
    private boolean handleIncomingLink(Intent intent) {
        if (intent == null) return false;
        String action = intent.getAction();

        if (Intent.ACTION_VIEW.equals(action)) {
            Uri data = intent.getData();
            if (data == null) return false;
            addFromLink(data.toString());
            return true;
        }

        // "Ulashish" orqali kelgan matn: Telegramdagi xabarda havola
        // boshqa so'zlar bilan birga bo'ladi, shuning uchun uni
        // matndan ajratib olamiz.
        if (Intent.ACTION_SEND.equals(action)) {
            String text = intent.getStringExtra(Intent.EXTRA_TEXT);
            String link = firstLink(text);
            if (link == null) {
                Toast.makeText(this, "Bu matnda Pult havolasi topilmadi",
                        Toast.LENGTH_LONG).show();
                return true;
            }
            addFromLink(link);
            return true;
        }
        return false;
    }

    /** Matndagi birinchi http(s) havolani qaytaradi. */
    private static String firstLink(String text) {
        if (text == null) return null;
        for (String word : text.split("\\s+")) {
            if (word.startsWith("http://") || word.startsWith("https://")) {
                return word;
            }
        }
        return null;
    }

    // ------------------------------------------------------------ yordamchi

    /** To'ldirilgan tugma. */
    private TextView button(String text, int bg, int fg) {
        TextView b = new TextView(this);
        b.setText(text);
        b.setTextColor(fg);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(16), dp(15), dp(16), dp(15));
        b.setBackground(rounded(bg, bg, 14));
        b.setClickable(true);
        return b;
    }

    /** Kichik, to'liq yumaloq tugma (banner ichidagi kabi). */
    private TextView pill(String text, int bg, int fg) {
        TextView b = new TextView(this);
        b.setText(text);
        b.setTextColor(fg);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(14), dp(8), dp(14), dp(8));
        b.setBackground(rounded(bg, bg, 999));
        b.setClickable(true);
        return b;
    }

    private GradientDrawable rounded(int fill, int stroke) {
        return rounded(fill, stroke, 14);
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
