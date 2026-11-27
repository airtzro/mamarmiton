// Every `import {a, b} from "./x.js"` in the web client must name something x.js really exports:
// a missing export is only a SyntaxError in the browser, never seen by the Python tests.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "../../mamarmiton_server/web");
const files = readdirSync(WEB).filter((f) => f.endsWith(".js") && f !== "sw.js");

// a block comment starts after a blank (so "image/*" in a string is not one)
const strip = (src) => src.replace(/(^|\s)\/\*[\s\S]*?\*\//g, "$1").replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1");

function exportsOf(src) {
  const names = new Set();
  let m;
  const decl = /^export\s+(?:async\s+)?(?:function\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)/gm;
  while ((m = decl.exec(src))) names.add(m[1]);
  // "export const A = 1, B = 2;" declares both
  const multi = /^export\s+(?:const|let|var)\s+([^;\n]*)/gm;
  while ((m = multi.exec(src))) for (const x of m[1].matchAll(/(?:^|,)\s*([A-Za-z_$][\w$]*)\s*=/g)) names.add(x[1]);
  const list = /^export\s*\{([^}]*)\}/gm;
  while ((m = list.exec(src))) {
    for (const part of m[1].split(",")) {
      const t = part.trim();
      if (t) names.add(t.split(/\s+as\s+/).pop().trim());
    }
  }
  if (/^export\s+default\b/m.test(src)) names.add("default");
  return names;
}

const sources = Object.fromEntries(files.map((f) => [f, strip(readFileSync(join(WEB, f), "utf8"))]));

test("every static import resolves to an existing module and exported name", () => {
  const problems = [];
  for (const [file, src] of Object.entries(sources)) {
    const re = /import\s+(?:([\w$]+)\s*,?\s*)?(?:\{([^}]*)\}|\*\s+as\s+([\w$]+))?\s*from\s*["']([^"']+)["']/g;
    let m;
    while ((m = re.exec(src))) {
      const [, defName, named, , spec] = m;
      if (!spec.startsWith(".")) { problems.push(`${file}: bare import ${spec}`); continue; }
      const target = spec.replace(/^\.\//, "");
      if (!sources[target]) { problems.push(`${file}: imports missing module ${spec}`); continue; }
      const have = exportsOf(sources[target]);
      if (defName && !have.has("default")) problems.push(`${file}: ${target} has no default export`);
      if (named) {
        for (const part of named.split(",")) {
          const name = part.trim().split(/\s+as\s+/)[0].trim();
          if (name && !have.has(name)) problems.push(`${file}: ${target} does not export "${name}"`);
        }
      }
    }
  }
  assert.deepEqual(problems, []);
});

test("every module parses and the service worker precaches only files that exist", () => {
  const sw = readFileSync(join(WEB, "sw.js"), "utf8");
  const shell = sw.slice(sw.indexOf("const SHELL"), sw.indexOf("];", sw.indexOf("const SHELL")));
  const urls = [...shell.matchAll(/"(\/[^"]*)"/g)].map((m) => m[1]);
  const missing = urls.filter((u) => u !== "/" && !existsSync(join(WEB, u.replace(/^\/app\//, ""))));
  assert.deepEqual(missing, []);
  const precached = new Set(urls);
  const unlisted = files.map((f) => `/app/${f}`).filter((u) => !precached.has(u));
  assert.deepEqual(unlisted, [], "web modules missing from the service worker shell");
});

test("every icon name used in the views exists in dom.js", () => {
  const known = new Set([...sources["dom.js"].matchAll(/^\s+([\w-]+): "/gm)].map((m) => m[1]));
  known.add("star-filled");
  const missing = [];
  for (const [file, src] of Object.entries(sources)) {
    for (const m of src.matchAll(/icon\(\s*(?:[\w.]+\s*\?\s*)?"([\w-]+)"(?:\s*:\s*"([\w-]+)")?/g)) {
      for (const name of [m[1], m[2]]) if (name && !known.has(name)) missing.push(`${file}: icon "${name}"`);
    }
  }
  assert.deepEqual(missing, []);
});

test("ids looked up in the page exist in index.html", () => {
  const html = readFileSync(join(WEB, "index.html"), "utf8");
  const missing = [];
  for (const [file, src] of Object.entries(sources)) {
    for (const m of src.matchAll(/getElementById\("([\w-]+)"\)/g)) {
      if (!html.includes(`id="${m[1]}"`)) missing.push(`${file}: #${m[1]}`);
    }
  }
  assert.deepEqual(missing, []);
});
