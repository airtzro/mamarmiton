"""Reading and scaling ingredient quantities such as ``"1/2 sachet"`` or ``"2,5 kg"``.

Scaling never edits the stored recipe; it returns new strings for display.
"""

from __future__ import annotations

import re
from fractions import Fraction

UNICODE_FRACTIONS = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"}
NUMBER_WORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "douze": 12,
}

UNITS = [
    "kg", "g", "gr", "grammes?", "mg", "l", "litres?", "dl", "cl", "ml",
    r"c\.?\s?à\.?\s?s\.?", r"c\.?\s?à\.?\s?c\.?", "cas", "cac", "cs", "cc",
    r"cuill(?:è|e)res?(?:\s+à\s+(?:soupe|café|cafe))?", r"cuil\.?(?:\s+à\s+(?:soupe|café|cafe))?",
    "pincées?", "sachets?", "tranches?", "morceaux?", "morceau", "verres?", "tasses?", "bols?",
    "boîtes?", "boites?", "gousses?", "brins?", "feuilles?", "bottes?", "noix", "noisettes?",
    "pots?", "briques?", "paquets?", "tablettes?", "carrés?", "poignées?", "filets?", "zestes?",
]
UNIT_RE = r"(?:" + "|".join(UNITS) + r")\b\.?"

# A number: "2", "2,5", "2.5", "1/2", "1 1/2"
_NUM = r"\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?"
_MIXED = r"\d+\s+\d+\s*/\s*\d+"
NUMBER_RE = re.compile(rf"(?P<num>{_MIXED}|{_NUM})(?:\s*(?:-|à)\s*(?P<num2>{_MIXED}|{_NUM}))?")


def _normalize(text: str) -> str:
    for char, ascii_frac in UNICODE_FRACTIONS.items():
        text = re.sub(rf"(\d)\s*{char}", rf"\1 {ascii_frac}", text)
        text = text.replace(char, ascii_frac)
    return text


def parse_number(token: str) -> Fraction | None:
    token = token.strip().replace(",", ".")
    try:
        if re.fullmatch(r"\d+\s+\d+\s*/\s*\d+", token):
            whole, frac = token.split(None, 1)
            return Fraction(int(whole)) + Fraction(frac.replace(" ", ""))
        if "/" in token:
            value = Fraction(token.replace(" ", ""))
        else:
            value = Fraction(token)
    except (ValueError, ZeroDivisionError):
        return None
    return value


METRIC_RE = re.compile(r"^\s*(?:kg|g|gr|grammes?|mg|l|litres?|dl|cl|ml)\b", re.IGNORECASE)


def format_number(value: Fraction | float, decimal: bool = False) -> str:
    """Readable number: ``1/2``, ``1 1/4``, ``2,5`` (``decimal=True`` avoids fractions)."""
    value = Fraction(value).limit_denominator(1000)
    if value <= 0:
        return "0"
    whole = int(value)
    rest = value - whole
    if value >= 10:
        return str(round(value))
    if decimal:
        text = f"{float(value):.2f}".rstrip("0").rstrip(".")
        return text.replace(".", ",")
    for frac in (Fraction(1, 4), Fraction(1, 3), Fraction(1, 2), Fraction(2, 3), Fraction(3, 4)):
        if abs(rest - frac) < Fraction(1, 40):
            frac_txt = f"{frac.numerator}/{frac.denominator}"
            return frac_txt if whole == 0 else f"{whole} {frac_txt}"
    if rest < Fraction(1, 20):
        return str(whole)
    if rest > Fraction(19, 20):
        return str(whole + 1)
    return f"{float(value):.1f}".replace(".", ",")


def scale_quantity(quantity: str, factor: float | Fraction) -> str:
    """Scale the first number (or number range) in ``quantity`` by ``factor``.

    ``"200 g"`` x 1.5 -> ``"300 g"``; ``"1/2 sachet"`` x 2 -> ``"1 sachet"``;
    ``"une pincée"`` x 2 -> ``"2 pincée"``; ``"sel"`` stays ``"sel"``.
    """
    if not quantity or factor == 1:
        return quantity
    factor = Fraction(factor).limit_denominator(1000)
    text = _normalize(quantity)

    match = NUMBER_RE.search(text)
    if match is None:
        word = re.match(r"\s*(\w+)\b", text)
        if word and word.group(1).lower() in NUMBER_WORDS:
            scaled = format_number(NUMBER_WORDS[word.group(1).lower()] * factor)
            return text[: word.start(1)] + scaled + text[word.end(1):]
        return quantity

    first = parse_number(match.group("num"))
    if first is None:
        return quantity
    decimal = bool(METRIC_RE.match(text[match.end():])) or bool(re.search(r"[.,]", match.group(0)))
    replacement = format_number(first * factor, decimal)
    if match.group("num2"):
        second = parse_number(match.group("num2"))
        if second is not None:
            sep = "-" if "-" in match.group(0) else " à "
            replacement += sep + format_number(second * factor, decimal)
    return text[: match.start()] + replacement + text[match.end():]


_LEAD_RE = re.compile(
    rf"^\s*(?P<qty>(?:{_MIXED}|{_NUM})(?:\s*(?:-|à)\s*(?:{_MIXED}|{_NUM}))?(?:\s*{UNIT_RE})?)"
    r"\s*(?:de\s+|d['’]\s*|des\s+)?(?P<name>.*)$",
    re.IGNORECASE,
)
_TRAIL_RE = re.compile(rf"^(?P<name>.*?)[\s(:]+(?P<qty>(?:{_MIXED}|{_NUM})(?:\s*{UNIT_RE})?)\)?\s*$", re.IGNORECASE)
_WORD_RE = re.compile(
    rf"^\s*(?P<qty>(?:{'|'.join(NUMBER_WORDS)})\b(?:\s*{UNIT_RE})?)\s*(?:de\s+|d['’]\s*|des\s+)?(?P<name>.+)$",
    re.IGNORECASE,
)


def split_quantity_and_name(line: str) -> tuple[str, str]:
    """Split ``"500 g de farine"`` into ``("500 g", "farine")``.

    Returns ``("", line)`` when no quantity is found.
    """
    text = _normalize(line.strip())
    for regex in (_LEAD_RE, _WORD_RE, _TRAIL_RE):
        match = regex.match(text)
        if match and match.group("name").strip():
            return match.group("qty").strip(), match.group("name").strip(" -,.")
    return "", line.strip()
