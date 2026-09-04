"""Orchestrates one experiment run: take the schema field list fetched from
pseudo-oasis plus the submitted form values, run the matching formula, and
assemble the POST /entries payload.

Generic on purpose: a field the schema declares but no formula knows about is
still copied through, so a new config-only bench in pseudo-oasis can be pushed
without a code change here (it just won't get a bespoke `outcome`).
"""
from engine import formulas

# Fields the form never collects: `outcome` is computed here; `ingredients`
# (list type) is out of scope for slice one.
SKIP_FIELDS = {"outcome", "ingredients"}

# schema_type -> function(data dict) -> outcome dict
OUTCOME_FORMULAS = {
    "grill_red_meat": lambda d: formulas.grill_red_meat_outcome(
        d["internal_temp_celsius"], d["heat_source"], d.get("duration_minutes", 20)
    ),
    "fermentation_wine": lambda d: formulas.fermentation_wine_outcome(
        d["starting_gravity"], d["final_gravity"], d["fermentation_days"], d["yeast_strain"]
    ),
}


def _coerce(value, field_type):
    if field_type == "number":
        return float(value)
    return str(value)


def build_entry(schema_type, schema_fields, form, nickname):
    """Return {schema_type, title, submitted_by, data} ready for client.post_entry.

    `schema_fields` is the list from client.get_schema_fields.
    `form` is a plain dict of submitted string values.
    Raises KeyError / ValueError if a required field is missing or unparseable.
    """
    data = {}
    for field in schema_fields:
        name = field["name"]
        if name in SKIP_FIELDS:
            continue
        raw = form.get(name, "")
        if raw == "" or raw is None:
            if field["required"]:
                raise ValueError(f"missing required field: {name}")
            continue
        data[name] = _coerce(raw, field["type"])

    formula = OUTCOME_FORMULAS.get(schema_type)
    if formula:
        data["outcome"] = formula(data)

    return {
        "schema_type": schema_type,
        "title": _title(schema_type, data, nickname),
        "submitted_by": nickname or None,
        "data": data,
    }


def _title(schema_type, data, nickname):
    who = f" ({nickname})" if nickname else ""
    cuisine = data.get("cuisine", "unspecified")
    if schema_type.startswith("grill_"):
        cut = schema_type.removeprefix("grill_").replace("_", " ")
        src = data.get("heat_source", "heat")
        return f"{cuisine} {cut} on {src}{who}"
    if schema_type.startswith("fermentation_"):
        kind = schema_type.removeprefix("fermentation_").replace("_", " ")
        days = int(data.get("fermentation_days", 0))
        return f"{cuisine} {kind}, {days}-day ferment{who}"
    return f"{cuisine} {schema_type}{who}"


if __name__ == "__main__":
    fields = [
        {"name": "cuisine", "type": "string", "required": True},
        {"name": "cooking_method", "type": "string", "required": True},
        {"name": "duration_minutes", "type": "number", "required": False},
        {"name": "outcome", "type": "object", "required": False},
        {"name": "internal_temp_celsius", "type": "number", "required": True},
        {"name": "heat_source", "type": "string", "required": True},
    ]
    form = {
        "cuisine": "Argentine",
        "cooking_method": "grilling",
        "internal_temp_celsius": "57",
        "heat_source": "grill",
        "duration_minutes": "18",
    }
    entry = build_entry("grill_red_meat", fields, form, "tester")
    assert entry["schema_type"] == "grill_red_meat"
    assert entry["data"]["internal_temp_celsius"] == 57.0
    assert set(entry["data"]["outcome"]) == {
        "doneness", "char_level", "juiciness_score", "food_safety_score", "quality_score"
    }
    assert entry["title"] == "Argentine red meat on grill (tester)", entry["title"]

    try:
        build_entry("grill_red_meat", fields, {"cuisine": "x"}, "t")
    except ValueError as e:
        assert "cooking_method" in str(e) or "internal_temp_celsius" in str(e), e
    else:
        raise AssertionError("expected ValueError for missing required field")

    wine_fields = [
        {"name": "cuisine", "type": "string", "required": True},
        {"name": "cooking_method", "type": "string", "required": True},
        {"name": "outcome", "type": "object", "required": False},
        {"name": "fermentation_days", "type": "number", "required": True},
        {"name": "yeast_strain", "type": "string", "required": True},
        {"name": "starting_gravity", "type": "number", "required": True},
        {"name": "final_gravity", "type": "number", "required": True},
    ]
    wine_form = {
        "cuisine": "French", "cooking_method": "fermenting",
        "fermentation_days": "28", "yeast_strain": "D47",
        "starting_gravity": "1.092", "final_gravity": "0.994",
    }
    wine = build_entry("fermentation_wine", wine_fields, wine_form, "vintner")
    assert wine["data"]["starting_gravity"] == 1.092
    assert set(wine["data"]["outcome"]) == {
        "abv", "acidity", "clarity", "aroma_score", "quality_score"
    }
    assert wine["title"] == "French wine, 28-day ferment (vintner)", wine["title"]

    print("generator self-check ok:", entry["title"], "|", wine["title"], wine["data"]["outcome"])
