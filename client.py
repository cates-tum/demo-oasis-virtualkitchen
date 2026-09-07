"""Talks to Nexus: GET /schemas to learn field lists, POST /entries to
push a generated experiment. This app keeps no schema copy of its own.
"""
import os

import httpx

OASIS_URL = os.getenv("OASIS_URL", "http://localhost:8000").rstrip("/")
OASIS_PUBLIC_URL = os.getenv("OASIS_PUBLIC_URL", OASIS_URL).rstrip("/")
TIMEOUT = 10.0


class OasisUnavailable(RuntimeError):
    """Nexus did not answer, or answered with an error."""


def ping():
    """True if Nexus is reachable. Never raises."""
    try:
        r = httpx.get(f"{OASIS_URL}/", timeout=3.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _load_all_schemas():
    try:
        r = httpx.get(f"{OASIS_URL}/schemas", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise OasisUnavailable(f"could not fetch {OASIS_URL}/schemas: {e}") from e


def get_schema_fields(schema_type):
    """Ordered, flattened field list for `schema_type`, inherited fields from
    `extends` merged in first. Each item: {name, type, required, enum, group},
    where `group` is the schema that declared the field (so the UI can separate
    inherited base fields from bench-specific ones). The computed `outcome`
    object is left in the list; callers skip it themselves.
    """
    all_schemas = _load_all_schemas()
    if schema_type not in all_schemas:
        raise OasisUnavailable(f"unknown schema_type: {schema_type}")

    def collect(name, seen):
        node = all_schemas.get(name)
        if not node or name in seen:
            return []
        seen.add(name)
        parent = node.get("extends")
        fields = list(collect(parent, seen)) if parent else []
        known = {f["name"] for f in fields}
        for fname, spec in (node.get("fields") or {}).items():
            spec = spec if isinstance(spec, dict) else {}
            item = {
                "name": fname,
                "type": spec.get("type", "string"),
                "required": bool(spec.get("required")),
                "enum": spec.get("enum"),
                "group": name,
            }
            if fname in known:  # child overrides parent
                fields = [item if f["name"] == fname else f for f in fields]
            else:
                fields.append(item)
                known.add(fname)
        return fields

    return collect(schema_type, set())


def post_entry(schema_type, title, submitted_by, data):
    """POST one entry. Returns the created entry dict (includes its id)."""
    payload = {
        "schema_type": schema_type,
        "title": title,
        "submitted_by": submitted_by or None,
        "data": data,
    }
    try:
        r = httpx.post(f"{OASIS_URL}/entries", json=payload, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise OasisUnavailable(f"POST {OASIS_URL}/entries failed: {e}") from e
    if r.status_code != 201:
        raise OasisUnavailable(f"Nexus rejected the entry ({r.status_code}): {r.text}")
    return r.json()


def entry_url(entry_id):
    """Where a visitor can see the pushed entry. Nexus has no explorer
    UI yet, so this points at the raw JSON endpoint; swap when one exists."""
    return f"{OASIS_PUBLIC_URL}/entries/{entry_id}"


if __name__ == "__main__":
    fields = get_schema_fields("grill_red_meat")
    names = [f["name"] for f in fields]
    assert "cuisine" in names and "cooking_method" in names, names  # inherited
    assert "internal_temp_celsius" in names and "heat_source" in names, names
    assert "outcome" in names, names
    req = {f["name"] for f in fields if f["required"]}
    assert req == {"cuisine", "cooking_method", "internal_temp_celsius", "heat_source"}, req
    by_name = {f["name"]: f for f in fields}
    assert by_name["heat_source"]["enum"] == ["grill", "pan", "oven"], by_name["heat_source"]
    assert by_name["cuisine"]["enum"] is None
    assert by_name["cuisine"]["group"] == "base_recipe_attempt", by_name["cuisine"]
    assert by_name["heat_source"]["group"] == "grill_red_meat", by_name["heat_source"]
    print("client self-check ok:", names)
