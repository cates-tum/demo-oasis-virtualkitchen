"""Rule engine for the grilling and fermentation benches.

Wired so far: grill_red_meat, fermentation_wine. Every outcome follows the same
shape: bounded input ranges -> a real-reference formula -> small bounded noise
-> output clamped to a sane range.

Wine quality goes through engine/wine_model.py (a regression pre-fit offline on
the UCI Wine Quality dataset). beer / poultry / fish come later.
"""
import math
import os
import random

from engine import wine_model

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


# --- fermentation bench: wine -------------------------------------------------

# Plausible operating ranges for the wine form inputs. Outside values clamp in.
GRAVITY_START_RANGE = (1.050, 1.130)
GRAVITY_FINAL_RANGE = (0.985, 1.030)
FERMENT_DAYS_RANGE = (3.0, 120.0)

# Titratable acidity of finished wine, g/L. Typical table wine sits ~5-9.
ACIDITY_RANGE = (3.0, 12.0)


def wine_abv(starting_gravity, final_gravity):
    """Same gravity-drop formula as beer: ABV ~= (SG - FG) * 131.25."""
    sg = _clamp(starting_gravity, *GRAVITY_START_RANGE)
    fg = _clamp(final_gravity, *GRAVITY_FINAL_RANGE)
    abv = (sg - fg) * 131.25
    return round(_clamp(abv + _noise(-0.3, 0.3), 0.0, 20.0), 2)


def wine_acidity(final_gravity, fermentation_days, yeast_strain):
    """Rule-based titratable acidity. Base ~6 g/L, nudged by residual sweetness
    (higher FG reads a touch more acidic on the palate here), ferment length,
    and a small per-strain term."""
    fg = _clamp(final_gravity, *GRAVITY_FINAL_RANGE)
    d = _clamp(fermentation_days, *FERMENT_DAYS_RANGE)
    profile = wine_model.get_profile(yeast_strain)
    raw = 6.0 + (fg - 0.996) * 120 + d * 0.01 + profile["va_bump"] * 10
    return round(_clamp(raw + _noise(-0.3, 0.3), *ACIDITY_RANGE), 2)


def wine_clarity(fermentation_days, final_gravity):
    """String label. Longer ferment and a lower finishing gravity mean more
    time to drop bright. Deterministic: it is a label off a clarity index."""
    d = _clamp(fermentation_days, *FERMENT_DAYS_RANGE)
    fg = _clamp(final_gravity, *GRAVITY_FINAL_RANGE)
    idx = d * 1.2 - (fg - 0.99) * 300
    if idx < 15:
        return "cloudy"
    if idx < 35:
        return "hazy"
    if idx < 60:
        return "clear"
    return "brilliant"


def wine_aroma_score(fermentation_days, yeast_strain, abv):
    """0-100. Strain sets the baseline; ferment length has a sweet spot around
    a month; hot alcohol above ~14% ABV knocks it back."""
    d = _clamp(fermentation_days, *FERMENT_DAYS_RANGE)
    profile = wine_model.get_profile(yeast_strain)
    raw = profile["aroma_base"] + (12 - abs(d - 30) * 0.4) - max(abv - 14, 0) * 3
    return round(_clamp(raw + _noise(-4, 4)), 1)


def wine_quality_features(starting_gravity, final_gravity, fermentation_days, yeast_strain, abv):
    """Map the four form inputs (+ computed ABV) onto the five physicochemical
    proxies the offline UCI regression expects, each clamped to the dataset's
    observed range."""
    fg = _clamp(final_gravity, *GRAVITY_FINAL_RANGE)
    d = _clamp(fermentation_days, *FERMENT_DAYS_RANGE)
    profile = wine_model.get_profile(yeast_strain)

    def clamp_feat(name, value):
        return _clamp(value, *wine_model.FEATURE_RANGE[name])

    return {
        "alcohol": clamp_feat("alcohol", abv),
        "density": clamp_feat("density", fg),
        "residual_sugar": clamp_feat("residual_sugar", 1.5 + (fg - 0.995) * 400),
        "volatile_acidity": clamp_feat("volatile_acidity", 0.30 + d * 0.006 + profile["va_bump"]),
        "sulphates": clamp_feat("sulphates", profile["sulphates"]),
    }


def wine_quality_score(starting_gravity, final_gravity, fermentation_days, yeast_strain, abv):
    """Offline-fit UCI regression -> 0-10 -> scaled to 0-100, plus bounded noise."""
    feats = wine_quality_features(starting_gravity, final_gravity, fermentation_days, yeast_strain, abv)
    q10 = wine_model.predict_quality(**feats)
    return round(_clamp(q10 * 10 + _noise(-3, 3)), 1)


def fermentation_wine_outcome(starting_gravity, final_gravity, fermentation_days, yeast_strain):
    """Full fermentation_wine outcome block. ABV is computed first; acidity,
    aroma, and quality all consume it."""
    abv = wine_abv(starting_gravity, final_gravity)
    return {
        "abv": abv,
        "acidity": wine_acidity(final_gravity, fermentation_days, yeast_strain),
        "clarity": wine_clarity(fermentation_days, final_gravity),
        "aroma_score": wine_aroma_score(fermentation_days, yeast_strain, abv),
        "quality_score": wine_quality_score(
            starting_gravity, final_gravity, fermentation_days, yeast_strain, abv
        ),
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

    # --- wine ---
    # ABV tracks the gravity drop
    assert 11 < wine_abv(1.090, 1.000) < 13, wine_abv(1.090, 1.000)
    assert wine_abv(1.090, 0.992) > wine_abv(1.090, 1.005)

    # clarity sharpens with a longer ferment
    assert wine_clarity(7, 1.005) == "cloudy", wine_clarity(7, 1.005)
    assert wine_clarity(90, 0.992) == "brilliant", wine_clarity(90, 0.992)

    # a "good" wine spec outscores a "poor" one, both in range
    good = fermentation_wine_outcome(1.095, 0.993, 30, "D47")
    poor = fermentation_wine_outcome(1.060, 1.010, 8, "EC-1118")
    assert good["quality_score"] > poor["quality_score"], (good, poor)

    # every numeric wine outcome stays inside 0-100 across the input grid
    for sg in (1.050, 1.080, 1.110, 1.130):
        for fg in (0.990, 0.998, 1.010, 1.025):
            for days in (3, 21, 60, 120):
                for strain in ("EC-1118", "D47", "unknown-strain"):
                    out = fermentation_wine_outcome(sg, fg, days, strain)
                    for k in ("aroma_score", "quality_score"):
                        assert 0.0 <= out[k] <= 100.0, (sg, fg, days, strain, k, out[k])
                    assert 3.0 <= out["acidity"] <= 12.0, out["acidity"]
                    assert out["clarity"] in {"cloudy", "hazy", "clear", "brilliant"}

    print("formulas self-check ok")
