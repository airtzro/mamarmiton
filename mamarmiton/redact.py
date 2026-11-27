"""Hide secrets in text sent to the server (problem reports).

Standard library only: the server image ships this file without the rest of the desktop app.
"""

from __future__ import annotations

import re

MASK = "[masqué]"
_SECRET_WORDS = r"(?:password|passwd|mot de passe|mdp|token|secret|api[_-]?key|old_password|new_password|invite|code d'invitation)"
_PATTERNS = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), rf"\1 {MASK}"),
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*)[^\r\n]+"), rf"\1{MASK}"),
    (re.compile(rf"(?i)\b({_SECRET_WORDS}[\"']?\s*[:=]\s*)(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&}}\]]+)"), rf"\1{MASK}"),
    (re.compile(r"\b[A-Z2-9]{4}(?:-[A-Z2-9]{4}){3}\b"), MASK),            # invitation code
    (re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])"), MASK),  # any long opaque token
]


def redact(text: str) -> str:
    """Hide anything that looks like a bearer token, a password or an invitation code."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text or "")
    return text
