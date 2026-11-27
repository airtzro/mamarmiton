// Port of mamarmiton/quantities.py (servings scaling). Same rules, same
// results: "1/2 sachet" x 2 -> "1 sachet", "200 g" x 1.5 -> "300 g".
// Python's \b only knows ASCII word edges in JS, so it is spelled out with
// Unicode-aware lookaheads (accented units such as "boîte" must still match).

const WB = String.raw`(?![\p{L}\p{N}_])`;
const UNICODE_FRACTIONS = { "½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8" };
const NUMBER_WORDS = {
  un: 1, une: 1, deux: 2, trois: 3, quatre: 4, cinq: 5, six: 6, sept: 7, huit: 8, neuf: 9, dix: 10, douze: 12,
};

// A number: "2", "2,5", "2.5", "1/2", "1 1/2"
const NUM = String.raw`\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?`;
const MIXED = String.raw`\d+\s+\d+\s*/\s*\d+`;
export const NUMBER_RE = new RegExp(String.raw`(?<num>${MIXED}|${NUM})(?:\s*(?:-|à)\s*(?<num2>${MIXED}|${NUM}))?`, "iu");
const METRIC_RE = new RegExp(String.raw`^\s*(?:kg|g|gr|grammes?|mg|l|litres?|dl|cl|ml)${WB}`, "iu");
const FIRST_WORD_RE = /^\s*([\p{L}\p{N}_]+)/u;
const AMOUNT_RE = new RegExp(
  String.raw`^\s*(?<amount>(?:${MIXED}|${NUM})(?:\s*(?:-|à)\s*(?:${MIXED}|${NUM}))?)\s*(?<unit>.*)$`, "u");

// ------------------------------------------------ exact fractions (small ints)
const gcd = (a, b) => (b === 0 ? Math.abs(a) : gcd(b, a % b));

export function frac(n, d = 1) {
  if (d < 0) { n = -n; d = -d; }
  const g = gcd(n, d) || 1;
  return { n: n / g, d: d / g };
}
const sub = (a, b) => frac(a.n * b.d - b.n * a.d, a.d * b.d);
const mul = (a, b) => frac(a.n * b.n, a.d * b.d);
const toFloat = (a) => a.n / a.d;
const cmp = (a, b) => a.n * b.d - b.n * a.d;
const abs = (a) => ({ n: Math.abs(a.n), d: a.d });

/** Closest fraction with a denominator <= max (same algorithm as Python's Fraction). */
function limitDenominator(x, max = 1000) {
  if (x.d <= max) return x;
  let [p0, q0, p1, q1] = [0, 1, 1, 0];
  let [n, d] = [x.n, x.d];
  for (;;) {
    const a = Math.floor(n / d);
    const q2 = q0 + a * q1;
    if (q2 > max) break;
    [p0, q0, p1, q1] = [p1, q1, p0 + a * p1, q2];
    [n, d] = [d, n - a * d];
  }
  const k = Math.floor((max - q0) / q1);
  const bound1 = frac(p0 + k * p1, q0 + k * q1);
  const bound2 = frac(p1, q1);
  return cmp(abs(sub(bound2, x)), abs(sub(bound1, x))) <= 0 ? bound2 : bound1;
}

function normalize(text) {
  for (const [char, ascii] of Object.entries(UNICODE_FRACTIONS)) {
    text = text.replace(new RegExp(String.raw`(\d)\s*${char}`, "g"), `$1 ${ascii}`);
    text = text.split(char).join(ascii);
  }
  return text;
}

export function parseNumber(token) {
  token = token.trim().replace(",", ".");
  let m = /^(\d+)\s+(\d+)\s*\/\s*(\d+)$/.exec(token);
  if (m) {
    if (Number(m[3]) === 0) return null;
    return frac(Number(m[1]) * Number(m[3]) + Number(m[2]), Number(m[3]));
  }
  m = /^(\d+)\s*\/\s*(\d+)$/.exec(token);
  if (m) return Number(m[2]) === 0 ? null : frac(Number(m[1]), Number(m[2]));
  m = /^(\d+)(?:\.(\d+))?$/.exec(token);
  if (m) {
    const decimals = m[2] || "";
    return frac(Number(m[1] + decimals), 10 ** decimals.length);
  }
  return null;
}

function pyRound(x) {  // Python rounds halves to the even number
  const whole = Math.floor(x.n / x.d);
  const rest = sub(x, frac(whole));
  const c = cmp(rest, frac(1, 2));
  if (c > 0) return whole + 1;
  if (c < 0) return whole;
  return whole % 2 === 0 ? whole : whole + 1;
}

/** Like Python's f"{x:.{digits}f}": exact ties (3.125) go to the even digit, JS toFixed rounds them up. */
function fixed(x, digits) {
  if ((x.d & (x.d - 1)) === 0) {  // power of two: the float is exact, so ties are real ties
    const scale = 10 ** digits;
    const r = pyRound(frac(x.n * scale, x.d));
    return (r / scale).toFixed(digits);
  }
  return toFloat(x).toFixed(digits);
}

/** Readable number: "1/2", "1 1/4", "2,5" (decimal=true avoids fractions). */
export function formatNumber(value, decimal = false) {
  value = limitDenominator(value, 1000);
  if (value.n <= 0) return "0";
  const whole = Math.floor(value.n / value.d);
  const rest = sub(value, frac(whole));
  if (cmp(value, frac(10)) >= 0) return String(pyRound(value));
  if (decimal) {
    return fixed(value, 2).replace(/0+$/, "").replace(/\.$/, "").replace(".", ",");
  }
  for (const f of [frac(1, 4), frac(1, 3), frac(1, 2), frac(2, 3), frac(3, 4)]) {
    if (cmp(abs(sub(rest, f)), frac(1, 40)) < 0) {
      const text = `${f.n}/${f.d}`;
      return whole === 0 ? text : `${whole} ${text}`;
    }
  }
  if (cmp(rest, frac(1, 20)) < 0) return String(whole);
  if (cmp(rest, frac(19, 20)) > 0) return String(whole + 1);
  return fixed(value, 1).replace(".", ",");
}

/** Scale the first number (or range) in `quantity` by `factor` (a {n, d} fraction). */
export function scaleQuantity(quantity, factor) {
  if (!quantity || (factor.n === factor.d)) return quantity;
  factor = limitDenominator(factor, 1000);
  const text = normalize(quantity);

  const match = NUMBER_RE.exec(text);
  if (match === null) {
    const word = FIRST_WORD_RE.exec(text);
    if (word && Object.hasOwn(NUMBER_WORDS, word[1].toLowerCase())) {
      const scaled = formatNumber(mul(frac(NUMBER_WORDS[word[1].toLowerCase()]), factor));
      const start = word[0].length - word[1].length;
      return text.slice(0, start) + scaled + text.slice(word[0].length);
    }
    return quantity;
  }
  const first = parseNumber(match.groups.num);
  if (first === null) return quantity;
  const decimal = METRIC_RE.test(text.slice(match.index + match[0].length)) || /[.,]/.test(match[0]);
  let replacement = formatNumber(mul(first, factor), decimal);
  if (match.groups.num2) {
    const second = parseNumber(match.groups.num2);
    if (second !== null) {
      const sep = match[0].includes("-") ? "-" : " à ";
      replacement += sep + formatNumber(mul(second, factor), decimal);
    }
  }
  return text.slice(0, match.index) + replacement + text.slice(match.index + match[0].length);
}

// Units offered in the editor (any other text is accepted too)
export const COMMON_UNITS = ["g", "kg", "ml", "cl", "l", "cuil. à soupe", "cuil. à café", "pincée", "sachet",
  "tranche", "gousse", "verre", "boîte", "botte"];

/** "500 g" -> ["500", "g"]; "une pincée" -> ["1", "pincée"]; "sel" -> ["", "sel"]. */
export function splitAmountUnit(quantity) {
  const text = normalize((quantity || "").trim());
  const match = AMOUNT_RE.exec(text);
  if (match) return [match.groups.amount.trim(), match.groups.unit.trim()];
  const word = /^\s*([\p{L}\p{N}_]+)\s*(.*)$/u.exec(text);
  if (word && Object.hasOwn(NUMBER_WORDS, word[1].toLowerCase())) {
    return [String(NUMBER_WORDS[word[1].toLowerCase()]), word[2].trim()];
  }
  return ["", text];
}

export function joinAmountUnit(amount, unit) {
  return [amount.trim(), unit.trim()].filter(Boolean).join(" ");
}
