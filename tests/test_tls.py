"""The urllib calls must trust GitHub even when the system has no CA store where OpenSSL looks
(the Ubuntu-built Linux binary on Arch: /usr/lib/ssl does not exist)."""

import ssl

import certifi

from mamarmiton import tls


def test_falls_back_to_certifi_when_the_system_store_is_empty(monkeypatch):
    real = ssl.create_default_context
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        if kwargs.get("cafile"):
            return real(*args, **kwargs)
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # no CA loaded, like the binary on Arch

    monkeypatch.setattr(ssl, "create_default_context", fake)
    tls.context.cache_clear()
    try:
        ctx = tls.context()
    finally:
        tls.context.cache_clear()
    assert calls[-1] == {"cafile": certifi.where()}
    assert ctx.cert_store_stats()["x509_ca"] > 0


def test_keeps_the_system_store_when_it_has_certificates():
    tls.context.cache_clear()
    try:
        assert tls.context().cert_store_stats()["x509_ca"] > 0
    finally:
        tls.context.cache_clear()
