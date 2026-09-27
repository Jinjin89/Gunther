"""Persistent private PKI for Gunther's LAN-only mobile gateway.

The local CA is deliberately long lived: changing it would invalidate every
device that trusted the workspace.  Leaf certificates are safe to renew or
reissue whenever the host's LAN addresses change.
"""

from __future__ import annotations

import ipaddress
import os
import re
import secrets
import ssl
import stat
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_CERTIFICATE_FILE_NAME = "mobile-gateway-ca.pem"
CA_PRIVATE_KEY_FILE_NAME = "mobile-gateway-ca-key.pem"
SERVER_CERTIFICATE_FILE_NAME = "mobile-gateway-server.pem"
SERVER_PRIVATE_KEY_FILE_NAME = "mobile-gateway-server-key.pem"

_CA_COMMON_NAME = "Gunther Local Mobile Gateway CA"
_CA_LIFETIME = timedelta(days=3650)
_SERVER_LIFETIME = timedelta(days=397)
_SERVER_RENEWAL_WINDOW = timedelta(days=30)
_CLOCK_SKEW = timedelta(minutes=5)
_HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class MobileGatewayPkiError(RuntimeError):
    """Base error for local mobile-gateway certificate management."""


class MobileGatewayPkiStateError(MobileGatewayPkiError):
    """Raised when persisted CA state is incomplete or cannot be trusted."""


@dataclass(frozen=True, slots=True)
class MobileGatewayTlsMaterial:
    """Paths and public certificate material needed by the gateway and clients."""

    ca_certificate_path: Path
    ca_private_key_path: Path
    server_certificate_path: Path
    server_private_key_path: Path
    ca_certificate_pem: str
    server_certificate_pem: str
    ca_sha256_fingerprint: str
    server_sha256_fingerprint: str
    dns_names: tuple[str, ...]
    ip_addresses: tuple[str, ...]
    server_not_after: datetime

    def create_server_ssl_context(self) -> ssl.SSLContext:
        """Build a TLS server context without exposing either private key."""

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(
            certfile=str(self.server_certificate_path),
            keyfile=str(self.server_private_key_path),
        )
        return context


def ensure_mobile_gateway_pki(
    storage_dir: Path,
    *,
    hostname: str,
    private_ip_addresses: Iterable[str | ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> MobileGatewayTlsMaterial:
    """Load or create the persistent CA and a leaf cert for the requested SANs.

    The CA is never replaced implicitly.  Incomplete, corrupt, expired, or
    mismatched CA files raise :class:`MobileGatewayPkiStateError`, because an
    automatic replacement would silently break trust on paired devices.
    A missing or unsuitable server certificate is safely reissued by the same
    CA, reusing its existing private key whenever that key is still readable.
    """

    directory = storage_dir.expanduser().resolve()
    _prepare_storage_directory(directory)
    normalized_hostname = _normalize_local_hostname(hostname)
    normalized_addresses = _normalize_private_addresses(private_ip_addresses)

    ca_certificate_path = directory / CA_CERTIFICATE_FILE_NAME
    ca_private_key_path = directory / CA_PRIVATE_KEY_FILE_NAME
    server_certificate_path = directory / SERVER_CERTIFICATE_FILE_NAME
    server_private_key_path = directory / SERVER_PRIVATE_KEY_FILE_NAME

    ca_private_key, ca_certificate = _ensure_ca(
        ca_private_key_path,
        ca_certificate_path,
    )
    server_private_key = _load_server_key_or_none(server_private_key_path)
    if server_private_key is None:
        server_private_key = ec.generate_private_key(ec.SECP256R1())
        _atomic_write(
            server_private_key_path,
            _private_key_pem(server_private_key),
            mode=0o600,
        )

    server_certificate = _load_server_certificate_or_none(server_certificate_path)
    if not _server_certificate_is_current(
        server_certificate,
        server_private_key,
        ca_certificate,
        normalized_hostname,
        normalized_addresses,
    ):
        server_certificate = _create_server_certificate(
            server_private_key,
            ca_private_key,
            ca_certificate,
            normalized_hostname,
            normalized_addresses,
        )
        _atomic_write(
            server_certificate_path,
            server_certificate.public_bytes(serialization.Encoding.PEM),
            mode=0o644,
        )

    _enforce_mode(ca_private_key_path, 0o600)
    _enforce_mode(server_private_key_path, 0o600)
    _enforce_mode(ca_certificate_path, 0o644)
    _enforce_mode(server_certificate_path, 0o644)

    return MobileGatewayTlsMaterial(
        ca_certificate_path=ca_certificate_path,
        ca_private_key_path=ca_private_key_path,
        server_certificate_path=server_certificate_path,
        server_private_key_path=server_private_key_path,
        ca_certificate_pem=ca_certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        server_certificate_pem=server_certificate.public_bytes(serialization.Encoding.PEM).decode(
            "ascii"
        ),
        ca_sha256_fingerprint=_sha256_fingerprint(ca_certificate),
        server_sha256_fingerprint=_sha256_fingerprint(server_certificate),
        dns_names=(normalized_hostname,),
        ip_addresses=tuple(str(address) for address in normalized_addresses),
        server_not_after=server_certificate.not_valid_after_utc,
    )


def _prepare_storage_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir():
        raise MobileGatewayPkiStateError(f"PKI storage is not a directory: {directory}")
    _enforce_mode(directory, 0o700)


def _normalize_local_hostname(hostname: str) -> str:
    candidate = hostname.strip().rstrip(".")
    try:
        normalized = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("Mobile gateway hostname is not a valid DNS name") from error
    labels = normalized.split(".")
    if (
        len(labels) < 2
        or labels[-1] != "local"
        or len(normalized) > 253
        or any(_HOST_LABEL_PATTERN.fullmatch(label) is None for label in labels)
    ):
        raise ValueError("Mobile gateway hostname must be a valid .local DNS name")
    return normalized


def _normalize_private_addresses(
    addresses: Iterable[str | ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    normalized: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError(f"Invalid mobile gateway IP address: {value}") from error
        if (
            not address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
        ):
            raise ValueError(f"Mobile gateway address must be a private LAN IP: {address}")
        normalized.add(address)
    return tuple(sorted(normalized, key=lambda item: (item.version, int(item))))


def _ensure_ca(
    private_key_path: Path,
    certificate_path: Path,
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key_exists = private_key_path.exists()
    certificate_exists = certificate_path.exists()
    if key_exists != certificate_exists:
        raise MobileGatewayPkiStateError(
            "Local CA state is incomplete; refusing to replace a trusted CA"
        )
    if not key_exists:
        private_key = ec.generate_private_key(ec.SECP256R1())
        certificate = _create_ca_certificate(private_key)
        _atomic_write(private_key_path, _private_key_pem(private_key), mode=0o600)
        _atomic_write(
            certificate_path,
            certificate.public_bytes(serialization.Encoding.PEM),
            mode=0o644,
        )
        return private_key, certificate

    try:
        private_key = serialization.load_pem_private_key(
            _read_regular_file(private_key_path),
            password=None,
        )
        certificate = x509.load_pem_x509_certificate(_read_regular_file(certificate_path))
    except (OSError, ValueError, TypeError) as error:
        raise MobileGatewayPkiStateError(
            "Local CA files are unreadable or invalid; refusing to replace them"
        ) from error
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise MobileGatewayPkiStateError("Local CA private key has an unsupported type")
    _validate_ca(private_key, certificate)
    return private_key, certificate


def _validate_ca(
    private_key: ec.EllipticCurvePrivateKey,
    certificate: x509.Certificate,
) -> None:
    if _public_key_der(private_key.public_key()) != _public_key_der(certificate.public_key()):
        raise MobileGatewayPkiStateError("Local CA certificate does not match its key")
    try:
        basic_constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        _verify_certificate_signature(certificate, certificate)
    except (InvalidSignature, ValueError, x509.ExtensionNotFound) as error:
        raise MobileGatewayPkiStateError("Local CA certificate is not a valid CA") from error
    now = datetime.now(UTC)
    if (
        certificate.subject != certificate.issuer
        or not basic_constraints.ca
        or not key_usage.key_cert_sign
        or certificate.not_valid_before_utc > now
        or certificate.not_valid_after_utc <= now
    ):
        raise MobileGatewayPkiStateError("Local CA certificate is not currently trustworthy")


def _create_ca_certificate(
    private_key: ec.EllipticCurvePrivateKey,
) -> x509.Certificate:
    now = datetime.now(UTC)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Gunther"),
            x509.NameAttribute(NameOID.COMMON_NAME, _CA_COMMON_NAME),
        ]
    )
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(now + _CA_LIFETIME)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )


def _load_server_key_or_none(path: Path) -> ec.EllipticCurvePrivateKey | None:
    if not path.exists():
        return None
    try:
        private_key = serialization.load_pem_private_key(
            _read_regular_file(path),
            password=None,
        )
    except (OSError, ValueError, TypeError):
        return None
    return private_key if isinstance(private_key, ec.EllipticCurvePrivateKey) else None


def _load_server_certificate_or_none(path: Path) -> x509.Certificate | None:
    if not path.exists():
        return None
    try:
        return x509.load_pem_x509_certificate(_read_regular_file(path))
    except (OSError, ValueError):
        return None


def _server_certificate_is_current(
    certificate: x509.Certificate | None,
    private_key: ec.EllipticCurvePrivateKey,
    ca_certificate: x509.Certificate,
    hostname: str,
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
) -> bool:
    if certificate is None:
        return False
    now = datetime.now(UTC)
    if (
        certificate.not_valid_before_utc > now
        or certificate.not_valid_after_utc <= now + _SERVER_RENEWAL_WINDOW
        or certificate.issuer != ca_certificate.subject
        or _public_key_der(certificate.public_key()) != _public_key_der(private_key.public_key())
    ):
        return False
    try:
        basic_constraints = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        extended_key_usage = certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        ).value
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        _verify_certificate_signature(certificate, ca_certificate)
    except (InvalidSignature, ValueError, x509.ExtensionNotFound):
        return False
    actual_dns_names = tuple(sorted(san.get_values_for_type(x509.DNSName)))
    actual_addresses = tuple(
        sorted(
            san.get_values_for_type(x509.IPAddress),
            key=lambda item: (item.version, int(item)),
        )
    )
    return (
        not basic_constraints.ca
        and ExtendedKeyUsageOID.SERVER_AUTH in extended_key_usage
        and actual_dns_names == (hostname,)
        and actual_addresses == addresses
    )


def _create_server_certificate(
    server_private_key: ec.EllipticCurvePrivateKey,
    ca_private_key: ec.EllipticCurvePrivateKey,
    ca_certificate: x509.Certificate,
    hostname: str,
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
) -> x509.Certificate:
    now = datetime.now(UTC)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Gunther"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ]
    )
    san_names: list[x509.GeneralName] = [x509.DNSName(hostname)]
    san_names.extend(x509.IPAddress(address) for address in addresses)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(server_private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(now + _SERVER_LIFETIME)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_private_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_private_key.public_key()),
            critical=False,
        )
        .sign(ca_private_key, hashes.SHA256())
    )


def _verify_certificate_signature(
    certificate: x509.Certificate,
    issuer: x509.Certificate,
) -> None:
    issuer_public_key = issuer.public_key()
    if not isinstance(issuer_public_key, ec.EllipticCurvePublicKey):
        raise ValueError("Unsupported certificate issuer key")
    issuer_public_key.verify(
        certificate.signature,
        certificate.tbs_certificate_bytes,
        ec.ECDSA(certificate.signature_hash_algorithm),
    )


def _private_key_pem(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_key_der(public_key: object) -> bytes:
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        return b""
    return public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _sha256_fingerprint(certificate: x509.Certificate) -> str:
    digest = certificate.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(digest[index : index + 2] for index in range(0, len(digest), 2))


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise MobileGatewayPkiStateError(f"PKI path is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, data: bytes, *, mode: int) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _enforce_mode(path, mode)
        _fsync_directory(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _enforce_mode(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        os.chmod(path, mode)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        with suppress(OSError):
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
