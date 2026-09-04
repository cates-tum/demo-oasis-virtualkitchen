"""Rule engine for the grilling bench.

Slice one covers grill_red_meat only. Every outcome follows the same shape:
bounded input ranges -> a real-reference formula -> small bounded noise ->
output clamped to 0-100.

beer/wine formulas will be added here (or alongside) later.
"""
import math
import os
import random

# Optional: VK_SEED=<int> makes formula noise reproducible across runs (handy
# for a scripted demo). Unset means fresh randomness each experiment.
_seed = os.getenv("VK_SEED", "").strip()
if _seed:
    random.seed(int(_seed))

# USDA minimum safe internal temp for red-meat steaks/roasts, in Celsius
# (145 F, plus a 3-minute rest). Matches the reference comment in
# pseudo-oasis/schemas/grill_red_meat.yaml. Ground red meat is higher (~71 C)
# and out of scope.
SAFE_TEMP_RED_MEAT = 63.0

# How aggressively each heat source browns the surface.
HEAT_FACTOR = {"grill": 1.0, "pan": 0.7, "oven": 0.35}

# "Most people like it here" score, keyed by the doneness label.
DONENESS_PREF = {
    "rare": 70,
    "medium_rare": 90,
    "medium": 95,
    "medium_well": 80,
    "well_done": 60,
}

# Plausible operating ranges for the two grill inputs. Values outside these are
# clamped in, so a formula never sees a wild number.
TEMP_RANGE = (40.0, 90.0)
DURATION_RANGE = (1.0, 120.0)


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _noise(lo, hi):
    return random.uniform(lo, hi)


def doneness(internal_temp_celsius):
    """Steak doneness band read straight off internal temp. No noise: it is a
    label, not a measurement."""
    t = _clamp(internal_temp_celsius, *TEMP_RANGE)
    if t < 52:
        return "rare"
    if t < 57:
        return "medium_rare"
    if t < 63:
        return "medium"
    if t < 69:
        return "medium_well"
    return "well_done"


def food_safety_score(internal_temp_celsius):
    """Logistic curve centred on the 63 C reference (scale 2.5 C): collapses
    below it, saturates high above it. This is the outcome where the real
    domain rule matters most for demo credibility."""
    t = _clamp(internal_temp_celsius, *TEMP_RANGE)
    raw = 100.0 / (1.0 + math.exp(-(t - SAFE_TEMP_RED_MEAT) / 2.5))
    return round(_clamp(raw + _noise(-2, 2)), 1)


def char_level(internal_temp_celsius, heat_source, duration_minutes):
    """Surface Maillard + burning: mostly cook time and how hot the source
    runs, a little from peak temp."""
    t = _clamp(internal_temp_celsius, *TEMP_RANGE)
    d = _clamp(duration_minutes, *DURATION_RANGE)
    factor = HEAT_FACTOR.get(heat_source, 0.7)
    raw = factor * (1.1 * d + 0.6 * max(t - 55, 0))
    return round(_clamp(raw + _noise(-4, 4)), 1)


def juiciness_score(internal_temp_celsius, heat_source, duration_minutes):
    """Moisture loss rises with internal temp (steep past ~54 C) and with
    total cook time. Gentle oven convection keeps a touch more."""
    t = _clamp(internal_temp_celsius, *TEMP_RANGE)
    d = _clamp(duration_minutes, *DURATION_RANGE)
    source_bonus = {"grill": 0, "pan": 0, "oven": 4}.get(heat_source, 0)
    raw = 100.0 - 2.2 * max(t - 54, 0) - 0.35 * d + source_bonus
    return round(_clamp(raw + _noise(-3, 3)), 1)


def quality_score(food_safety, juiciness, char, doneness_label):
    """Composite: weighted blend of the other three outcomes plus a doneness
    preference. Unsafe food caps the overall score."""
    char_fit = _clamp(100.0 - 1.5 * abs(char - 35))
    raw = (
        0.30 * food_safety
        + 0.35 * juiciness
        + 0.20 * char_fit
        + 0.15 * DONENESS_PREF.get(doneness_label, 75)
    )
    q = _clamp(raw + _noise(-3, 3))
    if food_safety < 50:
        q = min(q, food_safety)
    return round(q, 1)


def grill_red_meat_outcome(internal_temp_celsius, heat_source, duration_minutes):
    """Run the full grill_red_meat outcome block. Order matters: quality
    consumes the other four."""
    done = doneness(internal_temp_celsius)
    safety = food_safety_score(internal_temp_celsius)
    char = char_level(internal_temp_celsius, heat_source, duration_minutes)
    juice = juiciness_score(internal_temp_celsius, heat_source, duration_minutes)
    quality = quality_score(safety, juice, char, done)
    return {
        "doneness": done,
        "char_level": char,
        "juiciness_score": juice,
        "food_safety_score": safety,
        "quality_score": quality,
    }


if __name__ == "__main__":
    random.seed(0)

    # doneness bands
    assert doneness(48) == "rare", doneness(48)
    assert doneness(54) == "medium_rare", doneness(54)
    assert doneness(60) == "medium", doneness(60)
    assert doneness(66) == "medium_well", doneness(66)
    assert doneness(75) == "well_done", doneness(75)

    # food safety collapses below 63 C, saturates above it
    assert food_safety_score(55) < 20, food_safety_score(55)
    assert food_safety_score(75) > 95, food_safety_score(75)
    assert 40 < food_safety_score(63) < 60, food_safety_score(63)

    # juiciness falls as the steak goes from rare to overcooked
    juicy_rare = juiciness_score(55, "grill", 15)
    juicy_burnt = juiciness_score(80, "grill", 60)
    assert juicy_rare > juicy_burnt, (juicy_rare, juicy_burnt)

    # char rises with time and with a hotter source
    assert char_level(60, "grill", 45) > char_level(60, "oven", 10)

    # every numeric outcome stays inside 0-100 across the input grid
    for t in range(40, 91, 5):
        for src in ("grill", "pan", "oven"):
            for d in (1, 20, 60, 120):
                out = grill_red_meat_outcome(t, src, d)
                for k in ("char_level", "juiciness_score", "food_safety_score", "quality_score"):
                    assert 0.0 <= out[k] <= 100.0, (t, src, d, k, out[k])

    # unsafe food caps quality
    capped = quality_score(food_safety=8.0, juiciness=95.0, char=35.0, doneness_label="medium_rare")
    assert capped <= 8.0, capped

    print("formulas self-check ok")
