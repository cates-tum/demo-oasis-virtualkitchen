"""Guided-form web UI for the grilling bench (slice one: grill_red_meat).

Flow: GET / renders a form built from pseudo-oasis's schema; POST /run runs the
rule engine, pushes the entry to pseudo-oasis, and shows the outcome.
"""
import os

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import client
from engine import generator

SCHEMA_TYPE = "grill_red_meat"
NICK_COOKIE = "vk_nick"

# Local UI hints only: slider bounds and datalists. These are widget
# ergonomics, not field definitions. Field names / types / required / enum
# come from pseudo-oasis at request time. A field with an `enum` in the schema
# renders as a dropdown of those values with no hint needed here.
HINTS = {
    "internal_temp_celsius": {"widget": "slider", "min": 40, "max": 90, "step": 0.5, "default": 60},
    "duration_minutes": {"widget": "slider", "min": 1, "max": 120, "step": 1, "default": 20},
    "temperature_celsius": {"widget": "slider", "min": 100, "max": 300, "step": 5, "default": 220},
    "cooking_method": {"widget": "text", "datalist": ["grilling", "searing", "roasting"], "default": "grilling"},
    "cuisine": {"widget": "text", "datalist": ["Argentine", "American", "French", "Korean", "Turkish"], "default": ""},
}

app = FastAPI(title="virtual-kitchen")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _fields_for_form(values):
    """Resolve the schema and turn it into template-ready widget descriptors.
    `values` supplies current values (submitted or default)."""
    descriptors = []
    for field in client.get_schema_fields(SCHEMA_TYPE):
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


def _render_form(request, values, error=None, status=200):
    ctx = {
        "request": request,
        "schema_type": SCHEMA_TYPE,
        "nickname": request.cookies.get(NICK_COOKIE, ""),
        "oasis_url": client.OASIS_URL,
        "error": error,
    }
    try:
        ctx["fields"] = _fields_for_form(values)
    except client.OasisUnavailable as e:
        ctx["fields"] = []
        ctx["error"] = f"Cannot reach pseudo-oasis: {e}"
        status = 503
    return templates.TemplateResponse(request, "form.html", ctx, status_code=status)


@app.get("/", response_class=HTMLResponse)
def form(request: Request):
    return _render_form(request, values={})


@app.post("/run", response_class=HTMLResponse)
async def run(request: Request, nickname: str = Form("")):
    body = await request.form()
    values = {k: v for k, v in body.items()}
    nickname = (nickname or "").strip()

    try:
        fields = client.get_schema_fields(SCHEMA_TYPE)
        entry = generator.build_entry(SCHEMA_TYPE, fields, values, nickname)
        created = client.post_entry(**entry)
    except ValueError as e:
        return _render_form(request, values, error=str(e), status=400)
    except client.OasisUnavailable as e:
        return _render_form(request, values, error=str(e), status=502)

    resp = templates.TemplateResponse(request, "result.html", {
        "entry": created,
        "outcome": created["data"].get("outcome", {}),
        "inputs": entry["data"],
        "entry_url": client.entry_url(created["id"]),
        "safe_temp": generator.formulas.SAFE_TEMP_RED_MEAT,
    })
    if nickname:
        resp.set_cookie(NICK_COOKIE, nickname, max_age=60 * 60 * 24 * 30, samesite="lax")
    return resp
