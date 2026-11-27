// "Signaler un problème": keeps the last console errors in memory (never on disk) and builds the report.

const MAX_ERRORS = 50;
const MAX_LOG = 60 * 1024;
const errors = [];

const SECRET_WORDS = "(?:password|passwd|mot de passe|mdp|token|secret|api[_-]?key|old_password|new_password|invite|code d'invitation)";
const PATTERNS = [
  [/\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+/gi, "$1 [masqué]"],
  [/(authorization["']?\s*[:=]\s*)[^\r\n]+/gi, "$1[masqué]"],
  [new RegExp(`\\b(${SECRET_WORDS}["']?\\s*[:=]\\s*)("[^"\\r\\n]*"|'[^'\\r\\n]*'|[^\\s,;&}\\]]+)`, "gi"), "$1[masqué]"],
  [/\b[A-Z2-9]{4}(?:-[A-Z2-9]{4}){3}\b/g, "[masqué]"],
  [/(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])/g, "[masqué]"],
];

/** Hide anything that looks like a bearer token, a password or an invitation code. */
export function redact(text) {
  let out = String(text ?? "");
  for (const [pattern, replacement] of PATTERNS) out = out.replace(pattern, replacement);
  return out;
}

export function record(message, now = new Date()) {
  errors.push(`${now.toISOString()} ${redact(message)}`.slice(0, 2000));
  if (errors.length > MAX_ERRORS) errors.splice(0, errors.length - MAX_ERRORS);
}

export const recorded = () => errors.slice();

export function logTail() {
  let text = errors.join("\n");
  if (text.length > MAX_LOG) text = text.slice(-MAX_LOG);
  return redact(text);
}

/** Start listening (once). */
export function install(win = window) {
  if (win.__mamarmitonDiagnostics) return;
  win.__mamarmitonDiagnostics = true;
  win.addEventListener("error", (e) => record(`error: ${e.message} (${e.filename || ""}:${e.lineno || 0})`));
  win.addEventListener("unhandledrejection", (e) => record(`unhandled rejection: ${e.reason && e.reason.message ? e.reason.message : e.reason}`));
  const original = win.console.error.bind(win.console);
  win.console.error = (...args) => {
    try { record(`console.error: ${args.map((a) => (a instanceof Error ? a.message : typeof a === "string" ? a : JSON.stringify(a))).join(" ")}`); } catch { /* ignore */ }
    original(...args);
  };
}

export function buildReport(message, attachLog = true, win = window, { category = "bug", categoryOther = "" } = {}) {
  return {
    message: String(message).trim().slice(0, 4000),
    category,
    category_other: category === "other" ? String(categoryOther).trim().slice(0, 80) : "",
    client: "web",
    app_version: "web/1",
    os: String((win.navigator && win.navigator.userAgent) || "").slice(0, 300),
    log_tail: attachLog ? logTail() : "",
  };
}
