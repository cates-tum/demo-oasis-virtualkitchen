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

    print("generator self-check ok:", entry["title"], entry["data"]["outcome"])
