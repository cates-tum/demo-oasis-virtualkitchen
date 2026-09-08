# virtual-kitchen

Synthetic "cooking experiment" generator for the `pseudo-oasis` demo. It fetches
schema definitions from `pseudo-oasis` at runtime, builds a guided form from
them, runs the inputs through a formula-based rule engine, and pushes the
result back as an entry.

`pseudo-oasis` must be running first. This app stores nothing itself.

## Scope

All five bench types wired end to end:

- **grilling / red meat** (`grill_red_meat`)
- **grilling / poultry** (`grill_poultry`)
- **grilling / fish** (`grill_fish`)
- **fermentation / wine** (`fermentation_wine`)
- **fermentation / beer** (`fermentation_beer`)

The landing page lists them; each is `GET /form/<schema_type>` ->
`POST /run/<schema_type>`. The wired list is exactly the keys of
`engine.generator.OUTCOME_FORMULAS`.

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

### Behind a reverse proxy path prefix

If this app is served behind a proxy that strips a path prefix (e.g. Caddy's
`handle_path /kitchen/*` forwarding to this app at root), pass that prefix as
`--root-path` so generated links include it:

```bash
.venv/bin/uvicorn web.app:app --port 8001 --root-path "${ROOT_PATH:-}"
```

Set `ROOT_PATH=/kitchen` in the deploy environment; leave it unset for plain
local dev, where links generate with no prefix as before.

## Layout

```
engine/
  formulas.py     grilling (red meat / poultry / fish) + wine + beer outcome
                  math, per-meat tables, noise/clamp helpers, beer yeast table
  wine_model.py   UCI-fit wine quality coefficients + wine yeast profiles
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

## Form fields

The form is built from the fetched schema. `client.get_schema_fields` tags each
field with the schema that declared it, so the form renders two groups:
**General** (fields inherited from `base_recipe_attempt`: `cuisine`,
`cooking_method`) and **<Type> parameters** (fields the bench schema itself
declares). `cuisine` and `cooking_method` are `required: true` on the base
schema, so every entry type must send them, fermentation included;
`cooking_method` just defaults per bench (`grilling` / `fermenting`).

`generator.skip_fields(schema_type)` drops fields the wired forms do not
collect: `outcome` (computed), `ingredients` (list type), `temperature_celsius`
(no wired formula reads it), and `duration_minutes` on fermentation forms only
(grilling still uses it).

## grilling bench outcome formulas (red meat, poultry, fish)

The three grilling types share one outcome block, parametrised per meat.
Inputs: `internal_temp_celsius` (T, clamped 35-100), `heat_source`
(grill / pan / oven), `duration_minutes` (D, clamped 1-120), and `cut`
(the schema enum for that bench, optional). Every numeric output is
`formula + bounded noise`, then clamped to 0-100.

| output | logic | noise |
|---|---|---|
| `doneness` | temp bands per meat (see below) | none |
| `food_safety_score` | `100 / (1 + exp(-(T - safe_temp) / 2.5))`: collapses below the meat's safe temp, saturates above it | +/- 2 |
| `char_level` | `heat_factor * char_sensitivity * (1.1*D + 0.6*max(T-55, 0))`, heat_factor grill 1.0 / pan 0.7 / oven 0.35 | +/- 4 |
| `juiciness_score` | `100 - slope*max(T - knee, 0) - 0.35*D (+4 if oven)` | +/- 3 |
| `quality_score` | `0.30*safety + 0.35*juiciness + 0.20*char_fit + 0.15*doneness_pref`, then capped at `food_safety_score` when that is below 50 | +/- 3 |

Per-meat parameters (`engine/formulas.py`):

| meat | safe_temp | juiciness knee / slope | char sensitivity | doneness bands (upper temp -> label) |
|---|---|---|---|---|
| red meat | 63 C | 54 / 2.2 | 1.00 | 52 rare, 57 medium_rare, 63 medium, 69 medium_well, else well_done |
| poultry | 74 C | 68 / 3.0 | 1.05 | 70 underdone, 78 just_done, 85 cooked_through, 92 well_done, else dry |
| fish | 63 C | 52 / 3.5 | 1.15 | 48 rare, 54 medium_rare, 60 medium, 68 well_done, else overcooked |

Safe temps match the reference comments in `pseudo-oasis`'s grill schema YAMLs
(red meat / fish ~63 C, poultry ~74 C). A true medium-rare steak (57 C), a
50 C salmon, or a 63 C chicken all score low on `food_safety_score` on
purpose: that is the food-safety rule talking, and it is the number the demo
needs to get right. `doneness_pref` (a 0-100 "how much people like it there"
value) is co-located with each doneness band and feeds `quality_score`.

### cut

`cut` is a per-bench enum on the grill schemas, fetched at runtime like every
other field. `CUT_PROFILE` in `engine/formulas.py` gives each cut two levers:

- `ideal_temp` (deg C): where the cut eats best. Replaces the band
  `doneness_pref` with `100 - 2*abs(T - ideal_temp)`, so brisket rewards a
  90 C cook and tenderloin is punished for one.
- `moisture`: multiplier on the juiciness drying slope. Lean or thin cuts
  (breast 1.35, flank 1.30) dry faster; marbled or collagen cuts (ribeye
  0.80, brisket 0.70) hold.

`cut` never moves `food_safety_score`: the safe internal temp is per species,
not per cut. A blank or unknown cut uses a neutral default (no quality shift,
`moisture` 1.0), so a cut added to the Nexus enum later needs no code change.

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

## fermentation_beer outcome formulas

Inputs: `starting_gravity` (OG), `final_gravity` (FG), `fermentation_days` (D),
`yeast_strain`, `hop_grams`, `hop_alpha_acid_percent` (AA), `boil_time_minutes`
(t). Brewing yeast table (`US-05`, `S-04`, `WLP001`, `WB-06`, `T-58`, `W-34/70`,
else a default) lives in `engine/formulas.py`. No dataset for beer: quality is
rule-based, built on real homebrew relationships.

| output | logic | noise |
|---|---|---|
| `abv` | `(OG - FG) * 131.25`, clamped 0-15 | +/- 0.3 |
| `ibu` | Tinseth: `bigness * boil_factor * mgL`, clamped 0-120 (see below) | +/- 2 |
| `clarity` | index `D*1.1 - (FG - 0.995)*250 + strain.floc*25`: <15 cloudy, <32 hazy, <55 clear, else brilliant | none |
| `aroma_score` | `strain.aroma_base + min(hop_grams*0.15, 20) + (8 - abs(D - 18)*0.3) - max(ibu - 80, 0)*0.3` | +/- 4 |
| `quality_score` | `0.30*balance_fit + 0.20*atten_fit + 0.20*aroma + 0.15*clarity_score + 0.15*cond_fit` | +/- 3 |

IBU is the Tinseth (1997) formula on a fixed 20 L batch (`BATCH_VOLUME_LITERS`
in `formulas.py`; the schema carries no volume field):

```
mgL          = (AA/100) * hop_grams * 1000 / 20
bigness      = 1.65 * 0.000125 ** (OG - 1)
boil_factor  = (1 - e**(-0.04 * t)) / 4.15
IBU          = bigness * boil_factor * mgL
```

`quality_score` terms: `balance_fit` rewards a BU:GU ratio near 0.6
(`ibu / ((OG-1)*1000)`), `atten_fit` rewards apparent attenuation near 0.78
(`(OG-FG)/(OG-1)`), `cond_fit` rewards ferment length near 21 days,
`clarity_score` maps the label to cloudy 45 / hazy 65 / clear 85 / brilliant 95.
Extreme hop bills can drive raw Tinseth IBU past 120; the clamp there is a
deliberate ceiling, not a model of isomerization saturation.

`VK_SEED=<int>` in `.env` makes the noise reproducible across runs, all benches.

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
