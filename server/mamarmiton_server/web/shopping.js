// Port of mamarmiton/shopping.py: merge the ingredients of several recipes into one
// list grouped by aisle (units normalised, French formatting), plus the list state.
// Pure logic: the storage lives in courses.js.
import { NUMBER_RE, formatNumber, frac, parseNumber, splitAmountUnit } from "./quantities.js";
import { fold } from "./search.js";

export const AISLES = ["Fruits et légumes", "Crèmerie", "Boucherie / poisson", "Épicerie", "Boulangerie", "Surgelés",
  "Boissons", "Autres"];
export const OTHER = "Autres";

// ------------------------------------------------------------ fractions
const add = (a, b) => frac(a.n * b.d + b.n * a.d, a.d * b.d);
const mul = (a, b) => frac(a.n * b.n, a.d * b.d);
const cmp = (a, b) => a.n * b.d - b.n * a.d;
const num = (a) => a.n / a.d;
const ZERO = frac(0);

// ---------------------------------------------------------------- units
// [regex on accent-free lowercase text, dimension, factor to the base unit]
// Base units: grams, millilitres; spoons and the rest count as themselves.
const UNIT_PATTERNS = [
  [String.raw`^(?:c\.?\s*a\.?\s*c(?:afe)?|cuill?eres?\s*a\s*cafe|cuil\.?\s*a\s*cafe|cac|cc)(?![a-z])`, "cac", frac(1)],
  [String.raw`^(?:c\.?\s*a\.?\s*s(?:oupe)?|cuill?eres?\s*a\s*soupe|cuil\.?\s*a\s*soupe|cas|cs|cuill?eres?|cuil\.?)(?![a-z])`,
    "cas", frac(1)],
  [String.raw`^(?:kg|kilos?|kilogrammes?)(?![a-z])`, "mass", frac(1000)],
  [String.raw`^(?:g|gr|grammes?)(?![a-z])`, "mass", frac(1)],
  [String.raw`^mg(?![a-z])`, "mass", frac(1, 1000)],
  [String.raw`^(?:l|litres?)(?![a-z])`, "vol", frac(1000)],
  [String.raw`^dl(?![a-z])`, "vol", frac(100)],
  [String.raw`^cl(?![a-z])`, "vol", frac(10)],
  [String.raw`^ml(?![a-z])`, "vol", frac(1)],
].map(([p, dim, factor]) => [new RegExp(p), dim, factor]);
const SPOON_NAMES = { cas: "c. à soupe", cac: "c. à café" };
// Units kept as their own kind of count ("2 gousses d'ail" and "1 gousse d'ail" merge)
const COUNT_UNITS = new Set(["pincee", "sachet", "tranche", "morceau", "verre", "tasse", "bol", "boite", "gousse", "brin",
  "feuille", "botte", "noix", "noisette", "pot", "brique", "paquet", "tablette", "carre", "poignee", "filet", "zeste"]);
const UNIT_DISPLAY = { pincee: "pincée", boite: "boîte", poignee: "poignée", carre: "carré" };
const INVARIABLE = new Set(["noix"]);
const KEEP_S = new Set(["pois", "mais", "ananas", "anis", "bois", "brebis", "couscous", "tapas", "cassis", "jus", "radis",
  "paris", "pastis", "tournesol"]);

function singularWord(word) {
  if (word.length <= 3 || KEEP_S.has(word)) return word;
  if (word.endsWith("eaux")) return word.slice(0, -1);
  if (word.endsWith("s") && !word.endsWith("ss")) return word.slice(0, -1);
  return word;
}

export function foldName(text) {
  text = fold(text).replaceAll("œ", "oe").replaceAll("’", "'");
  text = text.replace(/\(.*?\)/g, " ");
  text = text.split(",")[0];
  text = text.trim().replace(/^(?:de la |de l'|du |des |de |d'|le |la |les |l')/, "");
  const words = text.match(/[a-z0-9]+/g) || [];
  return words.map(singularWord).join(" ");
}

/** Plural of an ingredient name when a count is 2 or more ("oeuf" -> "oeufs"). */
function pluralize(name) {
  const words = name.split(" ");
  if (words.length > 1 && !["de", "du", "au"].includes(fold(words[1])) && !fold(words[1]).startsWith("d'")) return name;
  const w = words[0];
  if (!w || "sxz".includes(w[w.length - 1]) || /\d/.test(w[w.length - 1])) return name;
  words[0] = w + "s";
  return words.join(" ");
}

const FULL_NUMBER = new RegExp(`^(?:${NUMBER_RE.source})$`, "iu");

/** "1,5 kg" -> {dim:"mass", value:1500/1}; "3" -> count 3; "sel" / "" -> null. Value is an {n, d} fraction. */
export function parseAmount(quantity) {
  const [number, rawUnit] = splitAmountUnit(quantity || "");
  if (!number) return null;
  const match = FULL_NUMBER.exec(number);
  if (match === null) return null;
  const value = parseNumber(match.groups.num2 || match.groups.num);
  if (value === null || value.n <= 0) return null;
  const unit = fold(rawUnit).replaceAll("’", "'");
  if (!unit) return { dim: "count", value, unit: "" };
  for (const [regex, dim, factor] of UNIT_PATTERNS) {
    if (regex.test(unit)) return { dim, value: mul(value, factor), unit: "" };
  }
  const first = singularWord(unit.split(/[\s.,;]+/)[0]);
  if (COUNT_UNITS.has(first)) return { dim: "unit", value, unit: first };
  return { dim: "unit", value, unit };
}

/** Like Python's f"{x:.{digits}f}": exact ties go to the even digit (JS toFixed rounds them up). */
function fixedHalfEven(x, digits) {
  const full = Math.abs(x).toFixed(30);  // exact decimal expansion of the double
  const [whole, decimals] = full.split(".");
  const keep = decimals.slice(0, digits), rest = decimals.slice(digits);
  const tie = /^50*$/.test(rest);
  if (!tie) return x.toFixed(digits);
  const lastDigit = Number((keep || whole).slice(-1));
  const down = lastDigit % 2 === 0;  // even: keep the truncated value
  const scale = 10 ** digits;
  const truncated = Math.floor(Math.abs(x) * scale) / scale;
  return ((x < 0 ? -1 : 1) * (down ? truncated : truncated + 1 / scale)).toFixed(digits);
}

/** French decimal: 1.5 -> "1,5", 200 -> "200". */
function fmt(value) {
  return fixedHalfEven(Number(value), 2).replace(/0+$/, "").replace(/\.$/, "").replace(".", ",");
}

function roundHalfEven(x) {
  const f = Math.floor(x), diff = x - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

const smallRound = (x) => (x >= 10 ? fmt(roundHalfEven(x)) : fmt(Number(fixedHalfEven(x, 1))));

/** Readable quantity in a sensible unit: 1500 g -> "1,5 kg", 1200 ml -> "1,2 l". */
export function formatAmount(amount) {
  const v = amount.value, x = num(v);
  if (amount.dim === "mass") {
    if (x >= 1000) return `${fmt(x / 1000)} kg`;
    if (x < 1) return `${roundHalfEven(x * 1000)} mg`;
    return `${smallRound(x)} g`;
  }
  if (amount.dim === "vol") {
    if (x >= 1000) return `${fmt(x / 1000)} l`;
    return `${smallRound(x)} ml`;
  }
  const number = formatNumber(v);
  if (amount.dim === "count") return number;
  if (Object.hasOwn(SPOON_NAMES, amount.dim)) return `${number} ${SPOON_NAMES[amount.dim]}`;
  let unit = UNIT_DISPLAY[amount.unit] || amount.unit;
  if (x >= 2 && !INVARIABLE.has(amount.unit) && unit && !"sx".includes(unit[unit.length - 1])) unit += "s";
  return `${number} ${unit}`;
}

// ---------------------------------------------------------------- aisles
const PHRASES = {  // checked first: more specific than single words
  "lait de coco": "Épicerie", "creme de coco": "Épicerie", "noix de coco": "Épicerie",
  "pate feuillete": "Crèmerie", "pate brise": "Crèmerie", "pate sable": "Crèmerie", "pate a tarte": "Crèmerie",
  "pomme de terre": "Fruits et légumes", "pois chiche": "Épicerie", "haricot rouge": "Épicerie",
  "haricot blanc": "Épicerie", "huile d'olive": "Épicerie", "sucre glace": "Épicerie",
  "beurre de cacahuete": "Épicerie", "poudre d'amande": "Épicerie", "creme de marron": "Épicerie",
  "chocolat noir": "Épicerie", "chocolat au lait": "Épicerie", "eau de fleur": "Épicerie",
  "petit pois": "Fruits et légumes", "haricot vert": "Fruits et légumes",
};
const KEYWORDS = [
  ["Surgelés", ["congele", "glace", "sorbet", "surgele"]],
  ["Boissons", ["armagnac", "biere", "boisson", "cafe", "champagne", "cidre", "cognac", "eau", "infusion", "jus", "kirsch", "limonade", "liqueur", "porto", "rhum", "sirop", "soda", "the", "vin", "whisky"]],
  ["Boulangerie", ["baguette", "biscotte", "brioche", "burger", "chapelure", "croissant", "naan", "pain", "pita", "tortilla", "wrap"]],
  ["Boucherie / poisson", ["agneau", "anchois", "andouillette", "bacon", "bar", "boeuf", "cabillaud", "calamar", "canard", "chorizo", "colin", "coquille", "cote", "crabe", "crevette", "dinde", "dorade", "escalope", "filet", "foie", "gigot", "hache", "huitre", "jambon", "langoustine", "lapin", "lardon", "lieu", "lotte", "magret", "maquereau", "merguez", "merlu", "moule", "pate", "poisson", "porc", "poulet", "poulpe", "rillette", "roti", "sardine", "saucisse", "saucisson", "saumon", "steak", "thon", "truite", "veau", "viande"]],
  ["Crèmerie", ["babybel", "beurre", "boursin", "brie", "camembert", "cancoillotte", "cheddar", "chevre", "comte", "creme", "emmental", "faisselle", "feta", "fondue", "fromage", "gorgonzola", "gruyere", "lait", "margarine", "maroilles", "mascarpone", "mozzarella", "munster", "oeuf", "parmesan", "raclette", "reblochon", "ricotta", "roquefort", "skyr", "yaourt", "yogourt"]],
  ["Épicerie", ["agar", "amande", "arome", "avoine", "bicarbonate", "biscuit", "boudoir", "bouillon", "boulgour", "cacahuete", "cacao", "cannelle", "capre", "cassonade", "cereale", "chocolat", "colorant", "concentre", "confiture", "conserve", "cornichon", "coulis", "couscous", "cumin", "curcuma", "curry", "datte", "edulcorant", "epice", "farine", "fecule", "feve", "figue", "flageolet", "flocon", "fusilli", "gelatine", "harissa", "huile", "ketchup", "lasagne", "laurier", "lentille", "levure", "macaroni", "mais", "maizena", "massepain", "mayonnaise", "miel", "moutarde", "muesli", "muscade", "noisette", "noix", "nouille", "nuoc", "nutella", "olive", "paprika", "passata", "pate", "penne", "pignon", "piment", "pistache", "pois", "poivre", "polenta", "praline", "pruneau", "puree", "quinoa", "raisin", "riz", "safran", "sauce", "sel", "semoule", "sesame", "soja", "spaghetti", "speculoos", "stevia", "sucre", "tabasco", "tagliatelle", "tahini", "tapioca", "vanille", "vergeoise", "vermicelle", "vinaigre", "wasabi", "abricot sec", "bouquet garni", "clou de girofle", "concentre de tomate", "coulis de tomate", "gingembre moulu", "herbe de provence", "pain d'epice", "piment d'espelette", "thym sec", "tomate pelee"]],
  ["Fruits et légumes", ["abricot", "ail", "ananas", "aneth", "artichaut", "asperge", "aubergine", "avocat", "banane", "basilic", "betterave", "blette", "brocoli", "butternut", "carotte", "cassis", "celeri", "cerfeuil", "cerise", "champignon", "chicoree", "chou", "ciboulette", "citron", "citronnelle", "citrouille", "clementine", "concombre", "coriandre", "courge", "courgette", "cresson", "echalion", "echalote", "endive", "epinard", "estragon", "fenouil", "figue", "fraise", "framboise", "fruit", "gingembre", "grenade", "groseille", "haricot", "herbe", "kiwi", "laitue", "legume", "lime", "litchi", "mache", "mandarine", "mangue", "melon", "menthe", "mure", "myrtille", "navet", "nectarine", "oignon", "orange", "origan", "oseille", "pamplemousse", "panais", "papaye", "pasteque", "patate", "peche", "persil", "piment", "poire", "poireau", "poivron", "pomme", "potiron", "prune", "radis", "raisin", "rhubarbe", "romarin", "roquette", "salade", "salsifis", "sauge", "scarole", "thym", "tomate", "topinambour", "cornichon frais", "mais frais"]],
];
// a few words appear in several lists: the first match in this order wins
const PRIORITY_WORDS = { pate: "Épicerie", gousse: "Fruits et légumes", piment: "Épicerie", raisin: "Fruits et légumes",
  figue: "Fruits et légumes", mais: "Épicerie", pois: "Épicerie", glace: "Surgelés",
  sucre: "Épicerie", noix: "Épicerie", olive: "Épicerie", cornichon: "Épicerie",
  thym: "Fruits et légumes", haricot: "Fruits et légumes", poivre: "Épicerie",
  poivron: "Fruits et légumes", abricot: "Fruits et légumes", gingembre: "Fruits et légumes",
  herbe: "Fruits et légumes", filet: "Boucherie / poisson", foie: "Boucherie / poisson" };

/** Aisle of an ingredient name, from a small built-in French keyword list. */
export function aisleOf(name) {
  const key = foldName(name);
  const padded = ` ${key} `;
  if (padded.includes(" surgele") || padded.includes(" congele")) return "Surgelés";
  for (const [phrase, aisle] of Object.entries(PHRASES)) {
    if (padded.includes(` ${phrase}`) || padded.includes(` ${foldName(phrase)}`)) return aisle;
  }
  const words = key.replaceAll("'", " ").split(/\s+/).filter(Boolean);
  for (const word of words) {
    if (Object.hasOwn(PRIORITY_WORDS, word)) return PRIORITY_WORDS[word];
  }
  for (const [aisle, keywords] of KEYWORDS) {
    for (const kw of keywords) {
      if (kw.includes(" ")) {
        if (padded.includes(` ${kw} `)) return aisle;
      } else if (words.includes(kw)) return aisle;
    }
  }
  return OTHER;
}

// --------------------------------------------------------------- merging
/** One line of plain text: "500 g de farine", "3 oeufs", "sel". */
export function itemLabel(item) {
  if (!item.quantity) return item.name;
  if (!/\p{L}/u.test(item.quantity)) return `${item.quantity} ${item.name}`;  // only digits: a plain count
  const folded = fold(item.name);
  const link = folded === "" || "aeiouhy".includes(folded[0]) ? "d'" : "de ";
  return `${item.quantity} ${link}${item.name}`;
}

function tidyName(name) {
  name = name.trim().replace(/\s+/g, " ");
  return name.replace(/^(?:de la |de l'|du |des |de |d')/i, "");
}

const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

/**
 * Merge the ingredients of [{recipe, servings}] into shopping lines
 * [{key, name, quantity, aisle, recipes}]. recipe: {nom, pour, ingredients: [{nom, quantite}]}.
 * Same ingredient (ignoring accents, case, plural) in the same kind of unit is added up;
 * an ingredient without any quantity ("sel") appears once, unless a quantified line exists.
 */
export function buildList(selection) {
  const merged = new Map();
  for (const { recipe, servings } of selection) {
    for (const ing of recipe.ingredients) {
      const keyName = foldName(ing.nom);
      if (!keyName) continue;
      const amount = parseAmount(ing.quantite);
      const dim = amount === null ? "none" : amount.dim !== "unit" ? amount.dim : `unit:${amount.unit}`;
      const key = `${keyName}|${dim}`;
      if (!merged.has(key)) merged.set(key, { name: ing.nom.trim(), amount, recipes: [], total: ZERO, keyName });
      const entry = merged.get(key);
      if (amount !== null) {
        const factor = frac(Math.max(1, servings), Math.max(1, recipe.pour));
        entry.total = add(entry.total, mul(amount.value, factor));
      }
      if (!entry.recipes.includes(recipe.nom)) entry.recipes.push(recipe.nom);
    }
  }
  const quantified = new Set();
  for (const e of merged.values()) if (e.amount !== null) quantified.add(e.keyName);
  const items = [];
  for (const [key, entry] of merged) {
    const amount = entry.amount;
    let name = tidyName(entry.name);
    let quantity = "";
    if (amount === null) {
      if (quantified.has(entry.keyName)) continue;  // "sel" next to "1 pincée de sel"
    } else {
      quantity = formatAmount({ dim: amount.dim, value: entry.total, unit: amount.unit });
      if (amount.dim === "count" && cmp(entry.total, frac(2)) >= 0) name = pluralize(name);
    }
    items.push({ key, name, quantity, aisle: aisleOf(entry.name), recipes: entry.recipes });
  }
  items.sort((a, b) => (AISLES.indexOf(a.aisle) - AISLES.indexOf(b.aisle)) || byText(fold(a.name), fold(b.name)) || byText(a.key, b.key));
  return items;
}

/** [[aisle, items]] in shop order, empty aisles left out. */
export function groupByAisle(items) {
  const groups = [];
  for (const aisle of AISLES) {
    const found = items.filter((i) => i.aisle === aisle);
    if (found.length) groups.push([aisle, found]);
  }
  return groups;
}

/** Plain text for a phone message. Ticked lines are left out. */
export function toText(items, checked = new Set(), extras = [], title = "Liste de courses") {
  const lines = [title, ""];
  for (const [aisle, group] of groupByAisle(items.filter((i) => !checked.has(i.key)))) {
    lines.push(aisle.toUpperCase());
    lines.push(...group.map((i) => `- ${itemLabel(i)}`));
    lines.push("");
  }
  const todo = extras.filter((e) => !e.done).map((e) => e.text);
  if (todo.length) {
    lines.push("AUTRES ARTICLES", ...todo.map((t) => `- ${t}`), "");
  }
  if (lines.length === 2) lines.push("(rien à acheter)");
  return `${lines.join("\n").trimEnd()}\n`;
}

// ------------------------------------------------------------------ state
const clampServings = (n) => Math.max(1, Math.min(99, Math.trunc(n)));

/** The current list: chosen recipes, ticked lines, hand-written lines. Plain data, JSON friendly. */
export function newState() {
  return { recipes: [], checked: new Set(), extras: [] };
}

export const servingsOf = (state, id) => (state.recipes.find((e) => e.id === id) || {}).servings ?? null;

export function addRecipe(state, id, servings) {
  servings = clampServings(servings);
  const entry = state.recipes.find((e) => e.id === id);
  if (entry) entry.servings = servings;
  else state.recipes.push({ id, servings });
}

export function removeRecipe(state, id) {
  state.recipes = state.recipes.filter((e) => e.id !== id);
}

export function setChecked(state, key, value) {
  if (value) state.checked.add(key); else state.checked.delete(key);
}

export function addExtra(state, text) {
  text = text.split(/\s+/).filter(Boolean).join(" ");
  if (!text) return false;
  state.extras.push({ text, done: false });
  return true;
}

export function clearState(state) {
  state.recipes = [];
  state.checked = new Set();
  state.extras = [];
}

/** Forget recipes that no longer exist and ticks of lines that are gone. */
export function prune(state, knownIds, validKeys) {
  state.recipes = state.recipes.filter((e) => knownIds.has(e.id));
  state.checked = new Set([...state.checked].filter((k) => validKeys.has(k)));
}

export function stateToDict(state) {
  return { version: 1, recipes: state.recipes, checked: [...state.checked].sort(), extras: state.extras };
}

export function stateFromDict(data) {
  const state = newState();
  if (data === null || typeof data !== "object" || Array.isArray(data)) return state;
  for (const entry of Array.isArray(data.recipes) ? data.recipes : []) {
    if (entry && typeof entry === "object" && entry.id) {
      let servings = parseInt(entry.servings ?? 4, 10);
      servings = Number.isNaN(servings) ? 4 : clampServings(servings);
      if (servingsOf(state, String(entry.id)) === null) state.recipes.push({ id: String(entry.id), servings });
    }
  }
  state.checked = new Set((Array.isArray(data.checked) ? data.checked : []).filter((k) => typeof k === "string"));
  for (const extra of Array.isArray(data.extras) ? data.extras : []) {
    if (extra && typeof extra === "object" && String(extra.text ?? "").trim()) {
      state.extras.push({ text: String(extra.text).trim(), done: Boolean(extra.done) });
    }
  }
  return state;
}
