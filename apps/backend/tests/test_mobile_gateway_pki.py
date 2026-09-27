from __future__ import annotations

import ipaddress
import ssl
import stat
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from gunther.mobile_gateway_pki import (
    MobileGatewayPkiStateError,
    ensure_mobile_gateway_pki,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _public_key_bytes(path: Path) -> bytes:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def test_creates_strict_files_and_ssl_context(tmp_path: Path) -> None:
    storage = tmp_path / "pki"
    material = ensure_mobile_gateway_pki(
        storage,
        hostname="Gunther-Studio.local.",
        private_ip_addresses=["192.168.50.8"],
    )

    assert _mode(storage) == 0o700
    assert _mode(material.ca_private_key_path) == 0o600
    assert _mode(material.server_private_key_path) == 0o600
    assert _mode(material.ca_certificate_path) == 0o644
    assert _mode(material.server_certificate_path) == 0o644
    assert material.ca_certificate_pem.startswith("-----BEGIN CERTIFICATE-----")
    assert material.server_certificate_pem.startswith("-----BEGIN CERTIFICATE-----")
    assert len(material.ca_sha256_fingerprint.split(":")) == 32
    assert len(material.server_sha256_fingerprint.split(":")) == 32
    assert isinstance(material.create_server_ssl_context(), ssl.SSLContext)


def test_restart_preserves_ca_and_server_identity(tmp_path: Path) -> None:
    arguments = {
        "hostname": "gunther-studio.local",
        "private_ip_addresses": ["10.0.0.4", "192.168.1.9"],
    }
    first = ensure_mobile_gateway_pki(tmp_path / "pki", **arguments)
    first_ca_key = first.ca_private_key_path.read_bytes()
    first_server_key = first.server_private_key_path.read_bytes()

    restarted = ensure_mobile_gateway_pki(tmp_path / "pki", **arguments)

    assert restarted.ca_sha256_fingerprint == first.ca_sha256_fingerprint
    assert restarted.server_sha256_fingerprint == first.server_sha256_fingerprint
    assert restarted.ca_private_key_path.read_bytes() == first_ca_key
    assert restarted.server_private_key_path.read_bytes() == first_server_key


def test_server_san_contains_normalized_hostname_and_private_ips(tmp_path: Path) -> None:
    material = ensure_mobile_gateway_pki(
        tmp_path / "pki",
        hostname="Gunther-Studio.LOCAL",
        private_ip_addresses=["fd00::20", "192.168.1.20", "192.168.1.20"],
    )
    certificate = x509.load_pem_x509_certificate(material.server_certificate_path.read_bytes())
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value

    assert san.get_values_for_type(x509.DNSName) == ["gunther-studio.local"]
    assert san.get_values_for_type(x509.IPAddress) == [
        ipaddress.ip_address("192.168.1.20"),
        ipaddress.ip_address("fd00::20"),
    ]
    assert material.dns_names == ("gunther-studio.local",)
    assert material.ip_addresses == ("192.168.1.20", "fd00::20")


def test_san_change_reissues_only_server_certificate(tmp_path: Path) -> None:
    storage = tmp_path / "pki"
    first = ensure_mobile_gateway_pki(
        storage,
        hostname="gunther.local",
        private_ip_addresses=["192.168.1.5"],
    )
    first_ca_key = first.ca_private_key_path.read_bytes()
    first_server_key_public = _public_key_bytes(first.server_private_key_path)

    changed = ensure_mobile_gateway_pki(
        storage,
        hostname="gunther.local",
        private_ip_addresses=["192.168.1.6", "10.0.0.7"],
    )

    assert changed.ca_sha256_fingerprint == first.ca_sha256_fingerprint
    assert changed.ca_private_key_path.read_bytes() == first_ca_key
    assert changed.server_sha256_fingerprint != first.server_sha256_fingerprint
    assert _public_key_bytes(changed.server_private_key_path) == first_server_key_public
    assert changed.ip_addresses == ("10.0.0.7", "192.168.1.6")


def test_refuses_public_addresses_and_incomplete_ca_state(tmp_path: Path) -> None:
    for directory_name, rejected_address in (
        ("public", "8.8.8.8"),
        ("ipv4-link-local", "169.254.12.8"),
        ("ipv6-link-local", "fe80::20"),
    ):
        with pytest.raises(ValueError, match="private LAN IP"):
            ensure_mobile_gateway_pki(
                tmp_path / directory_name,
                hostname="gunther.local",
                private_ip_addresses=[rejected_address],
            )

    material = ensure_mobile_gateway_pki(
        tmp_path / "incomplete",
        hostname="gunther.local",
        private_ip_addresses=[],
    )
    material.ca_certificate_path.unlink()
    with pytest.raises(MobileGatewayPkiStateError, match="incomplete"):
        ensure_mobile_gateway_pki(
            tmp_path / "incomplete",
            hostname="gunther.local",
            private_ip_addresses=[],
        )
