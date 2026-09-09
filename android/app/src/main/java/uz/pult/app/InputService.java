package uz.pult.app;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.graphics.Point;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.Display;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

/**
 * Kompyuterdan kelgan bosishlarni telefonda bajaradi.
 *
 * Android oddiy ilovaga boshqa ilovalar ustiga bosish huquqini bermaydi -
 * bu ataylab qo'yilgan himoya. Yagona ochiq yo'l - maxsus imkoniyatlar
 * (Accessibility) xizmati, uni foydalanuvchi sozlamalardan qo'lda
 * yoqishi kerak. Buni chetlab o'tish mumkin emas.
 */
public class InputService extends AccessibilityService {

    private static final String TAG = "PultInput";
    private static InputService instance;

    /** Xizmat yoqilgan bo'lsa uni qaytaradi, aks holda null. */
    public static InputService get() {
        return instance;
    }

    private int width = 1080;
    private int height = 2400;

    // Sudrash holati: Android'da sudrash bitta uzluksiz imo-ishora,
    // bizga esa u bo'laklab keladi (bosildi, surildi, qo'yildi).
    private GestureDescription.StrokeDescription stroke;
    private float lastX, lastY;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        measureScreen();
        Log.i(TAG, "kiritish xizmati yoqildi: " + width + "x" + height);
    }

    @Override
    public void onDestroy() {
        instance = null;
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // Hodisalar bizga kerak emas: xizmat faqat imo-ishora yuborish
        // uchun ishlatiladi.
    }

    @Override
    public void onInterrupt() {
        stroke = null;
    }

    private void measureScreen() {
        try {
            WindowManager wm = (WindowManager) getSystemService(WINDOW_SERVICE);
            Display d = wm.getDefaultDisplay();
            Point p = new Point();
            d.getRealSize(p);
            width = p.x;
            height = p.y;
        } catch (Exception e) {
            Log.w(TAG, "ekran o'lchami aniqlanmadi: " + e);
        }
    }

    public int screenWidth() {
        measureScreen();
        return width;
    }

    public int screenHeight() {
        return height;
    }

    // ------------------------------------------------------- imo-ishoralar

    private float px(double nx) {
        measureScreen();
        return (float) Math.max(0, Math.min(width - 1, nx * width));
    }

    private float py(double ny) {
        return (float) Math.max(0, Math.min(height - 1, ny * height));
    }

    /** Qisqa bosish. */
    public void tap(double nx, double ny) {
        float x = px(nx), y = py(ny);
        Path p = new Path();
        p.moveTo(x, y);
        dispatch(new GestureDescription.StrokeDescription(p, 0, 60));
    }

    /** Uzoq bosish - kontekst menyusi uchun. */
    public void longPress(double nx, double ny) {
        float x = px(nx), y = py(ny);
        Path p = new Path();
        p.moveTo(x, y);
        dispatch(new GestureDescription.StrokeDescription(p, 0, 650));
    }

    /** Ikki marta bosish. */
    public void doubleTap(double nx, double ny) {
        tap(nx, ny);
        // Android ikki bosishni vaqt bo'yicha ajratadi, shuning uchun
        // ikkinchisini biroz kechiktiramiz
        new android.os.Handler(getMainLooper()).postDelayed(() -> tap(nx, ny), 90);
    }

    /**
     * Sudrashni boshlaydi.
     *
     * continueStroke bo'lmaganda har bir siljish alohida imo-ishora
     * bo'lardi va Android ularni sudrash deb qabul qilmasdi - ro'yxat
     * aylanmas, belgi ko'chmas edi.
     */
    public void dragStart(double nx, double ny) {
        float x = px(nx), y = py(ny);
        Path p = new Path();
        p.moveTo(x, y);
        lastX = x;
        lastY = y;
        stroke = new GestureDescription.StrokeDescription(p, 0, 80, true);
        dispatch(stroke);
    }

    public void dragMove(double nx, double ny) {
        if (stroke == null) {
            dragStart(nx, ny);
            return;
        }
        float x = px(nx), y = py(ny);
        Path p = new Path();
        p.moveTo(lastX, lastY);
        p.lineTo(x, y);
        lastX = x;
        lastY = y;
        stroke = stroke.continueStroke(p, 0, 40, true);
        dispatch(stroke);
    }

    public void dragEnd() {
        if (stroke == null) return;
        Path p = new Path();
        p.moveTo(lastX, lastY);
        p.lineTo(lastX, lastY);
        GestureDescription.StrokeDescription last = stroke.continueStroke(p, 0, 40, false);
        stroke = null;
        dispatch(last);
    }

    /** Aylantirish: barmoq bilan surish. */
    public void scroll(double amount) {
        measureScreen();
        float cx = width / 2f;
        float from = height * (amount > 0 ? 0.35f : 0.65f);
        float to = from + (float) (amount * height * 0.12f);
        to = Math.max(10, Math.min(height - 10, to));
        Path p = new Path();
        p.moveTo(cx, from);
        p.lineTo(cx, to);
        dispatch(new GestureDescription.StrokeDescription(p, 0, 180));
    }

    private void dispatch(GestureDescription.StrokeDescription s) {
        try {
            GestureDescription.Builder b = new GestureDescription.Builder();
            b.addStroke(s);
            dispatchGesture(b.build(), null, null);
        } catch (Exception e) {
            Log.w(TAG, "imo-ishora yuborilmadi: " + e);
        }
    }

    // ------------------------------------------------------- tizim tugmalari

    public boolean global(String name) {
        int action;
        switch (name) {
            case "back":    action = GLOBAL_ACTION_BACK; break;
            case "home":    action = GLOBAL_ACTION_HOME; break;
            case "recents": action = GLOBAL_ACTION_RECENTS; break;
            case "notifications": action = GLOBAL_ACTION_NOTIFICATIONS; break;
            case "lock":
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    action = GLOBAL_ACTION_LOCK_SCREEN;
                    break;
                }
                return false;
            default: return false;
        }
        return performGlobalAction(action);
    }

    // ------------------------------------------------------------ matn

    /**
     * Matnni faol maydonga qo'shadi.
     *
     * Klaviatura hodisasini yuborishning iloji yo'q, shuning uchun
     * fokusdagi maydonning matni to'g'ridan-to'g'ri o'zgartiriladi.
     * Bu barcha maydonlarda ham ishlamaydi (masalan o'yinlarda), lekin
     * oddiy kiritish maydonlari uchun yetarli.
     */
    public boolean typeText(String text) {
        AccessibilityNodeInfo node = focusedEditable();
        if (node == null) return false;
        CharSequence existing = node.getText();
        String value = (existing == null ? "" : existing.toString()) + text;
        return setText(node, value);
    }

    public boolean backspace() {
        AccessibilityNodeInfo node = focusedEditable();
        if (node == null) return false;
        CharSequence existing = node.getText();
        if (existing == null || existing.length() == 0) return true;
        return setText(node, existing.subSequence(0, existing.length() - 1).toString());
    }

    private boolean setText(AccessibilityNodeInfo node, String value) {
        Bundle args = new Bundle();
        args.putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
        boolean ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        node.recycle();
        return ok;
    }

    private AccessibilityNodeInfo focusedEditable() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return null;
        AccessibilityNodeInfo focus = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        root.recycle();
        if (focus == null || !focus.isEditable()) {
            if (focus != null) focus.recycle();
            return null;
        }
        return focus;
    }
}
