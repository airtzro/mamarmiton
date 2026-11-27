# Product plan

Mamarmiton is a **family recipe book**. It keeps the recipes a family actually cooks (grandma's handwritten ones, the PDFs, the website bookmarks), makes them easy to cook from, and passes them between generations and households.

This file replaces the old feature wishlist. Work is picked by **phase**, and every task must name the goal it serves. If a feature can't be tied to one of the goals below, it waits.

## Who it is for

| Segment | Today | What they need first |
|---|---|---|
| **The keeper**: a non-technical cook who owns the book (our first user, on Windows) | 1 person | Never lose a recipe, find one fast, nothing confusing |
| **The family**: children and siblings who want the recipes on their phone | invited by hand | Get in without help, see the shared book, add their own |
| **Other families** | none | Only once the first family uses it every week |

## Goals and how we measure them

| Goal | Metric (weekly, aggregated, no recipe content) | Target before moving on |
|---|---|---|
| **Activation** | Invited people who open a recipe within 7 days | ≥ 70 % |
| **Retention** | Weekly active cooks (opened or cooked a recipe) | the family is still active after 8 weeks |
| **Trust** | Data-loss incidents; restore drill passed | 0 incidents; drill every quarter |
| **Reliability** | Server uptime; sync errors per active user | ≥ 99 %; < 1 per week |
| **Cost** | Our hours of support per family per month | < 1 h |

North-star metric: **recipes cooked or opened per week across all families**.

## What already shipped, and what it has to prove

Everything below exists in code. Most of it has **never been used by a real person** (and some has never run on Windows or a phone), so it counts as unproven. Phase 0 adds usage counters. After 8 weeks of real use, any feature that nobody touches is hidden or removed, which keeps the app simple for the keeper.

| Area | Features | Status |
|---|---|---|
| Core | Recipes, search, categories, favourites, photos, PDF import (AI + rules), sync with offline cache and conflict copies, sharing | **core**: must be rock-solid |
| Cooking | Kitchen mode, step timers, servings scaling | unproven |
| Planning | Shopping list (desktop + web) | unproven |
| Capture | Import from a website URL (JSON-LD), "Créer une variante" | unproven |
| Organise | Tags | unproven |
| Output | Print / PDF | unproven |
| Safety | Undo delete, `.bak`, atomic writes, sync integrity tests | **core** |
| Delivery | Web app with offline mode, Windows release workflow, update check | infrastructure |

## Phase 0: ship what exists (now)

Nothing new gets built until the current version reaches the first family safely.

- **Must be in v4.1 itself**, because an exe installed in the family stays there for months:
  - **database schema migrations** (`PRAGMA user_version` plus ordered steps, tested from every past schema);
  - **a minimum client version** (the server answers `426`, and the app says "mettez à jour");
  - **privacy-respecting usage counters**: per feature, per day, aggregated, no recipe content, no third-party tracker; desktop counts travel with sync, and Aide gets a "what we measure" note. Without them, the trial below cannot tell us which features to keep;
  - **"Signaler un problème"** in the app: sends a message, the app version and recent log lines to the server.
- **Deploy** the server to the NAS (the NAS runs an older build, without the security, sync-integrity and web fixes). Cut **v4.1.0** with the release workflow and install it on the keeper's PC.
- **A real-device check of the core flows only**: login, add, edit, photo, offline, then sync on Windows, one Android phone and one iPhone. Every bug found goes first in line.
- **Public address without a port**: a permanent router rule for 443, or move the server (see Phase 2).
- **Backups you can trust**: a nightly `backup` cron (database and photos) to the USB disk plus one copy off-site, and a **restore drill** script that restores into a temporary instance and checks the counts.
- **Know when it's down**: an external uptime check on `/health` that alerts the developer.

Exit criterion: the keeper and 2 family members use it for 2 weeks with no data-loss incident and no help needed after setup.

## Phase 1: the first family (about 10 people)

Goal: **activation and retention** inside one family, with no support from the developer.

| Task | Why (goal) |
|---|---|
| **Invite links and QR codes** created from the app, not the server command line. The keeper invites her children herself. | Activation, support cost |
| **First-run onboarding**: on the desktop, offer "import my existing book" on first login; on the web, an empty state that explains the next step | Activation |
| **Password reset without SSH**: an admin or family-owner button that makes a one-time reset link | Support cost |

Exit criterion: the activation and retention targets above are met for one family for 8 weeks.

## Phase 2: more families (10 families, about 100 people)

Only once Phase 1 retention holds.

| Task | Why (goal) |
|---|---|
| **Families as a first-class concept**: a household with owner, members and a shared book, instead of only person-to-person shares. Quotas per family for photo storage. | Growth, cost control |
| **Move off the home NAS** to a small VPS or managed host, keeping the same Docker image. The NAS data disk is USB, the IPv4 is shared, and uptime depends on home power. | Reliability |
| **RGPD basics**: privacy policy, account deletion, a full data export (JSON + photos zip), data processing kept in the EU | Trust, legal (French users) |
| **Self-serve sign-up with a waitlist**, plus rate limits per family | Growth |
| **Staging instance** and a deploy script that runs migrations and rolls back on a failed health check | Reliability |
| **Error reporting** dashboard (server logs + client reports) and a weekly metrics summary | Reliability, decide what to build |

## Phase 3: sustainability (only if Phase 2 works)

Ideas to validate with the families first, not to build up front:

- **Printed family cookbook** (the PDF export already renders a whole book) through a print-on-demand partner. This is a natural paid product.
- **Freemium**: free for one family's text; paid for more photo storage and several households.
- **Public share link** for one recipe ("send grandma's crêpes to a friend"), read-only, with a "create your family's book" call to action. This is the growth loop.

## Not now

These stay off the table unless users ask for them: weekly meal planner, nutrition estimates, photo/OCR import, a bigger AI model, drag-and-drop import, English UI, ratings and a "cooked on" history, and picking the cover photo. They will be reconsidered with usage data and user requests.

## Engineering rules that come with this plan

- Every new feature ships with a usage counter and an owner goal from this file.
- Core flows (sync, storage, photos) get a failing test before any fix (see `tests/test_sync_integrity.py`).
- CI must stay green. A release is cut from a tag, never from a laptop.
- No release reaches the keeper before the real-device check of the core flows.

## Tech debt

- The rule-based PDF parser struggles with multi-column layouts (pdfplumber `extract_words` with x positions could rebuild the columns).
- Schema migrations (`PRAGMA user_version`) and the minimum desktop version (426) exist since v4.1; see server/README.md.
- The web client has unit tests for its logic but no browser tests; add Playwright once the core flows are stable.
