# virtual-kitchen

Synthetic "cooking experiment" generator for the `pseudo-oasis` demo. It fetches
schema definitions from `pseudo-oasis` at runtime, builds a guided form from
them, runs the inputs through a formula-based rule engine, and pushes the
result back as an entry.

`pseudo-oasis` must be running first. This app stores nothing itself.

## Scope so far

Two bench types wired end to end:

- **grilling / red meat** (`grill_red_meat`)
- **fermentation / wine** (`fermentation_wine`)

The landing page lists them; each is `GET /form/<schema_type>` ->
`POST /run/<schema_type>`. Beer, poultry, and fish formulas are not written yet.
The wired list is exactly the keys of `engine.generator.OUTCOME_FORMULAS`.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # edit if pseudo-oasis is not on localhost:8000
.venv/bin/uvicorn web.app:app --reload
```

`pseudo-oasis` already uses port 8000, so run this app on another port:

```bash
.venv/bin/uvicorn web.app:app --reload --port 8001
```

Then open http://localhost:8001.

## Layout

```
engine/
  formulas.py     grill + wine outcome math, noise/clamp helpers
  wine_model.py   UCI-fit wine quality coefficients + yeast-strain profiles
  generator.py    schema fields + form values -> POST /entries payload
web/
  app.py          FastAPI: landing, GET /form/<type>, POST /run/<type>
  templates/      base.html, index.html, form.html, result.html
client.py         GET /schemas and POST /entries against pseudo-oasis
```

Each of `engine/formulas.py`, `engine/wine_model.py`, `engine/generator.py`,
and `client.py` has an `if __name__ == "__main__"` self-check:

```bash
.venv/bin/python -m engine.formulas
.venv/bin/python -m engine.wine_model
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

## fermentation_wine outcome formulas

Inputs: `starting_gravity` (SG), `final_gravity` (FG), `fermentation_days` (D),
`yeast_strain`. Strain profiles (`EC-1118`, `71B`, `D47`, `RC-212`, `K1-V1116`,
else a default) live in `engine/wine_model.py`. Numeric outputs are
`formula + bounded noise`, then clamped.

| output | logic | noise |
|---|---|---|
| `abv` | `(SG - FG) * 131.25` (same as beer), clamped 0-20 | +/- 0.3 |
| `acidity` (g/L) | `6.0 + (FG - 0.996)*120 + D*0.01 + strain.va_bump*10`, clamped 3-12 | +/- 0.3 |
| `clarity` | index `D*1.2 - (FG - 0.99)*300`: <15 cloudy, <35 hazy, <60 clear, else brilliant | none |
| `aroma_score` | `strain.aroma_base + (12 - abs(D - 30)*0.4) - max(abv - 14, 0)*3`, clamped 0-100 | +/- 4 |
| `quality_score` | offline OLS regression, then `*10` to reach 0-100 | +/- 3 |

`quality_score` uses coefficients in `engine/wine_model.py`, fit once offline by
OLS on the UCI Wine Quality dataset (red, n=1599, R^2 0.34):

```
quality ~ alcohol + volatile_acidity + sulphates + residual_sugar + density
```

The five features are derived from the form inputs (ABV -> alcohol,
FG -> density, FG -> residual sugar, D + strain -> volatile acidity,
strain -> sulphates) and each clamped to the dataset's observed range so the
linear model is never extrapolated far. Nothing is fetched or trained at
runtime. To refit: download the dataset and run
`python3 engine/fit_wine_model.py winequality-red.csv`, then paste its output
into `wine_model.py`. That script is not imported by the app.

`VK_SEED=<int>` in `.env` makes the noise reproducible across runs, both benches.

## Known gaps / findings for the platform

- `pseudo-oasis` has no explorer UI yet, so the result page links to the raw
  entry JSON at `GET /entries/{id}`. Swap for a real explorer URL when one
  exists.
- `ingredients` (list type) is not collected in slice one.

Resolved:

- `heat_source` allowed values are now a machine-readable `enum` in
  `GET /schemas` (`field.enum = [grill, pan, oven]`). The form reads them
  straight from the fetched schema and renders a dropdown; no UI hint. An
  out-of-range value is rejected by `pseudo-oasis` with 422, which the form
  surfaces as an error the same way a missing required field does.

## Docker

Not set up yet. Runs under `uvicorn --reload` for now. A `Dockerfile` and a
compose service networked to `pseudo-oasis` come with a later slice.
