package uz.pult.app;

import android.net.http.SslCertificate;
import android.os.Bundle;

import java.security.MessageDigest;

/**
 * Computing the certificate fingerprint.
 *
 * The format matches what the agent shows: the first eight bytes of the
 * SHA-256, in hex, separated by colons. That way the user can compare
 * the fingerprint on the phone with the one on the computer by eye.
 */
public final class Fingerprint {

    private Fingerprint() {
    }

    /** The full fingerprint (hex, upper case, no separators) for comparison. */
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

    /** The shortened form used for display: AA:BB:CC:... (8 bytes). */
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
