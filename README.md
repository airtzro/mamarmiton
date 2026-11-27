# MAMARMITON

A desktop recipe book (in French), built with Python and [customtkinter](https://github.com/TomSchimansky/CustomTkinter).
You can type recipes in by hand or import them from PDF files. A small AI model runs **locally** to read the PDFs. Nothing is sent online.

## Features

- **Recipe cards** in a responsive grid, with a photo or a colour band for the dish type.
- **Live search** by name or ingredient. Accents and capitals don't matter.
- **Filters**: dish type, cooking method, maximum total time, "contains these ingredients", favourites only. Several sort orders.
- **Servings calculator**: the −/+ buttons rescale every quantity (`1/2 sachet`, `2,5 kg`, `2-3 oeufs`, `une pincée`…) without changing the saved recipe.
- **Ingredient tick-boxes** to keep track while cooking.
- **Favourites** (★), **photos** per recipe, **notes**.
- **PDF import**: the text is read by a local LLM (Qwen 2.5 1.5B) and cross-checked by a rule-based parser. You always **review the result** before it is saved. Photos from the PDF come along too. Scanned PDFs work through OCR if Tesseract is installed.
- **Safe storage**: atomic writes, an automatic `.bak` copy, corrupt files are set aside instead of overwritten, plus JSON export and import for backups.
- **Light and dark mode**, and three **text sizes** (Aide et réglages).

## Account and sync (optional)

Without an account, Mamarmiton works locally exactly as before. An account lets you use the same recipe books on several computers, and on a phone through the web version.

- **Sign in**: click **Se connecter** in the sidebar. Log in with your name and password, or create an account with an invitation code from the admin. The default server is already set; to change it, open **Options avancées** in the login dialog.
- **First login**: your local recipes are uploaded to the account. Your local `recette.json` is left untouched, so it stays a backup.
- **Offline**: recipes are cached on the computer. Changes made without a connection wait in an outbox and are sent at the next sync. The sidebar shows the sync status.
- **Sharing**: use **Partager** on one of your recipes to share it with another user. Recipes shared with you are read-only and marked **Lecture seule**. The account dialog lists the books shared with you.
- **Conflicts**: if the same recipe was changed in two places, a dialog asks whether to keep your version, the other version, or both.
- **Web version**: the same server serves a web page at its root address (for example `https://<name>.duckdns.org/`). It works in a phone browser and can be added to the home screen. It has no PDF import and no offline use.

## Running it

Requires Python 3.11+ **with Tk**. Account sync uses [httpx](https://www.python-httpx.org/), installed with the package.

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -e .
# optional: AI reading of PDFs (prebuilt CPU wheels, no compiler needed)
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
python recette.py
```

### Linux / macOS

System Python often lacks Tk (`ImportError: libtk8.6.so`). A [uv](https://docs.astral.sh/uv/)-managed Python ships with it:

```bash
uv venv --managed-python -p 3.14 .venv
uv pip install -e ".[dev]"
uv pip install llama-cpp-python --only-binary llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu   # optional
.venv/bin/python recette.py        # or: .venv/bin/mamarmiton
```

### The AI model (optional)

Download `qwen2.5-1.5b-instruct-q4_0.gguf` (about 1 GB, from the Qwen/Qwen2.5-1.5B-Instruct-GGUF repository on Hugging Face) and put it in a `model/` folder in **one** of these places:

1. the data folder (see below): `<data>/model/`
2. next to `recette.exe` (or the project root when running from source)
3. the current working directory

If the model or `llama-cpp-python` is missing, imports still work with the rule-based parser. The Help window shows whether the AI is ready. On a typical laptop CPU an import takes about 15–60 s.

### OCR for scanned PDFs (optional)

`pip install -e ".[ocr]"`, then install the [Tesseract](https://github.com/tesseract-ocr/tesseract) program with French data and [Poppler](https://poppler.freedesktop.org/).

## Where the data lives

| OS | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\recette_app\` (same as v3, so existing recipes are picked up) |
| Linux | `~/.local/share/recette_app/` (`$XDG_DATA_HOME`) |
| any | `$MAMARMITON_DATA_DIR` if set |

Inside: `recette.json` (the recipes), `recette.json.bak` (previous save), `images/<recipe id>/`, `settings.json`, `mamarmiton.log`, and for accounts `account.json` (chmod 600) plus `remote/<username>/` (offline cache and outbox).

**Upgrading from v3** happens automatically on first launch:

- the old file is copied to `recette.v3-backup-<date>.json`;
- every recipe gets an `id`;
- `recette_fav.json` is merged into the recipes;
- photos from the old `img/<name>/` folders next to the exe are copied in.

The JSON stays readable with the same French keys (`nom`, `temps`, `temps_cuisson`, `pour`, `type_plat`, `type_cuisson`, `ingredients[].quantité`, `etapes`).

## Building the Windows .exe

```powershell
pip install -e ".[dev]"
pyinstaller recette.spec
```

The result is `dist/recette.exe`, a single file with no console window. Ship the `model/` folder next to it if you want AI import.

## Project layout

```
recette.py                 launcher (python recette.py / PyInstaller entry)
mamarmiton/
  config.py                paths (data dir, model, bundled resources), logging
  models.py                Recipe / Ingredient dataclasses, parsing of times & numbers
  quantities.py            reading and scaling quantities ("1 1/2 c. à s." × 2)
  storage.py               RecipeStore: JSON persistence, backups, v3 migration, images
  search.py                filters and sorting (pure functions)
  settings.py              user preferences
  importers/
    pdf_text.py            PDF text (+ OCR) and photo extraction
    heuristic.py           rule-based recipe parser
    llm.py                 local LLM parser (llama-cpp)
    pipeline.py            PDF → Recipe: AI + rules, cross-checked
  remote/
    client.py              typed HTTP client for the server (httpx)
    account.py             account.json: the signed-in account
    store.py               RemoteStore: same methods as RecipeStore, offline cache, outbox, conflicts
  ui/
    app.py                 main window, category sidebar, actions
    account.py             login, invitation code, account management dialog
    sharing.py             share a recipe, books shared with me
    conflicts.py           conflict dialog (keep mine / theirs / both)
    remote_logic.py        background sync glue between the UI and RemoteStore
    home.py                greeting, search + filter bar, responsive card grid
    detail.py              recipe page: cover, key facts, servings calculator, tickable steps, photos
    dialogs.py             recipe editor, import progress, help & settings
    widgets.py             recipe card, sidebar item, modal base
    art.py                 Pillow-drawn icons, generated cover art, rounded photos (light/dark)
    theme.py               design tokens (light/dark colours, fonts), styled widget factories
tests/                     pytest suite (no GUI needed)
```

The UI only talks to `RecipeStore` (or `RemoteStore`, which has the same methods), `search` and `importers`. Everything below `ui/` can be tested without a display.

## Tests

```bash
.venv/bin/python -m pytest
```

`tests/test_remote.py` tests the server client and needs the server's virtual environment, because it imports the server package:

```bash
cd server && .venv/bin/python -m pytest ../tests -q
```

## Keyboard shortcuts

`Ctrl+N` new recipe · `Ctrl+F` search · `Ctrl+S` save (in the editor) · `Échap` close a dialog.

See [ROADMAP.md](ROADMAP.md) for what's next.
