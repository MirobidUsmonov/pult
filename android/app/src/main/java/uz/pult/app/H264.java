package uz.pult.app;

/**
 * H.264 oqimidan brauzer uchun kodek satrini oladi.
 *
 * Kompyuter tarafida ham xuddi shu hisob bor: satrni qo'lda yozib
 * qo'yish mumkin emas, chunki profil va daraja kodlagichga hamda ekran
 * o'lchamiga qarab o'zgaradi, noto'g'ri satr esa brauzerda dekoderni
 * umuman ishga tushirmaydi. SPS ning o'zidan olingani doim to'g'ri.
 */
public final class H264 {

    private H264() {
    }

    /** Masalan "avc1.42E01E". Topilmasa keng tarqalgan qiymat qaytadi. */
    public static String codecString(byte[] annexB) {
        if (annexB == null) return "avc1.42E01E";
        int i = 0;
        while (i + 4 < annexB.length) {
            int start = findStartCode(annexB, i);
            if (start < 0) break;
            int nal = start;
            if (nal >= annexB.length) break;
            int type = annexB[nal] & 0x1F;
            if (type == 7 && nal + 3 < annexB.length) {   // SPS
                return String.format("avc1.%02X%02X%02X",
                        annexB[nal + 1], annexB[nal + 2], annexB[nal + 3]);
            }
            i = nal + 1;
        }
        return "avc1.42E01E";
    }

    /** Keyingi NAL boshlanish joyini qaytaradi (boshlanish kodidan keyin). */
    private static int findStartCode(byte[] b, int from) {
        for (int i = from; i + 2 < b.length; i++) {
            if (b[i] == 0 && b[i + 1] == 0) {
                if (b[i + 2] == 1) return i + 3;
                if (i + 3 < b.length && b[i + 2] == 0 && b[i + 3] == 1) return i + 4;
            }
        }
        return -1;
    }
}
