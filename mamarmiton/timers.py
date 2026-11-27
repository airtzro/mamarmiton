"""Find cooking durations in French step text ("25 minutes", "1 h 30", "une demi-heure"…).

Pure functions, no UI: :func:`find_durations` returns the spans of the text that
hold a duration together with its length in seconds (a range such as
"10 à 15 minutes" uses its upper bound).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER_WORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "quinze": 15, "vingt": 20, "trente": 30,
    "quarante": 40, "cinquante": 50, "soixante": 60,
}
_WORD = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_NUM = r"\d+(?:[.,]\d+)?"
_AMOUNT = rf"(?:{_NUM}|{_WORD})"
_LEFT = r"(?<![\w.,/])"
_HOUR = r"(?:heures?|h)(?![^\W\d_])"
_MIN = r"(?:minutes?|min|mn)(?![^\W\d_])"
_SEC = r"(?:secondes?|sec)(?![^\W\d_])"
_UNIT = rf"(?:{_HOUR}|{_MIN}|{_SEC})"
_APOS = r"['’]"
_SEP = r"[\s  ]*"

SECONDS_PER = {"h": 3600, "m": 60, "s": 1}


@dataclass(frozen=True)
class Duration:
    start: int
    end: int
    seconds: int

    def text_in(self, text: str) -> str:
        return text[self.start:self.end]


def _value(raw: str) -> float:
    raw = raw.lower()
    if raw in NUMBER_WORDS:
        return NUMBER_WORDS[raw]
    return float(raw.replace(",", "."))


def _unit_seconds(unit: str) -> int:
    unit = unit.lower()
    if unit.startswith("h"):
        return 3600
    if unit.startswith("s"):
        return 1
    return 60


# (compiled pattern, function(match) -> seconds), tried in this order; a later
# match may not overlap an earlier one.
def _hours_minutes(m: re.Match) -> int:
    return round(_value(m["h"]) * 3600) + int(m["m"]) * 60


def _range(m: re.Match) -> int:
    return round(max(_value(m["a"]), _value(m["b"])) * _unit_seconds(m["u"]))


def _plain(m: re.Match) -> int:
    return round(_value(m["n"]) * _unit_seconds(m["u"]))


def _hour_and_half(m: re.Match) -> int:
    return round(_value(m["n"]) * 3600) + 1800


def _fixed(seconds: int):
    return lambda _m: seconds


_PATTERNS: list[tuple[re.Pattern, object]] = [
    # "1 h 30", "1h30", "1 heure 30 minutes"
    (re.compile(rf"{_LEFT}(?P<h>{_AMOUNT}){_SEP}{_HOUR}{_SEP}(?P<m>\d{{1,2}})(?!\d)(?:{_SEP}{_MIN})?", re.I),
     _hours_minutes),
    # "1 heure et demie", "une heure et demie"
    (re.compile(rf"{_LEFT}(?P<n>{_AMOUNT}){_SEP}heures?\s+et\s+demie\b", re.I), _hour_and_half),
    # "10 à 15 minutes", "10-15 min", "1 à 2 heures"
    (re.compile(rf"{_LEFT}(?P<a>{_AMOUNT}){_SEP}(?:à|a|-|–|—|ou){_SEP}(?P<b>{_AMOUNT}){_SEP}(?P<u>{_UNIT})", re.I),
     _range),
    (re.compile(rf"{_LEFT}trois\s+quarts?\s+d{_APOS}\s*heure\b", re.I), _fixed(2700)),
    (re.compile(rf"{_LEFT}(?:un\s+)?quart\s+d{_APOS}\s*heure\b", re.I), _fixed(900)),
    (re.compile(rf"{_LEFT}(?:une?\s+)?demi[\s-]heure\b", re.I), _fixed(1800)),
    # "25 minutes", "2 heures", "30 secondes", "une heure", "1,5 heure"
    (re.compile(rf"{_LEFT}(?P<n>{_AMOUNT}){_SEP}(?P<u>{_UNIT})", re.I), _plain),
]


def find_durations(text: str) -> list[Duration]:
    """Durations found in ``text``, in reading order (zero-length ones are ignored)."""
    found: list[Duration] = []
    for pattern, seconds_of in _PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < d.end and d.start < m.end() for d in found):
                continue
            seconds = int(seconds_of(m))
            if seconds > 0:
                found.append(Duration(m.start(), m.end(), seconds))
    return sorted(found, key=lambda d: d.start)


def format_clock(seconds: int) -> str:
    """``1499`` -> ``24:59``; an hour or more -> ``1:05:00``."""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_short(seconds: int) -> str:
    """Compact label for a button: ``25 min``, ``1 h 30``, ``30 s``."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d}" if m else f"{h} h"
    return f"{m} min" if not s else f"{m} min {s:02d}"
