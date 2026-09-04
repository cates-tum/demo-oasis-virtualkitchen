# Project: virtual-kitchen

## What this is

A synthetic "cooking experiment" generator app, built as a companion to the `pseudo-oasis` repo (the platform). Together they form a demo mirroring NOMAD Oasis's design patterns via a cooking-domain analogy; built for a conference keynote (October). This repo plays the role of "an instrument" in the NOMAD analogy: it produces data and pushes it to the platform, it does not store or serve data itself.

`pseudo-oasis` must already be running (locally via docker-compose, or on the EOSC VM later) for this app to function, since it fetches schema definitions from it at runtime and pushes generated entries to it via API.

## Core integration principle: fetch schemas at runtime, never duplicate them

This app must call `pseudo-oasis`'s `GET /schemas` endpoint to learn what fields exist for a given bench/type, rather than keeping its own hardcoded copy of field lists. This is the same config-driven philosophy as `pseudo-oasis`, applied to the client side:

- The **generator UI form** should be built dynamically from the fetched schema (which fields exist, whether required), not hardcoded per cuisine type.
- The **rule engine / formulas** (below) may have their own internal parameters (e.g. yeast strain effect on ABV) that aren't part of the schema itself; that's fine, those are computation logic, not field definitions. But the actual field *names and structure* pushed to `pseudo-oasis` must match what `/schemas` declares for that `schema_type`.
- If a new bench/type is added later purely as a config file in `pseudo-oasis`, this app should be able to at least accept and push generic entries for it without a code change, even if a bespoke formula for it doesn't exist yet.

## The two benches and their formulas

### Fermentation bench (wine, beer)

**Beer**: use real, deterministic homebrewing formulas, not invented ones:
- ABV ≈ `(starting_gravity - final_gravity) * 131.25`
- IBU via the Tinseth formula, using `hop_grams`, `hop_alpha_acid_percent`, `boil_time_minutes`, and batch volume (assume a fixed reasonable batch size, e.g. 20L, unless the schema has a volume field)
- Clarity/aroma/quality: light rule-based scoring off fermentation_days, yeast_strain (a small lookup table of a few strain "profiles" is fine), and the above; plus bounded random noise so results aren't perfectly deterministic

**Wine**: quality_score uses a small regression pre-fit offline on the public UCI Wine Quality dataset (physicochemical properties → quality). Coefficients get baked into a config/constants file here, not fetched live and not a runtime-trained model. ABV/acidity/clarity/aroma can use simpler rule-based formulas similar to beer's approach, informed by starting/final gravity and fermentation_days.

### Grilling bench (red meat, poultry, fish)

No dataset; use real domain food-safety reference points as the basis for `food_safety_score` and `doneness`:
- Red meat: ~63°C minimum safe internal temp
- Poultry: ~74°C minimum safe internal temp
- Fish: ~63°C minimum safe internal temp

(These match the notes already present in `pseudo-oasis`'s grill schema YAMLs; keep them consistent.)

`char_level` and `juiciness_score` derive from `internal_temp_celsius`, `heat_source`, and `duration_minutes`, with bounded random noise. `food_safety_score` should meaningfully drop below the safe threshold and be high above it; this is the one place where getting the "real domain rule" right actually matters for demo credibility.

## General rule-engine design

All formulas should follow the same shape: **bounded input ranges → a formula (real or lookup-table-based) → small random noise → clamped output ranges.** Avoid pure randomness with no formula behind it; the whole point is that results feel plausible and internally consistent, not that they're technically impressive statistics.

## Tech stack

- Python, likely FastAPI or a simpler framework for the guided-form web UI (your call at implementation time; keep it lightweight; this app doesn't need its own database, it's stateless aside from in-memory session/form state)
- `client.py` (or equivalent) handles fetching `/schemas` from `pseudo-oasis` and posting to `/entries`
- Docker Compose, meant to run alongside `pseudo-oasis`'s compose stack (or as a separate compose file networked to it; decide at implementation time based on what's simplest to deploy together on the EOSC VM later)

## Repo layout

```
virtual-kitchen/
├── engine/
│   ├── formulas.py        beer ABV/IBU, grilling temp-safety curves
│   ├── wine_model.py      pre-fit UCI wine dataset coefficients
│   └── generator.py       orchestrates: fetch schema -> collect inputs -> run formula -> build entry payload
├── web/                   guided form UI: bench picker -> type -> params -> "run experiment"
├── client.py              GET /schemas and POST /entries against pseudo-oasis's API
├── docker-compose.yml
└── README.md
```

## UI / UX principles

- Guided flow: pick bench → pick type (wine/beer, or red meat/poultry/fish) → set a few key parameters via sliders/dropdowns (favor these over free text, consistent with `pseudo-oasis`'s guardrails) → "run experiment" button.
- After submission: show the visitor their generated entry's outcome, with a link/button to view it in `pseudo-oasis`'s explorer.
- Responsive layout; visitors may open the shared URL on a phone even though it's not the primary demo mode; don't build mobile-first, but don't break on a narrow screen either.
- Lightweight nickname-only "registration" (cookie or simple session), consistent with `pseudo-oasis`; no real auth needed.

## Non-goals

- No user accounts/auth beyond a nickname.
- No live ML training or model fetching; the wine model is pre-fit and static.
- No admin UI for adding new formula types; that's a code change here (unlike schema types in `pseudo-oasis`, which are config-only). This asymmetry is fine and expected: schemas are data structure (config-driven), formulas are domain logic (code).

## When in doubt

Get one full bench type (e.g. grilling/red-meat, since its formula is simplest and most demo-compelling) working end-to-end; form → formula → push → visible in pseudo-oasis's explorer, before broadening to all five types. A single working vertical slice beats five half-built ones.
