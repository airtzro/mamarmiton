"""Fetch a web page for the recipe import without letting a URL reach the NAS or its network.

The server runs at home, so "import this address" must never become "read the router's admin page":

* only http/https, only ports 80 and 443;
* the host name is resolved here and every address must be a public one (no private, loopback,
  link-local, multicast, reserved or unspecified address, nor an IPv4 address hidden in an IPv6 one);
* the connection goes to that vetted address (the name is only used for the Host header and the TLS
  certificate), so a DNS answer cannot change between the check and the connection;
* redirects are followed here, one by one, and each hop goes through the same checks.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

from mamarmiton import tls
from mamarmiton.importers.web import (MAX_IMAGE_BYTES, MAX_PAGE_BYTES, USER_AGENT, WebImportError,
                                        decode_page)

class Refused(WebImportError):
    """The address is not one we may connect to (nothing was contacted)."""


TIMEOUT = 15  # seconds per network step
MAX_REDIRECTS = 5
PORTS = {"http": 80, "https": 443}
REDIRECTS = (301, 302, 303, 307, 308)

NOT_ALLOWED = "Cette adresse n'est pas autorisée : seuls les sites publics d'internet peuvent être lus."
UNREACHABLE = ("Impossible de joindre le site. Vérifiez l'adresse et votre connexion internet, "
               "puis réessayez.")
TOO_BIG = "Cette page est trop volumineuse pour être lue."
SLOW = "Le site met trop de temps à répondre."

_BLOCKED_V6 = [ipaddress.ip_network(n) for n in (
    "64:ff9b::/96", "64:ff9b:1::/48",  # NAT64 (embeds any IPv4 address)
    "2002::/16",                       # 6to4
    "2001::/32",                       # Teredo
)]


def is_public(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public(ip.ipv4_mapped)
        if any(ip in net for net in _BLOCKED_V6):
            return False
    return ip.is_global and not ip.is_multicast


def _resolve(host: str, port: int) -> list[str]:
    """Every address of ``host`` that is safe to connect to; raises when any of them is not."""
    try:
        found = {info[4][0].split("%")[0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise WebImportError(UNREACHABLE) from exc
    if not found:
        raise WebImportError(UNREACHABLE)
    if not all(is_public(ip) for ip in found):  # a name with one private address is refused as a whole
        raise Refused(NOT_ALLOWED)
    return sorted(found)


def check_url(url: str) -> tuple[str, str, int]:
    """(scheme, host, port) of an allowed address, else WebImportError."""
    try:
        parts = urllib.parse.urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise Refused(NOT_ALLOWED) from exc
    scheme = parts.scheme.lower()
    if scheme not in PORTS or not host or parts.username is not None:
        raise Refused(NOT_ALLOWED)
    port = port or PORTS[scheme]
    if port not in PORTS.values():
        raise Refused(NOT_ALLOWED)
    return scheme, host, port


def _open_socket(ip: str, port: int, host: str, tls_on: bool, timeout: float) -> socket.socket:
    sock = socket.create_connection((ip, port), timeout)
    if tls_on:
        try:
            return tls.context().wrap_socket(sock, server_hostname=host)  # certificate checked against the name
        except Exception:
            sock.close()
            raise
    return sock


class _Pinned(http.client.HTTPConnection):
    """An HTTP(S) connection that goes to a chosen address while ``host`` stays the name of the site."""

    def __init__(self, host: str, port: int, ip: str, tls_on: bool, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._ip, self._tls_on = ip, tls_on

    def connect(self) -> None:
        self.sock = _open_socket(self._ip, self.port, self.host, self._tls_on, self.timeout)


def _get_once(url: str, accept: str, limit: int, deadline: float) -> tuple[int, dict, bytes]:
    scheme, host, port = check_url(url)
    ips = _resolve(host, port)
    parts = urllib.parse.urlsplit(url)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    headers = {"Host": f"[{host}]" if ":" in host else host,  # the port is always the scheme's own
               "User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
               "Accept-Encoding": "identity", "Connection": "close"}
    last: Exception | None = None
    for ip in ips:
        left = deadline - time.monotonic()
        if left <= 0:
            raise WebImportError(SLOW)
        conn = _Pinned(host, port, ip, scheme == "https", min(TIMEOUT, left))
        try:
            conn.request("GET", target, headers=headers)
            response = conn.getresponse()
            status, got = response.status, {k.lower(): v for k, v in response.getheaders()}
            if status in REDIRECTS:
                return status, got, b""
            data = bytearray()
            while len(data) <= limit:
                if time.monotonic() > deadline:
                    raise WebImportError(SLOW)
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                data += chunk
            if len(data) > limit:
                raise WebImportError(TOO_BIG)
            return status, got, bytes(data)
        except WebImportError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:  # includes timeouts
            last = exc
        finally:
            conn.close()
    raise WebImportError(UNREACHABLE) from last


def fetch(url: str, accept: str, limit: int, deadline: float) -> tuple[bytes, str]:
    """GET ``url`` (following at most MAX_REDIRECTS redirects) -> (body, charset). ``deadline`` is monotonic."""
    for _ in range(MAX_REDIRECTS + 1):
        status, headers, data = _get_once(url, accept, limit, deadline)
        if status in REDIRECTS:
            location = headers.get("location", "")
            if not location:
                raise WebImportError(UNREACHABLE)
            url = urllib.parse.urljoin(url, location)  # checked again at the top of the next hop
            continue
        if status in (401, 403, 429):
            raise WebImportError("Ce site refuse l'accès automatique à cette page. "
                                 "Essayez de l'enregistrer en PDF, puis d'importer le PDF.")
        if status in (404, 410):
            raise WebImportError("Cette page n'existe pas (erreur 404). Vérifiez l'adresse.")
        if status != 200:
            raise WebImportError(f"Le site a répondu par une erreur ({status}).")
        message = http.client.HTTPMessage()
        message["content-type"] = headers.get("content-type", "")
        return data, message.get_content_charset() or ""
    raise WebImportError("Cette adresse renvoie vers trop de pages. Essayez l'adresse de la recette elle-même.")


def fetch_page(url: str, deadline: float) -> str:
    data, charset = fetch(url, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", MAX_PAGE_BYTES, deadline)
    return decode_page(data, charset)


def fetch_image(url: str, deadline: float) -> bytes:
    return fetch(url, "image/*", MAX_IMAGE_BYTES, deadline)[0]
