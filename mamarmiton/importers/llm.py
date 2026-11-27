"""Recipe extraction with a small local LLM (Qwen 2.5 1.5B, GGUF, CPU only).

``llama-cpp-python`` and the model file are optional: when either is
missing, :func:`is_available` returns False and the importer falls back to
the rule-based parser.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import re
from pathlib import Path

from mamarmiton import config
from mamarmiton.models import COOKING_TYPES, DISH_TYPES

log = logging.getLogger(__name__)

MAX_INPUT_CHARS = 7000  # keeps prompt + answer inside the 4096-token context
N_CTX = 4096

SYSTEM_PROMPT = f"""Tu extrais UNE recette de cuisine d'un texte et tu réponds UNIQUEMENT par un objet JSON valide, sans aucun autre texte.

Schéma :
{{
  "nom": "<nom de la recette>",
  "temps": <minutes de préparation + repos, entier>,
  "temps_cuisson": <minutes de cuisson, entier>,
  "pour": <nombre de personnes, entier>,
  "type_plat": "<un parmi : {' / '.join(DISH_TYPES)}>",
  "type_cuisson": "<un parmi : {' / '.join(COOKING_TYPES)}>",
  "ingredients": [{{"nom": "<ingrédient>", "quantité": "<quantité avec unité>"}}],
  "etapes": ["<étape 1>", "<étape 2>"]
}}

Règles :
- Tout en français.
- Les quantités gardent leur unité (g, kg, ml, cl, l, cuillère à soupe, pincée, sachet...). Laisse "" si aucune quantité.
- Si un temps ou le nombre de personnes manque, estime-le prudemment.
- Recopie fidèlement chaque étape, dans l'ordre, sans en inventer.
- N'inclus pas les ustensiles dans les ingrédients.
- Chaque ingrédient n'apparaît qu'une fois. "nom" est l'aliment, "quantité" est le nombre + l'unité.

Exemple. Texte :
CRÊPES  Pour 4 personnes  Préparation 10 min  Cuisson 20 min
Ingrédients : 250 g de farine, 4 oeufs, 50 cl de lait, 1 pincée de sel
1 Mélange la farine et les oeufs. 2 Ajoute le lait petit à petit. 3 Fais cuire à la poêle.
Réponse :
{{"nom": "Crêpes", "temps": 10, "temps_cuisson": 20, "pour": 4, "type_plat": "Dessert", "type_cuisson": "Poêle", "ingredients": [{{"nom": "farine", "quantité": "250 g"}}, {{"nom": "oeufs", "quantité": "4"}}, {{"nom": "lait", "quantité": "50 cl"}}, {{"nom": "sel", "quantité": "1 pincée"}}], "etapes": ["Mélange la farine et les oeufs.", "Ajoute le lait petit à petit.", "Fais cuire à la poêle."]}}"""


def llama_cpp_installed() -> bool:
    try:
        import llama_cpp  # noqa: F401
    except Exception:  # ImportError, or a broken native library
        return False
    return True


def is_available() -> bool:
    return config.find_model() is not None and llama_cpp_installed()


def extract_json(raw: str) -> dict | None:
    """Pull the first JSON object out of a model answer (tolerates ``` fences)."""
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(raw[start:], start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return data if isinstance(data, dict) else None
    return None


def load_model(model_path: Path, n_threads: int | None = None):
    """Load the GGUF model (takes a few seconds and about 1 GB of memory)."""
    from llama_cpp import Llama

    return Llama(
        model_path=str(model_path),
        n_ctx=N_CTX,
        n_threads=n_threads or max(1, (os.cpu_count() or 2) - 1),
        n_gpu_layers=0,
        verbose=False,
    )


def ask_model(llm, text: str) -> dict | None:
    """The recipe JSON the loaded model reads in ``text`` (cut to ``MAX_INPUT_CHARS``), or None."""
    text = text[:MAX_INPUT_CHARS]
    kwargs = dict(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Texte de la recette :\n\"\"\"\n{text}\n\"\"\""},
        ],
        max_tokens=1200,
        temperature=0.1,
        repeat_penalty=1.15,
    )
    try:
        output = llm.create_chat_completion(response_format={"type": "json_object"}, **kwargs)
    except Exception:  # older llama-cpp builds without JSON grammar support
        log.info("JSON mode unavailable, retrying without it")
        output = llm.create_chat_completion(**kwargs)
    raw = output["choices"][0]["message"]["content"] or ""
    log.debug("LLM raw output: %s", raw)
    data = extract_json(raw)
    if data is None:
        log.warning("LLM answer was not valid JSON")
    return data


def parse_recipe_with_llm(text: str, model_path: Path | None = None) -> dict | None:
    model_path = model_path or config.find_model()
    if model_path is None:
        return None
    llm = load_model(model_path)
    try:
        return ask_model(llm, text)
    finally:  # the desktop frees the memory between imports
        del llm
        gc.collect()
