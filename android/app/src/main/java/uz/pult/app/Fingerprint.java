package uz.pult.app;

import android.net.http.SslCertificate;
import android.os.Bundle;

import java.security.MessageDigest;

/**
 * Sertifikat izini hisoblash.
 *
 * Format agent ko'rsatadigani bilan bir xil: SHA-256 ning birinchi
 * sakkiz bayti, ikkilik sonlar ikki nuqta bilan ajratilgan. Shunda
 * foydalanuvchi telefondagi izni kompyuterdagisi bilan ko'z bilan
 * solishtira oladi.
 */
public final class Fingerprint {

    private Fingerprint() {
    }

    /** To'liq iz (hex, katta harflar, ajratkichsiz) - taqqoslash uchun. */
    public static String of(SslCertificate cert) {
        byte[] der = der(cert);
        if (der == null) return "";
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(der);
            StringBuilder sb = new StringBuilder(hash.length * 2);
            for (byte b : hash) sb.append(String.format("%02X", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** Ko'rsatish uchun qisqartirilgan ko'rinish: AA:BB:CC:... (8 bayt). */
    public static String shortForm(String full) {
        if (full == null || full.length() < 16) return full == null ? "" : full;
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 16; i += 2) {
            if (sb.length() > 0) sb.append(':');
            sb.append(full, i, i + 2);
        }
        return sb.toString();
    }

    /**
     * Sertifikatning xom (DER) ko'rinishi.
     *
     * SslCertificate uni to'g'ridan-to'g'ri bermaydi, lekin saveState()
     * ichida x509 baytlari bor. Bu Android'da ish beradigan yagona
     * ochiq yo'l.
     */
    private static byte[] der(SslCertificate cert) {
        if (cert == null) return null;
        try {
            Bundle b = SslCertificate.saveState(cert);
            if (b == null) return null;
            return b.getByteArray("x509-certificate");
        } catch (Exception e) {
            return null;
        }
    }
}
