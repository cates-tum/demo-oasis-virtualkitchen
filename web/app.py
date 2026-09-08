"""Guided-form web UI. Pick a wired bench type, fill a form built from
Nexus's schema for it, run the rule engine, push the entry, see the
outcome.

Wired types are exactly the keys of engine.generator.OUTCOME_FORMULAS.
"""
import os

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import client
from engine import formulas, generator

NICK_COOKIE = "vk_nick"

# One card per wired type on the landing page. Only types that also have a
# formula in generator.OUTCOME_FORMULAS are offered.
BENCHES = {
    "grill_red_meat": {
        "bench": "Grilling",
        "type_label": "Red meat",
        "blurb": "Steak or roast on a grill, pan, or in the oven. Safety pivots "
                 "on the ~63 C red-meat minimum.",
    },
    "grill_poultry": {
        "bench": "Grilling",
        "type_label": "Poultry",
        "blurb": "Chicken or turkey on a grill, pan, or oven. Safety pivots on "
                 "the higher ~74 C poultry minimum; breast dries fast past done.",
    },
    "grill_fish": {
        "bench": "Grilling",
        "type_label": "Fish",
        "blurb": "Fish fillet or steak on a grill, pan, or oven. Delicate: "
                 "juiciness falls fast past ~52 C, safe at ~63 C.",
    },
    "fermentation_wine": {
        "bench": "Fermentation",
        "type_label": "Wine",
        "blurb": "Must to wine over a set ferment. Quality comes from a "
                 "regression pre-fit on the UCI Wine Quality dataset.",
    },
    "fermentation_beer": {
        "bench": "Fermentation",
        "type_label": "Beer",
        "blurb": "Wort to beer over a set ferment. Real homebrew math: ABV "
                 "from the gravity drop, IBU from the Tinseth formula.",
    },
}

# Local UI hints only: slider bounds and datalists. Widget ergonomics, not
# field definitions. Field names / types / required / enum come from
# Nexus per request; an `enum` field renders as a dropdown with no hint.
HINTS = {
    # grilling (internal_temp_celsius default is per meat, see _field_hint)
    "duration_minutes": {"widget": "slider", "min": 1, "max": 120, "step": 1, "default": 20},
    "temperature_celsius": {"widget": "slider", "min": 100, "max": 300, "step": 5, "default": 220},
    # fermentation (shared by wine + beer; yeast_strain is a schema enum -> dropdown)
    "fermentation_days": {"widget": "slider", "min": 3, "max": 90, "step": 1, "default": 21},
    "starting_gravity": {"widget": "slider", "min": 1.030, "max": 1.120, "step": 0.001, "default": 1.055},
    "final_gravity": {"widget": "slider", "min": 0.985, "max": 1.030, "step": 0.001, "default": 1.010},
    # beer only
    "hop_grams": {"widget": "slider", "min": 0, "max": 200, "step": 5, "default": 40},
    "hop_alpha_acid_percent": {"widget": "slider", "min": 2, "max": 20, "step": 0.1, "default": 6.0},
    "boil_time_minutes": {"widget": "slider", "min": 0, "max": 120, "step": 5, "default": 60},
    # shared base fields (cooking_method default is per-bench, see _field_hint)
    "cuisine": {"widget": "text", "datalist": ["Argentine", "American", "French", "Korean", "Turkish"], "default": ""},
}

COOKING_METHODS = ["grilling", "searing", "roasting", "fermenting"]

app = FastAPI(title="e-kitchen")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
templates.env.globals["nexus_url"] = client.OASIS_PUBLIC_URL


def _require_wired(schema_type):
    if schema_type not in BENCHES or schema_type not in generator.OUTCOME_FORMULAS:
        raise HTTPException(404, f"no wired form for {schema_type!r}")


def _field_hint(name, schema_type):
    """UI hint for one field. Most come from HINTS; a couple depend on the
    bench type."""
    # yeast_strain carries a schema enum now, so the generic enum -> <select>
    # path handles it like cut. The formula strain profiles
    # (formulas.BEER_YEAST, wine_model.STRAIN_PROFILES) stay internal, keyed by
    # the same values, with a default for anything off-list.
    if name == "cooking_method":
        default = "fermenting" if schema_type.startswith("fermentation_") else "grilling"
        return {"widget": "text", "datalist": COOKING_METHODS, "default": default}
    if name == "internal_temp_celsius":
        # start each meat's slider near a sensible target for that meat
        default = {"grill_red_meat": 60, "grill_poultry": 74, "grill_fish": 58}.get(schema_type, 63)
        return {"widget": "slider", "min": 40, "max": 100, "step": 0.5, "default": default}
    return HINTS.get(name, {})


def _fields_for_form(schema_type, values):
    """Resolve the schema and turn it into template-ready widget descriptors.
    `values` supplies current values (submitted or default)."""
    descriptors = []
    for field in client.get_schema_fields(schema_type):
        name = field["name"]
        if name in generator.skip_fields(schema_type):
            continue
        hint = _field_hint(name, schema_type)
        enum = field.get("enum")
        if enum:
            widget = "select"
        else:
            widget = hint.get("widget") or ("number" if field["type"] == "number" else "text")
        current = values.get(name)
        if current in (None, ""):
            current = hint.get("default") or (enum[0] if enum else "")
        descriptors.append({
            "name": name,
            "label": name.replace("_", " ").capitalize(),
            "required": field["required"],
            "widget": widget,
            "min": hint.get("min"),
            "max": hint.get("max"),
            "step": hint.get("step"),
            "options": enum or hint.get("options"),
            "datalist": hint.get("datalist"),
            "value": current,
            "inherited": field.get("group") != schema_type,
        })
    return descriptors


def _render_form(request, schema_type, values, error=None, status=200):
    meta = BENCHES[schema_type]
    ctx = {
        "request": request,
        "schema_type": schema_type,
        "bench": meta["bench"],
        "type_label": meta["type_label"],
        "nickname": request.cookies.get(NICK_COOKIE, ""),
        "error": error,
    }
    try:
        ctx["fields"] = _fields_for_form(schema_type, values)
    except client.OasisUnavailable as e:
        ctx["fields"] = []
        ctx["error"] = f"Cannot reach Nexus: {e}"
        status = 503
    return templates.TemplateResponse(request, "form.html", ctx, status_code=status)


_GRILL_MEAT = {"grill_red_meat": ("red meat", "red_meat"),
               "grill_poultry": ("poultry", "poultry"),
               "grill_fish": ("fish", "fish")}


def _result_note(schema_type, outcome, inputs):
    if schema_type in _GRILL_MEAT:
        noun, kind = _GRILL_MEAT[schema_type]
        t = inputs.get("internal_temp_celsius")
        safe = formulas.SAFE_TEMP[kind]
        below = outcome.get("food_safety_score", 100) < 50
        return (f"food_safety_score is a logistic curve on the ~{safe:g} C safe "
                f"internal temp for {noun}. {t} C "
                + ("sits below that reference, so the score is low by design."
                   if below else "clears that reference."))
    if schema_type == "fermentation_wine":
        return ("quality_score comes from an OLS regression pre-fit offline on "
                "the UCI Wine Quality dataset (alcohol, volatile acidity, "
                "sulphates, residual sugar, density), scaled to 0-100.")
    if schema_type == "fermentation_beer":
        return ("abv is the gravity drop times 131.25; ibu is the Tinseth "
                f"formula over hop_grams, hop_alpha_acid_percent, "
                f"boil_time_minutes and a fixed {formulas.BATCH_VOLUME_LITERS:g} L "
                "batch. clarity, aroma, and quality are rule-based.")
    return None


@app.get("/", response_class=HTMLResponse, name="index")
def index(request: Request):
    cards = [{"schema_type": st, **BENCHES[st]} for st in BENCHES
             if st in generator.OUTCOME_FORMULAS]
    return templates.TemplateResponse(request, "index.html", {"cards": cards})


@app.get("/form/{schema_type}", response_class=HTMLResponse, name="form")
def form(request: Request, schema_type: str):
    _require_wired(schema_type)
    return _render_form(request, schema_type, values={})


@app.post("/run/{schema_type}", response_class=HTMLResponse, name="run")
async def run(request: Request, schema_type: str, nickname: str = Form("")):
    _require_wired(schema_type)
    body = await request.form()
    values = {k: v for k, v in body.items()}
    nickname = (nickname or "").strip()

    try:
        fields = client.get_schema_fields(schema_type)
        entry = generator.build_entry(schema_type, fields, values, nickname)
        created = client.post_entry(**entry)
    except ValueError as e:
        return _render_form(request, schema_type, values, error=str(e), status=400)
    except client.OasisUnavailable as e:
        return _render_form(request, schema_type, values, error=str(e), status=502)

    outcome = created["data"].get("outcome", {})
    resp = templates.TemplateResponse(request, "result.html", {
        "entry": created,
        "outcome": outcome,
        "inputs": entry["data"],
        "entry_url": client.entry_url(created["id"]),
        "note": _result_note(schema_type, outcome, entry["data"]),
    })
    if nickname:
        resp.set_cookie(NICK_COOKIE, nickname, max_age=60 * 60 * 24 * 30, samesite="lax")
    return resp
