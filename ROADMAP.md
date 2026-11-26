# Roadmap

Ordered by value for the main user (a non-technical cook on Windows) against effort.
Effort: **S** is under a day, **M** is a few days, **L** is a week or more.

## Next: quick wins

| Feature | Why | Effort |
|---|---|---|
| **Print / export a recipe to PDF** | Cooks still like paper in the kitchen. Render the detail view to a one-page A4 PDF (reportlab, or Pillow → PDF). | S |
| **Kitchen mode** | Full-screen, very large text, one step at a time with big Précédent/Suivant buttons, and the screen kept awake. | S |
| **Timers in steps** | Detect "25 minutes" in a step and show a ▶ button that starts a countdown with a sound. | S |
| **Duplicate a recipe** | "Créer une variante" button: copy, then open the editor. | S |
| **Undo delete** | A toast with "Annuler" for 10 s instead of only a confirm dialog (the store already keeps `.bak`). | S |
| **Tags** | Free tags ("Noël", "rapide", "sans gluten") with chips in the sidebar filter. | S |
| **Pick the cover photo** | Choose which photo shows on the card; reorder photos. | S |
| **Drag & drop a PDF** onto the window to import it (tkinterdnd2). | Fewer clicks | S |

## Then: bigger features

| Feature | Why | Effort |
|---|---|---|
| **Import from a recipe website URL** | Most sites (Marmiton, 750g, Cuisine AZ…) embed `schema.org/Recipe` JSON-LD. Parsing it is reliable and needs no AI. | M |
| **Import from a photo** (phone picture of a handwritten or book recipe) | OCR, then the same AI/rules pipeline as PDFs. | M |
| **Shopping list** | Tick recipes plus servings, get a merged list grouped by aisle, then print it or copy it to the phone. Needs unit normalisation (g/kg, cl/l), which `quantities.py` already half does. | M |
| **Weekly meal planner** | A Monday-to-Sunday grid where you drop recipes in; feeds the shopping list. | M |
| **Rating & "cooked on" history** | ★1–5 plus the dates it was made. Sort by "pas faite depuis longtemps". | S–M |
| **Better AI** | Offer Qwen 2.5 3B for PCs with 8 GB+ RAM, keep the model loaded between imports, and add a "re-run the AI" button on the review screen. Possibly use a JSON-schema grammar instead of plain JSON mode. | M |
| **Auto-update check** | Read a version file on GitHub at start-up and offer the new `.exe`. | M |

## Later: platform

| Feature | Why | Effort |
|---|---|---|
| **SQLite storage** | Once there are more than ~1000 recipes, or if sync is added. Keep JSON export. | M |
| **Sync between PCs / phone** | Simplest is pointing the data folder at a OneDrive/Syncthing folder (needs a file lock and conflict handling). Fuller option: a small web companion. | L |
| **Share a recipe** | Export one recipe as `.mamarmiton` (JSON + photos zipped) that another copy can import. | S–M |
| **Installer** | An Inno Setup installer with Start-menu entry, uninstall, and the AI model downloaded on first use instead of shipped. | M |
| **Nutrition estimate** | Map ingredients to the CIQUAL table (ANSES, open data) to estimate kcal per serving. | L |
| **English UI** | Wrap strings with `gettext`; the code is already split so this is mechanical. | M |

## Tech debt to keep in mind

- No automated GUI tests yet (the logic is covered by `pytest`). A smoke test that builds `App` under Xvfb in CI would catch regressions.
- No CI. Add a GitHub Actions workflow that runs `pytest` and builds the `.exe` on `windows-latest` for each tag.
- The rule-based parser struggles with multi-column PDF layouts. pdfplumber's `extract_words` with x-positions could rebuild the columns.
