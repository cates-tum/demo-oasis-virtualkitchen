"""Guided-form web UI. Pick a wired bench type, fill a form built from
pseudo-oasis's schema for it, run the rule engine, push the entry, see the
outcome.

Wired types are exactly the keys of engine.generator.OUTCOME_FORMULAS.
"""
import os

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import client
from engine import generator, wine_model

NICK_COOKIE = "vk_nick"

# One card per wired type on the landing page. Only types that also have a
# formula in generator.OUTCOME_FORMULAS are offered.
BENCHES = {
    "grill_red_meat": {
        "bench": "Grilling",
        "type_label": "Red meat",
        "blurb": "Steak or roast on a grill, pan, or in the oven. Outcome from "
                 "food-safety temp curves and time/heat browning.",
    },
    "fermentation_wine": {
        "bench": "Fermentation",
        "type_label": "Wine",
        "blurb": "Must to wine over a set ferment. Quality comes from a "
                 "regression pre-fit on the UCI Wine Quality dataset.",
    },
}

# Local UI hints only: slider bounds and datalists. Widget ergonomics, not
# field definitions. Field names / types / required / enum come from
# pseudo-oasis per request; an `enum` field renders as a dropdown with no hint.
HINTS = {
    # grilling
    "internal_temp_celsius": {"widget": "slider", "min": 40, "max": 90, "step": 0.5, "default": 60},
    "duration_minutes": {"widget": "slider", "min": 1, "max": 120, "step": 1, "default": 20},
    "temperature_celsius": {"widget": "slider", "min": 100, "max": 300, "step": 5, "default": 220},
    # fermentation
    "fermentation_days": {"widget": "slider", "min": 3, "max": 90, "step": 1, "default": 21},
    "starting_gravity": {"widget": "slider", "min": 1.050, "max": 1.130, "step": 0.001, "default": 1.090},
    "final_gravity": {"widget": "slider", "min": 0.985, "max": 1.030, "step": 0.001, "default": 0.995},
    "yeast_strain": {"widget": "text", "datalist": list(wine_model.STRAIN_PROFILES), "default": "EC-1118"},
    # shared base fields
    "cooking_method": {"widget": "text", "datalist": ["grilling", "searing", "roasting", "fermenting"], "default": "grilling"},
    "cuisine": {"widget": "text", "datalist": ["Argentine", "American", "French", "Korean", "Turkish"], "default": ""},
}

app = FastAPI(title="virtual-kitchen")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _require_wired(schema_type):
    if schema_type not in BENCHES or schema_type not in generator.OUTCOME_FORMULAS:
        raise HTTPException(404, f"no wired form for {schema_type!r}")


def _fields_for_form(schema_type, values):
    """Resolve the schema and turn it into template-ready widget descriptors.
    `values` supplies current values (submitted or default)."""
    descriptors = []
    for field in client.get_schema_fields(schema_type):
        name = field["name"]
        if name in generator.SKIP_FIELDS:
            continue
        hint = HINTS.get(name, {})
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
        ctx["error"] = f"Cannot reach pseudo-oasis: {e}"
        status = 503
    return templates.TemplateResponse(request, "form.html", ctx, status_code=status)


def _result_note(schema_type, outcome, inputs):
    if schema_type == "grill_red_meat":
        t = inputs.get("internal_temp_celsius")
        safe = generator.formulas.SAFE_TEMP_RED_MEAT
        below = outcome.get("food_safety_score", 100) < 50
        return (f"food_safety_score is a logistic curve on the ~{safe} C USDA "
                f"minimum safe internal temp for red meat. {t} C "
                + ("sits below that reference, so the score is low by design."
                   if below else "clears that reference."))
    if schema_type == "fermentation_wine":
        return ("quality_score comes from an OLS regression pre-fit offline on "
                "the UCI Wine Quality dataset (alcohol, volatile acidity, "
                "sulphates, residual sugar, density), scaled to 0-100.")
    return None


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    cards = [{"schema_type": st, **BENCHES[st]} for st in BENCHES
             if st in generator.OUTCOME_FORMULAS]
    return templates.TemplateResponse(request, "index.html", {"cards": cards})


@app.get("/form/{schema_type}", response_class=HTMLResponse)
def form(request: Request, schema_type: str):
    _require_wired(schema_type)
    return _render_form(request, schema_type, values={})


@app.post("/run/{schema_type}", response_class=HTMLResponse)
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
