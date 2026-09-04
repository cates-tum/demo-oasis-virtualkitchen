# virtual-kitchen

Synthetic "cooking experiment" generator for the `pseudo-oasis` demo. It fetches
schema definitions from `pseudo-oasis` at runtime, builds a guided form from
them, runs the inputs through a formula-based rule engine, and pushes the
result back as an entry.

`pseudo-oasis` must be running first. This app stores nothing itself.

## Slice one scope

One bench type end to end: **grilling / red meat** (`grill_red_meat`).
The bench and type pickers are stubbed to that. Beer, wine, poultry, and fish
formulas are not written yet.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # edit if pseudo-oasis is not on localhost:8000
.venv/bin/uvicorn web.app:app --reload
```

Open http://localhost:8000 ... use a different port if `pseudo-oasis` already
has 8000:

```bash
.venv/bin/uvicorn web.app:app --reload --port 8001
```

Then open http://localhost:8001.

## Layout

```
engine/
  formulas.py     grill_red_meat outcome math + noise/clamp helpers
  generator.py    schema fields + form values -> POST /entries payload
web/
  app.py          FastAPI: GET / (form), POST /run (compute + push + result)
  templates/      base.html, form.html, result.html
client.py         GET /schemas and POST /entries against pseudo-oasis
```

Each of `engine/formulas.py`, `engine/generator.py`, and `client.py` has an
`if __name__ == "__main__"` self-check:

```bash
.venv/bin/python engine/formulas.py
.venv/bin/python -m engine.generator
.venv/bin/python client.py          # needs pseudo-oasis running
```

## grill_red_meat outcome formulas

Inputs: `internal_temp_celsius` (clamped 40-90), `heat_source`
(grill / pan / oven), `duration_minutes` (clamped 1-120). Every numeric output
is `formula + bounded noise`, then clamped to 0-100.

| output | logic | noise |
|---|---|---|
| `doneness` | temp bands: <52 rare, <57 medium_rare, <63 medium, <69 medium_well, else well_done | none |
| `food_safety_score` | `100 / (1 + exp(-(T - 63) / 2.5))`. Logistic curve on the USDA ~63 C red-meat minimum: collapses below it, saturates above it | +/- 2 |
| `char_level` | `heat_factor * (1.1*D + 0.6*max(T-55, 0))`, heat_factor grill 1.0 / pan 0.7 / oven 0.35 | +/- 4 |
| `juiciness_score` | `100 - 2.2*max(T-54, 0) - 0.35*D (+4 if oven)` | +/- 3 |
| `quality_score` | `0.30*safety + 0.35*juiciness + 0.20*char_fit + 0.15*doneness_pref`, then capped at `food_safety_score` when that is below 50 | +/- 3 |

A true medium-rare steak (57 C) scores low on `food_safety_score` on purpose:
that is the USDA guideline talking, and it is the one number the demo needs to
get right.

`VK_SEED=<int>` in `.env` makes the noise reproducible across runs.

## Known gaps / findings for the platform

- `pseudo-oasis` has no explorer UI yet, so the result page links to the raw
  entry JSON at `GET /entries/{id}`. Swap for a real explorer URL when one
  exists.
- `heat_source` allowed values (`grill`, `pan`, `oven`) live in a YAML comment
  in `pseudo-oasis/schemas/grill_red_meat.yaml`, which `GET /schemas` does not
  expose. They are hardcoded as a UI hint here. If allowed-value lists should
  drive the form, the platform schema needs a machine-readable `enum`.
- `ingredients` (list type) is not collected in slice one.

## Docker

Not set up yet. Slice one runs under `uvicorn --reload`. A `Dockerfile` and a
compose service networked to `pseudo-oasis` come with the next slice.
