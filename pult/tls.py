"""
Generating a self-signed certificate.

Why it is needed: browsers only grant video decoding (WebCodecs),
service workers and wake lock in a "secure context". localhost is
exempt, but the phone reaches the computer over a LAN IP, which is not
considered secure. Over plain HTTP the app therefore does not work on
the phone at all, which makes HTTPS mandatory.

Because the certificate signs itself, the browser warns on the first
visit. The user clicks through once, the page becomes a secure context,
and everything works from then on.
"""
from __future__ import annotations

import datetime
import ipaddress
import logging
import socket
import ssl
from pathlib import Path

log = logging.getLogger("pult.tls")

CERT_NAME = "cert.pem"
KEY_NAME = "key.pem"
VALID_DAYS = 3650


def local_ips() -> list[str]:
    """The computer's local IP addresses."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if ":" not in addr or addr.count(":") > 1:  # IPv4 or IPv6
                ips.add(addr)
    except Exception:
        pass
    # The address on the route out to the internet - the most reliable way
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips)


def _san_entries():
    from cryptography import x509

    names: list = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    try:
        host = socket.gethostname()
        names.append(x509.DNSName(host))
        names.append(x509.DNSName(f"{host}.local"))
    except Exception:
        pass
    for ip in local_ips():
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue
    return names


def _covers_current_ips(cert_path: Path) -> bool:
    """Whether the addresses in the certificate match the current network.

    Switching Wi-Fi changes the IP and the old certificate stops
    matching - then it has to be regenerated.
    """
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if cert.not_valid_after_utc < datetime.datetime.now(datetime.timezone.utc):
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        return set(local_ips()).issubset(have)
    except Exception:
        return False


def generate(cert_path: Path, key_path: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Pult"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Pult"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(_san_entries()), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        key_path.chmod(0o600)
    except Exception:
        pass
    log.info("generated a new certificate: %s", cert_path)


def ensure(config_dir: Path) -> tuple[Path, Path]:
    """Makes sure a certificate exists, regenerating it when needed."""
    cert_path = config_dir / CERT_NAME
    key_path = config_dir / KEY_NAME
    if not cert_path.exists() or not key_path.exists():
        generate(cert_path, key_path)
    elif not _covers_current_ips(cert_path):
        log.info("network address changed - regenerating the certificate")
        generate(cert_path, key_path)
    return cert_path, key_path


def context(config_dir: Path) -> ssl.SSLContext:
    cert_path, key_path = ensure(config_dir)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


def fingerprint(config_dir: Path) -> str:
    """Certificate fingerprint, so the user can confirm they connected
    to the right computer."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        cert = x509.load_pem_x509_certificate((config_dir / CERT_NAME).read_bytes())
        raw = cert.fingerprint(hashes.SHA256())
        return ":".join(f"{b:02X}" for b in raw[:8])
    except Exception:
        return ""
