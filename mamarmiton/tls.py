"""One SSL context for the ``urllib`` calls (update check and download, web page import).

The Linux binary is built on Ubuntu and carries Ubuntu's OpenSSL, which looks for the trusted
certificates in ``/usr/lib/ssl``. Other distributions (Arch, Fedora…) keep them elsewhere, so every
https request failed there and the app said "vérifiez la connexion à internet". When the system
store gives no certificate, use certifi's bundle (shipped with httpx, already in the build).
"""

from __future__ import annotations

import functools
import logging
import ssl

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0):
        return ctx
    try:
        import certifi
    except ImportError:
        log.warning("No system CA certificates found and certifi is missing: https will fail")
        return ctx
    log.info("No system CA certificates found, using certifi's bundle")
    return ssl.create_default_context(cafile=certifi.where())
