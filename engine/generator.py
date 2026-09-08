"""Orchestrates one experiment run: take the schema field list fetched from
Nexus plus the submitted form values, run the matching formula, and
assemble the POST /entries payload.

Generic on purpose: a field the schema declares but no formula knows about is
still copied through, so a new config-only bench in Nexus can be pushed
without a code change here (it just won't get a bespoke `outcome`).
"""
from engine import formulas

# Fields the wired forms never collect. `outcome` is computed here;
# `temperature_celsius` is a base_recipe_attempt optional no wired formula
# reads. `ingredients` IS collected now (fixed slots, see _ingredients_from_form).
SKIP_FIELDS = {"outcome", "temperature_celsius"}

# How many ingredient rows the e-kitchen form offers.
INGREDIENT_SLOTS = 4

# `duration_minutes` is real input for grilling (it drives char and juiciness)
# but meaningless on a fermentation form, where ferment length is
# `fermentation_days`. Skip it only there.
SKIP_FIELDS_BY_TYPE = {
    "fermentation_wine": {"duration_minutes"},
    "fermentation_beer": {"duration_minutes"},
}


def skip_fields(schema_type):
    return SKIP_FIELDS | SKIP_FIELDS_BY_TYPE.get(schema_type, set())

# schema_type -> function(data dict) -> outcome dict
OUTCOME_FORMULAS = {
    "grill_red_meat": lambda d: formulas.grill_red_meat_outcome(
        d["internal_temp_celsius"], d["heat_source"], d.get("duration_minutes", 20),
        d.get("cut"), d.get("ingredients")
    ),
    "grill_poultry": lambda d: formulas.grill_poultry_outcome(
        d["internal_temp_celsius"], d["heat_source"], d.get("duration_minutes", 20),
        d.get("cut"), d.get("ingredients")
    ),
    "grill_fish": lambda d: formulas.grill_fish_outcome(
        d["internal_temp_celsius"], d["heat_source"], d.get("duration_minutes", 20),
        d.get("cut"), d.get("ingredients")
    ),
    "fermentation_wine": lambda d: formulas.fermentation_wine_outcome(
        d["starting_gravity"], d["final_gravity"], d["fermentation_days"], d["yeast_strain"],
        d.get("ingredients")
    ),
    "fermentation_beer": lambda d: formulas.fermentation_beer_outcome(
        d["starting_gravity"], d["final_gravity"], d["fermentation_days"], d["yeast_strain"],
        d["hop_grams"], d["hop_alpha_acid_percent"], d["boil_time_minutes"], d.get("ingredients")
    ),
}


def _coerce(value, field_type):
    if field_type == "number":
        return float(value)
    return str(value)


def _ingredients_from_form(form):
    """Gather the fixed ingredient slots (ingredient_name_i / _qty_i / _unit_i)
    into the schema list-of-{name,quantity,unit} shape. Rows with a blank name
    are dropped; quantity/unit are optional per row."""
    rows = []
    for i in range(INGREDIENT_SLOTS):
        name = (form.get(f"ingredient_name_{i}") or "").strip()
        if not name:
            continue
        row = {"name": name}
        qty = (form.get(f"ingredient_qty_{i}") or "").strip()
        if qty:
            try:
                row["quantity"] = float(qty)
            except ValueError:
                pass
        unit = (form.get(f"ingredient_unit_{i}") or "").strip()
        if unit:
            row["unit"] = unit
        rows.append(row)
    return rows


def build_entry(schema_type, schema_fields, form, nickname):
    """Return {schema_type, title, submitted_by, data} ready for client.post_entry.

    `schema_fields` is the list from client.get_schema_fields.
    `form` is a plain dict of submitted string values.
    Raises KeyError / ValueError if a required field is missing or unparseable.
    """
    skip = skip_fields(schema_type)
    data = {}
    for field in schema_fields:
        name = field["name"]
        if name in skip:
            continue
        if field["type"] == "list":
            rows = _ingredients_from_form(form)
            if rows:
                data[name] = rows
            elif field["required"]:
                raise ValueError(f"missing required field: {name}")
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
        cut = data.get("cut") or schema_type.removeprefix("grill_")
        cut = str(cut).replace("_", " ")
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
        {"name": "cut", "type": "string", "required": False},
    ]
    form = {
        "cuisine": "Argentine",
        "cooking_method": "grilling",
        "internal_temp_celsius": "57",
        "heat_source": "grill",
        "duration_minutes": "18",
        "cut": "ribeye",
    }
    entry = build_entry("grill_red_meat", fields, form, "tester")
    assert entry["schema_type"] == "grill_red_meat"
    assert entry["data"]["internal_temp_celsius"] == 57.0
    assert entry["data"]["cut"] == "ribeye"
    assert set(entry["data"]["outcome"]) == {
        "doneness", "char_level", "juiciness_score", "food_safety_score", "quality_score"
    }
    assert entry["title"] == "Argentine ribeye on grill (tester)", entry["title"]

    # a cut left blank falls back to the meat kind in the title
    no_cut = build_entry("grill_red_meat", fields, {k: v for k, v in form.items() if k != "cut"}, "t")
    assert no_cut["title"] == "Argentine red meat on grill (t)", no_cut["title"]

    # poultry and fish reuse the same fields, different schema_type -> formula
    for st, cut in (("grill_poultry", "thigh"), ("grill_fish", "fillet")):
        e = build_entry(st, fields, {**form, "internal_temp_celsius": "74", "cut": cut}, "tester")
        assert set(e["data"]["outcome"]) == {
            "doneness", "char_level", "juiciness_score", "food_safety_score", "quality_score"
        }
        assert e["title"] == f"Argentine {cut} on grill (tester)", e["title"]

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

    beer_fields = wine_fields + [
        {"name": "hop_grams", "type": "number", "required": True},
        {"name": "hop_alpha_acid_percent", "type": "number", "required": True},
        {"name": "boil_time_minutes", "type": "number", "required": True},
    ]
    beer_form = {
        "cuisine": "German", "cooking_method": "fermenting",
        "fermentation_days": "21", "yeast_strain": "US-05",
        "starting_gravity": "1.052", "final_gravity": "1.011",
        "hop_grams": "45", "hop_alpha_acid_percent": "7.0", "boil_time_minutes": "60",
    }
    beer = build_entry("fermentation_beer", beer_fields, beer_form, "brewer")
    assert beer["data"]["hop_grams"] == 45.0
    assert set(beer["data"]["outcome"]) == {
        "abv", "ibu", "clarity", "aroma_score", "quality_score"
    }
    assert beer["title"] == "German beer, 21-day ferment (brewer)", beer["title"]

    # ingredients: fixed slots -> list; a role nudge lands; the trio fires the egg
    ing_fields = fields + [{"name": "ingredients", "type": "list", "required": False}]
    ing_form = {**form, "ingredient_name_0": "salt", "ingredient_qty_0": "5",
                "ingredient_unit_0": "g", "ingredient_name_1": "garlic"}
    e_ing = build_entry("grill_red_meat", ing_fields, ing_form, "t")
    assert e_ing["data"]["ingredients"] == [
        {"name": "salt", "quantity": 5.0, "unit": "g"}, {"name": "garlic"}], e_ing["data"]["ingredients"]
    egg_form = {**form, "ingredient_name_0": "honey", "ingredient_name_1": "soy sauce",
                "ingredient_name_2": "garlic"}
    e_egg = build_entry("grill_red_meat", ing_fields, egg_form, "t")
    assert e_egg["data"]["outcome"].get("notes", "").startswith("\u2728"), e_egg["data"]["outcome"]

    print("generator self-check ok:", entry["title"], "|", wine["title"], "|",
          beer["title"], beer["data"]["outcome"])
