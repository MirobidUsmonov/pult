package uz.pult.app;

/**
 * Reads the browser's codec string out of an H.264 stream.
 *
 * The computer side does the very same calculation: the string cannot
 * be hard-coded, because the profile and level change with the encoder
 * and the screen size, and a wrong string will not start the browser's
 * decoder at all. Taken from the SPS itself, it is always right.
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

    /** Returns where the next NAL begins (just after the start code). */
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
