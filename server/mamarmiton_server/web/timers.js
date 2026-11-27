// Port of mamarmiton/timers.py: finds cooking durations in French step text
// ("25 minutes", "1 h 30", "une demi-heure"...). Pure functions, no DOM.
// Python's \w and \b are Unicode-aware, so they are spelled out with \p{L}\p{N}.

export const NUMBER_WORDS = {
  un: 1, une: 1, deux: 2, trois: 3, quatre: 4, cinq: 5, six: 6, sept: 7, huit: 8,
  neuf: 9, dix: 10, onze: 11, douze: 12, quinze: 15, vingt: 20, trente: 30,
  quarante: 40, cinquante: 50, soixante: 60,
};
const WORD = Object.keys(NUMBER_WORDS).sort((a, b) => b.length - a.length).join("|");
const NUM = String.raw`\d+(?:[.,]\d+)?`;
const AMOUNT = `(?:${NUM}|${WORD})`;
const LEFT = String.raw`(?<![\p{L}\p{N}_.,/])`;
const WB = String.raw`(?![\p{L}\p{N}_])`;
const NOT_LETTER = String.raw`(?![\p{L}])`;
const HOUR = `(?:heures?|h)${NOT_LETTER}`;
const MIN = `(?:minutes?|min|mn)${NOT_LETTER}`;
const SEC = `(?:secondes?|sec)${NOT_LETTER}`;
const UNIT = `(?:${HOUR}|${MIN}|${SEC})`;
const APOS = "['’]";
const SEP = String.raw`[\s  ]*`;

function value(raw) {
  raw = raw.toLowerCase();
  if (Object.hasOwn(NUMBER_WORDS, raw)) return NUMBER_WORDS[raw];
  return parseFloat(raw.replace(",", "."));
}

function unitSeconds(unit) {
  unit = unit.toLowerCase();
  if (unit.startsWith("h")) return 3600;
  if (unit.startsWith("s")) return 1;
  return 60;
}

const re = (source) => new RegExp(source, "giu");
const fixed = (seconds) => () => seconds;

// Tried in this order; a later match may not overlap an earlier one.
const PATTERNS = [
  // "1 h 30", "1h30", "1 heure 30 minutes"
  [re(String.raw`${LEFT}(?<h>${AMOUNT})${SEP}${HOUR}${SEP}(?<m>\d{1,2})(?!\d)(?:${SEP}${MIN})?`),
    (g) => Math.round(value(g.h) * 3600) + parseInt(g.m, 10) * 60],
  // "1 heure et demie"
  [re(String.raw`${LEFT}(?<n>${AMOUNT})${SEP}heures?\s+et\s+demie${WB}`), (g) => Math.round(value(g.n) * 3600) + 1800],
  // "10 à 15 minutes", "10-15 min", "1 à 2 heures"
  [re(String.raw`${LEFT}(?<a>${AMOUNT})${SEP}(?:à|a|-|–|—|ou)${SEP}(?<b>${AMOUNT})${SEP}(?<u>${UNIT})`),
    (g) => Math.round(Math.max(value(g.a), value(g.b)) * unitSeconds(g.u))],
  [re(String.raw`${LEFT}trois\s+quarts?\s+d${APOS}\s*heure${WB}`), fixed(2700)],
  [re(String.raw`${LEFT}(?:un\s+)?quart\s+d${APOS}\s*heure${WB}`), fixed(900)],
  [re(String.raw`${LEFT}(?:une?\s+)?demi[\s-]heure${WB}`), fixed(1800)],
  // "25 minutes", "2 heures", "30 secondes", "une heure", "1,5 heure"
  [re(String.raw`${LEFT}(?<n>${AMOUNT})${SEP}(?<u>${UNIT})`), (g) => Math.round(value(g.n) * unitSeconds(g.u))],
];

/** Durations found in `text`, in reading order: [{start, end, seconds}] (zero-length ones are ignored). */
export function findDurations(text) {
  const found = [];
  for (const [pattern, secondsOf] of PATTERNS) {
    for (const m of text.matchAll(pattern)) {
      const start = m.index, end = m.index + m[0].length;
      if (found.some((d) => start < d.end && d.start < end)) continue;
      const seconds = Math.trunc(secondsOf(m.groups));
      if (seconds > 0) found.push({ start, end, seconds });
    }
  }
  return found.sort((a, b) => a.start - b.start);
}

export const textIn = (duration, text) => text.slice(duration.start, duration.end);

const pad = (n) => String(n).padStart(2, "0");

/** 1499 -> "24:59"; an hour or more -> "1:05:00". */
export function formatClock(seconds) {
  seconds = Math.max(0, Math.trunc(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** Compact label for a button: "25 min", "1 h 30", "30 s". */
export function formatShort(seconds) {
  seconds = Math.trunc(seconds);
  if (seconds < 60) return `${seconds} s`;
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  if (h) return m ? `${h} h ${pad(m)}` : `${h} h`;
  return s ? `${m} min ${pad(s)}` : `${m} min`;
}

/** Whole seconds left before `end` (a Date.now() timestamp); never negative. Based on timestamps, so it stays right in a background tab. */
export function secondsLeft(end, now = Date.now()) {
  return Math.max(0, Math.ceil((end - now) / 1000));
}
