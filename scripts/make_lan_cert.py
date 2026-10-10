"""
LAN 内で https を使うための証明書を作る(mkcert と同じ考え方)。

1. この PC だけの認証局(CA)を作る: data/certs/ca.key と ca.crt(1回だけ。あれば使い回す)
2. その CA で、サーバーの証明書を発行する: data/certs/server.key と server.crt
   名前: novelai.lan(lan-dns で付けた名前)・localhost・この PC の LAN の IPv4・127.0.0.1

スマホなどの端末には ca.crt を「CA 証明書」としてインストールする。すると、この PC のサーバー
(https://novelai.lan:5173 など)が警告なしで開け、ホーム画面へのインストール(PWA)や通知が使える。

ca.key は認証局の秘密鍵。これがあれば、どのサイトの証明書でも作れてしまうので、外に出さない
(data/ は git に入れない)。

実行方法:
  uv run python scripts/make_lan_cert.py                 # 名前は novelai.lan と、この PC の IPv4
  uv run python scripts/make_lan_cert.py --name foo.lan  # 名前を足す
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "data" / "certs"
# CA は長め、サーバーの証明書はブラウザが受け付ける上限(約 825 日)より短く
CA_DAYS = 3650
SERVER_DAYS = 800


def lan_addresses() -> list[str]:
    """この PC の IPv4(ループバックとリンクローカルを除く)。"""
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except socket.gaierror:
        pass
    # 外へ出るときに使う IP(実際には送らない)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            addresses.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in addresses if not a.startswith(("127.", "169.254.")))


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )


def load_or_create_ca() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key_path, cert_path = CERT_DIR / "ca.key", CERT_DIR / "ca.crt"
    if key_path.exists() and cert_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        return key, x509.load_pem_x509_certificate(cert_path.read_bytes())
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "novelai-userscripts-example LAN"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"novelai-userscripts-example LAN CA ({socket.gethostname()})"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(key_path, key)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"認証局(CA)を作りました: {cert_path}")
    return key, cert


def issue_server(
    ca_key: ec.EllipticCurvePrivateKey, ca_cert: x509.Certificate, names: list[str], ips: list[str]
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    sans: list[x509.GeneralName] = [x509.DNSName(n) for n in names]
    sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=SERVER_DAYS))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(CERT_DIR / "server.key", key)
    (CERT_DIR / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"サーバーの証明書を作りました(期限 {SERVER_DAYS} 日): {', '.join(names + ips)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", action="append", default=[], help="足すホスト名(何度でも)")
    args = parser.parse_args()
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = load_or_create_ca()
    names = list(dict.fromkeys(["novelai.lan", "localhost", *args.name]))
    ips = list(dict.fromkeys(["127.0.0.1", *lan_addresses()]))
    issue_server(ca_key, ca_cert, names, ips)
    print(
        "\nスマホには data/certs/ca.crt を CA 証明書としてインストールしてください(README の「スマホにインストール」)。"
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
