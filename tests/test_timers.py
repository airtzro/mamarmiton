import pytest

from mamarmiton.timers import find_durations, format_clock, format_short


@pytest.mark.parametrize("text, seconds", [
    ("Cuire 25 minutes.", 1500),
    ("Cuire 25 min à feu doux.", 1500),
    ("Laisser reposer 1 h 30.", 5400),
    ("Laisser reposer 1h30.", 5400),
    ("Cuire 1 heure 30 minutes.", 5400),
    ("Cuire 2 heures.", 7200),
    ("Cuire 1 heure.", 3600),
    ("Cuire 1,5 heure.", 5400),
    ("Saisir 30 secondes.", 30),
    ("Saisir 30 sec.", 30),
    ("Cuire 10 à 15 minutes.", 900),
    ("Cuire 10-15 min.", 900),
    ("Cuire 1 à 2 heures.", 7200),
    ("Cuire une heure.", 3600),
    ("Cuire une heure et demie.", 5400),
    ("Cuire une demi-heure.", 1800),
    ("Cuire un quart d'heure.", 900),
    ("Cuire un quart d’heure.", 900),
    ("Cuire trois quarts d'heure.", 2700),
    ("Cuire vingt minutes.", 1200),
    ("Cuire 25 minutes.", 1500),
    ("CUIRE 25 MINUTES", 1500),
])
def test_single_duration(text, seconds):
    found = find_durations(text)
    assert [d.seconds for d in found] == [seconds]


def test_span_covers_matched_text():
    text = "Cuire 10 à 15 minutes puis servir."
    (d,) = find_durations(text)
    assert d.text_in(text) == "10 à 15 minutes"


def test_hour_minutes_span():
    text = "Attendre 1 h 30 avant de servir."
    (d,) = find_durations(text)
    assert d.text_in(text) == "1 h 30"


def test_several_durations_in_reading_order():
    text = "Cuire 5 minutes, puis laisser reposer 1 heure et enfin 30 secondes au four."
    assert [d.seconds for d in find_durations(text)] == [300, 3600, 30]
    starts = [d.start for d in find_durations(text)]
    assert starts == sorted(starts)


def test_no_overlap():
    text = "Cuire 1 h 30 puis 10 à 15 minutes."
    found = find_durations(text)
    assert [d.seconds for d in found] == [5400, 900]
    assert found[0].end <= found[1].start


@pytest.mark.parametrize("text", [
    "",
    "Mélanger les ingrédients.",
    "Préchauffer le four à 180°C.",
    "Ajouter 3 pommes et 200 g de sucre.",
    "Prendre 2 heureux convives.",
    "Servir avec 5 mini-tartes.",
    "Une minuscule pincée de sel.",
    "Utiliser 0 minute.",
])
def test_no_duration(text):
    assert find_durations(text) == []


def test_fraction_is_not_a_duration():
    assert find_durations("Cuire 1/2 heure.") == []


def test_temperature_not_confused_with_time():
    text = "Cuire à 180 °C pendant 25 minutes."
    assert [d.seconds for d in find_durations(text)] == [1500]


def test_word_boundaries():
    assert find_durations("5 hamburgers") == []
    assert find_durations("5 secs") == []
    assert [d.seconds for d in find_durations("Cuire 5 min.")] == [300]
    assert [d.seconds for d in find_durations("Cuire 5 minutes!")] == [300]


@pytest.mark.parametrize("seconds, clock", [
    (0, "00:00"), (5, "00:05"), (1499, "24:59"), (3600, "1:00:00"), (5400, "1:30:00"), (-3, "00:00"),
])
def test_format_clock(seconds, clock):
    assert format_clock(seconds) == clock


@pytest.mark.parametrize("seconds, label", [
    (30, "30 s"), (1500, "25 min"), (5400, "1 h 30"), (7200, "2 h"), (90, "1 min 30"), (3660, "1 h 01"),
])
def test_format_short(seconds, label):
    assert format_short(seconds) == label
