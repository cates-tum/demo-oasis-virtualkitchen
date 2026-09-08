"""Rule engine for the grilling and fermentation benches.

Wired: grill_red_meat / grill_poultry / grill_fish, fermentation_wine,
fermentation_beer. Every outcome follows the same shape: bounded input ranges
-> a real-reference formula -> small bounded noise -> output clamped to a sane
range.

The three grilling types share one outcome block, parametrised per meat by
safe internal temp, doneness bands, and how fast juiciness/char move. Wine
quality goes through engine/wine_model.py (a regression pre-fit offline on the
UCI Wine Quality dataset). Beer uses real homebrew math (gravity-drop ABV,
Tinseth IBU) plus rule-based clarity/aroma/quality.
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

# Minimum safe internal temp per meat, in Celsius. USDA-style references, and
# consistent with the comments in Nexus's grill schema YAMLs:
# red meat / fish ~63 C (145 F), poultry ~74 C (165 F). Ground meat is higher
# and out of scope.
SAFE_TEMP = {"red_meat": 63.0, "poultry": 74.0, "fish": 63.0}
SAFE_TEMP_RED_MEAT = SAFE_TEMP["red_meat"]  # kept: referenced by the web layer

# How aggressively each heat source browns the surface.
HEAT_FACTOR = {"grill": 1.0, "pan": 0.7, "oven": 0.35}

# Doneness bands per meat: (upper temp, label, quality preference 0-100).
# The temps are culinary references; the preference is "how much people like
# it there" and feeds quality_score. Poultry has no "rare" band, fish is done
# at a lower temp than red meat.
DONENESS_BANDS = {
    "red_meat": [(52, "rare", 70), (57, "medium_rare", 90), (63, "medium", 95),
                 (69, "medium_well", 80), (999, "well_done", 60)],
    "poultry":  [(70, "underdone", 25), (78, "just_done", 92), (85, "cooked_through", 85),
                 (92, "well_done", 68), (999, "dry", 40)],
    "fish":     [(48, "rare", 60), (54, "medium_rare", 88), (60, "medium", 92),
                 (68, "well_done", 70), (999, "overcooked", 40)],
}

# Juiciness: moisture loss starts at `knee` C and then falls `slope` points/C.
# Poultry breast holds moisture until it is done, then dries fast; fish is
# delicate and dries very fast once past its knee.
JUICINESS_CURVE = {"red_meat": (54, 2.2), "poultry": (68, 3.0), "fish": (52, 3.5)}

# Fish chars and scorches faster than red meat; poultry skin browns readily.
CHAR_SENSITIVITY = {"red_meat": 1.0, "poultry": 1.05, "fish": 1.15}

# Per-cut tuning for the grilling bench (schema field `grill_*.cut`). Two levers:
#   ideal_temp  deg C where this cut eats best; feeds quality_score as a
#               doneness-fit term. None -> fall back to the DONENESS_BANDS
#               preference, i.e. no cut effect on quality.
#   moisture    multiplier on the juiciness drying slope. >1 for a lean or thin
#               cut that dries fast, <1 for a marbled or collagen-rich cut that
#               holds. Safe internal temp is NOT a lever: it is per species
#               (SAFE_TEMP), not per cut.
# Keyed by kind then cut because a name like "tenderloin" or "whole" means
# different things across benches. Unknown or blank cut -> DEFAULT_CUT_PROFILE,
# so a cut added to the Nexus enum later still produces a sane entry with no
# code change here.
# ponytail: two levers, no per-cut knee shift or char factor; add one if the
# demo needs a cut to visibly change char.
DEFAULT_CUT_PROFILE = {"ideal_temp": None, "moisture": 1.0}
CUT_PROFILE = {
    "red_meat": {
        "ribeye":     {"ideal_temp": 57, "moisture": 0.80},
        "striploin":  {"ideal_temp": 55, "moisture": 0.95},
        "sirloin":    {"ideal_temp": 54, "moisture": 1.05},
        "tenderloin": {"ideal_temp": 54, "moisture": 1.25},
        "flank":      {"ideal_temp": 54, "moisture": 1.30},
        "skirt":      {"ideal_temp": 54, "moisture": 1.25},
        "flat_iron":  {"ideal_temp": 56, "moisture": 1.00},
        "tri_tip":    {"ideal_temp": 57, "moisture": 1.00},
        "brisket":    {"ideal_temp": 92, "moisture": 0.70},
        "short_rib":  {"ideal_temp": 90, "moisture": 0.70},
        "lamb_chop":  {"ideal_temp": 58, "moisture": 0.95},
        "pork_chop":  {"ideal_temp": 63, "moisture": 1.10},
    },
    "poultry": {
        "breast":      {"ideal_temp": 74, "moisture": 1.35},
        "thigh":       {"ideal_temp": 80, "moisture": 0.80},
        "drumstick":   {"ideal_temp": 82, "moisture": 0.85},
        "wing":        {"ideal_temp": 80, "moisture": 1.00},
        "leg_quarter": {"ideal_temp": 82, "moisture": 0.80},
        "tenderloin":  {"ideal_temp": 74, "moisture": 1.40},
        "half":        {"ideal_temp": 78, "moisture": 1.00},
        "whole":       {"ideal_temp": 78, "moisture": 0.95},
    },
    "fish": {
        "fillet":  {"ideal_temp": 54, "moisture": 1.30},
        "steak":   {"ideal_temp": 52, "moisture": 1.15},
        "loin":    {"ideal_temp": 50, "moisture": 1.15},
        "collar":  {"ideal_temp": 60, "moisture": 0.85},
        "portion": {"ideal_temp": 55, "moisture": 1.15},
        "whole":   {"ideal_temp": 58, "moisture": 0.95},
    },
}


def cut_profile(kind, cut):
    """Per-cut lever set for `kind`. Falls back to DEFAULT_CUT_PROFILE for a
    blank or unrecognised cut."""
    return CUT_PROFILE.get(kind, {}).get(cut or "", DEFAULT_CUT_PROFILE)

# Plausible operating ranges for the two grill inputs. Values outside these are
# clamped in, so a formula never sees a wild number.
TEMP_RANGE = (35.0, 100.0)
DURATION_RANGE = (1.0, 120.0)


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _noise(lo, hi):
    return random.uniform(lo, hi)


def _doneness(internal_temp_celsius, kind):
    t = _clamp(internal_temp_celsius, *TEMP_RANGE)
    for upper, label, pref in DONENESS_BANDS[kind]:
        if t < upper:
            return label, pref
    label, pref = DONENESS_BANDS[kind][-1][1], DONENESS_BANDS[kind][-1][2]
    return label, pref


def doneness(internal_temp_celsius, kind="red_meat"):
    """Doneness band read straight off internal temp. No noise: it is a label,
    not a measurement."""
    return _doneness(internal_temp_celsius, kind)[0]


def food_safety_score(internal_temp_celsius, safe_temp=SAFE_TEMP_RED_MEAT):
    """Logistic curve centred on the meat's safe internal temp (scale 2.5 C):
    collapses below it, saturates high above it. This is the outcome where the
    real domain rule matters most for demo credibility."""
    t = _clamp(internal_temp_celsius, *TEMP_RANGE)
    raw = 100.0 / (1.0 + math.exp(-(t - safe_temp) / 2.5))
    return round(_clamp(raw + _noise(-2, 2)), 1)


def char_level(internal_temp_celsius, heat_source, duration_minutes, kind="red_meat"):
    """Surface Maillard + burning: mostly cook time and how hot the source
    runs, a little from peak temp, scaled by how easily the meat chars."""
    t = _clamp(internal_temp_celsius, *TEMP_RANGE)
    d = _clamp(duration_minutes, *DURATION_RANGE)
    factor = HEAT_FACTOR.get(heat_source, 0.7) * CHAR_SENSITIVITY[kind]
    raw = factor * (1.1 * d + 0.6 * max(t - 55, 0))
    return round(_clamp(raw + _noise(-4, 4)), 1)


def juiciness_score(internal_temp_celsius, heat_source, duration_minutes, kind="red_meat",
                    moisture=1.0):
    """Moisture loss rises past the meat's knee temp and with total cook time.
    Gentle oven convection keeps a touch more. `moisture` scales the drying
    slope per cut (>1 dries faster)."""
    t = _clamp(internal_temp_celsius, *TEMP_RANGE)
    d = _clamp(duration_minutes, *DURATION_RANGE)
    knee, slope = JUICINESS_CURVE[kind]
    source_bonus = {"grill": 0, "pan": 0, "oven": 4}.get(heat_source, 0)
    raw = 100.0 - slope * moisture * max(t - knee, 0) - 0.35 * d + source_bonus
    return round(_clamp(raw + _noise(-3, 3)), 1)


def quality_score(food_safety, juiciness, char, doneness_pref):
    """Composite: weighted blend of the other three outcomes plus the doneness
    preference (0-100, from DONENESS_BANDS). Unsafe food caps the overall
    score."""
    char_fit = _clamp(100.0 - 1.5 * abs(char - 35))
    raw = (
        0.30 * food_safety
        + 0.35 * juiciness
        + 0.20 * char_fit
        + 0.15 * doneness_pref
    )
    q = _clamp(raw + _noise(-3, 3))
    if food_safety < 50:
        q = min(q, food_safety)
    return round(q, 1)


def _grill_outcome(internal_temp_celsius, heat_source, duration_minutes, kind, cut=None):
    """Shared grilling-bench outcome block. Order matters: quality consumes the
    other four. `cut` (schema enum) shifts the doneness preference and the
    drying rate; it never moves the food-safety reference."""
    label, pref = _doneness(internal_temp_celsius, kind)
    prof = cut_profile(kind, cut)
    if prof["ideal_temp"] is not None:
        t = _clamp(internal_temp_celsius, *TEMP_RANGE)
        pref = _clamp(100.0 - 2.0 * abs(t - prof["ideal_temp"]))
    safety = food_safety_score(internal_temp_celsius, SAFE_TEMP[kind])
    char = char_level(internal_temp_celsius, heat_source, duration_minutes, kind)
    juice = juiciness_score(internal_temp_celsius, heat_source, duration_minutes, kind,
                            prof["moisture"])
    quality = quality_score(safety, juice, char, pref)
    return {
        "doneness": label,
        "char_level": char,
        "juiciness_score": juice,
        "food_safety_score": safety,
        "quality_score": quality,
    }


def grill_red_meat_outcome(internal_temp_celsius, heat_source, duration_minutes, cut=None):
    return _grill_outcome(internal_temp_celsius, heat_source, duration_minutes, "red_meat", cut)


def grill_poultry_outcome(internal_temp_celsius, heat_source, duration_minutes, cut=None):
    return _grill_outcome(internal_temp_celsius, heat_source, duration_minutes, "poultry", cut)


def grill_fish_outcome(internal_temp_celsius, heat_source, duration_minutes, cut=None):
    return _grill_outcome(internal_temp_celsius, heat_source, duration_minutes, "fish", cut)


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


# --- fermentation bench: beer -----------------------------------------------

# Beer wort is thinner than must; keep a beer-specific starting-gravity range.
BEER_GRAVITY_START_RANGE = (1.020, 1.120)
HOP_GRAMS_RANGE = (0.0, 300.0)
HOP_AA_RANGE = (2.0, 22.0)
BOIL_TIME_RANGE = (0.0, 120.0)

# CLAUDE.md: assume a fixed batch size unless the schema carries a volume
# field. It does not, so the Tinseth calc uses this. If a volume field is
# added to fermentation_beer later, thread it through beer_ibu.
BATCH_VOLUME_LITERS = 20.0

# A few real brewing yeasts. `aroma_base` seeds aroma_score; `floc`
# (flocculation, 0-1) drives how fast the beer drops bright. Unknown strain ->
# DEFAULT_BEER_YEAST so a new strain still produces an entry.
BEER_YEAST = {
    "US-05":   {"aroma_base": 48, "floc": 0.60},   # American ale, clean
    "S-04":    {"aroma_base": 58, "floc": 0.90},    # English ale, fruity, fast to clear
    "WLP001":  {"aroma_base": 50, "floc": 0.55},    # California ale, clean
    "WB-06":   {"aroma_base": 74, "floc": 0.20},    # wheat / hefe, banana-clove, stays hazy
    "T-58":    {"aroma_base": 66, "floc": 0.50},    # Belgian, spicy / estery
    "W-34/70": {"aroma_base": 40, "floc": 0.65},    # lager, very clean
}
DEFAULT_BEER_YEAST = {"aroma_base": 52, "floc": 0.55}


def beer_yeast_profile(strain):
    return BEER_YEAST.get(strain, DEFAULT_BEER_YEAST)


def beer_abv(starting_gravity, final_gravity):
    """ABV ~= (SG - FG) * 131.25, the standard homebrew estimate."""
    sg = _clamp(starting_gravity, *BEER_GRAVITY_START_RANGE)
    fg = _clamp(final_gravity, *GRAVITY_FINAL_RANGE)
    abv = (sg - fg) * 131.25
    return round(_clamp(abv + _noise(-0.3, 0.3), 0.0, 15.0), 2)


def beer_ibu(hop_grams, hop_alpha_acid_percent, boil_time_minutes, starting_gravity,
             batch_volume_liters=BATCH_VOLUME_LITERS):
    """Tinseth (1997) IBU estimate.

        mg/L alpha acids = AA_decimal * grams * 1000 / volume_liters
        bigness factor   = 1.65 * 0.000125 ** (OG - 1)
        boil-time factor = (1 - e**(-0.04 * minutes)) / 4.15
        IBU              = bigness * boil_time * mg/L
    """
    grams = _clamp(hop_grams, *HOP_GRAMS_RANGE)
    aa = _clamp(hop_alpha_acid_percent, *HOP_AA_RANGE) / 100.0
    minutes = _clamp(boil_time_minutes, *BOIL_TIME_RANGE)
    og = _clamp(starting_gravity, *BEER_GRAVITY_START_RANGE)

    mgl = aa * grams * 1000.0 / batch_volume_liters
    bigness = 1.65 * 0.000125 ** (og - 1.0)
    boil_factor = (1.0 - math.exp(-0.04 * minutes)) / 4.15
    ibu = bigness * boil_factor * mgl
    return round(_clamp(ibu + _noise(-2, 2), 0.0, 120.0), 1)


def beer_clarity(fermentation_days, final_gravity, yeast_strain):
    """String label. Longer conditioning, a lower finishing gravity, and a
    higher-flocculation strain all mean a brighter beer. Deterministic."""
    d = _clamp(fermentation_days, *FERMENT_DAYS_RANGE)
    fg = _clamp(final_gravity, *GRAVITY_FINAL_RANGE)
    floc = beer_yeast_profile(yeast_strain)["floc"]
    idx = d * 1.1 - (fg - 0.995) * 250 + floc * 25
    if idx < 15:
        return "cloudy"
    if idx < 32:
        return "hazy"
    if idx < 55:
        return "clear"
    return "brilliant"


def beer_aroma_score(fermentation_days, yeast_strain, ibu, hop_grams):
    """0-100. Strain baseline plus hop presence, with a ferment-length sweet
    spot near two weeks; aggressive bitterness above ~80 IBU masks balance."""
    d = _clamp(fermentation_days, *FERMENT_DAYS_RANGE)
    grams = _clamp(hop_grams, *HOP_GRAMS_RANGE)
    raw = (beer_yeast_profile(yeast_strain)["aroma_base"]
           + min(grams * 0.15, 20)
           + (8 - abs(d - 18) * 0.3)
           - max(ibu - 80, 0) * 0.3)
    return round(_clamp(raw + _noise(-4, 4)), 1)


def beer_quality_score(ibu, clarity, aroma_score, fermentation_days, starting_gravity, final_gravity):
    """Rule-based (no dataset for beer). Rewards a balanced BU:GU ratio,
    reasonable apparent attenuation, aroma, clarity, and enough conditioning
    time."""
    og = _clamp(starting_gravity, *BEER_GRAVITY_START_RANGE)
    fg = _clamp(final_gravity, *GRAVITY_FINAL_RANGE)
    gu = (og - 1.0) * 1000.0                       # gravity units, ~50 for OG 1.050
    bugu = ibu / gu if gu > 0 else 0.0             # bitterness-to-gravity, ~0.6 balanced
    attenuation = (og - fg) / (og - 1.0) if og > 1.0 else 0.0   # apparent, ~0.78 typical

    balance_fit = _clamp(100 - abs(bugu - 0.6) * 120)
    atten_fit = _clamp(100 - abs(attenuation - 0.78) * 300)
    cond_fit = _clamp(100 - abs(fermentation_days - 21) * 3)
    clarity_score = {"cloudy": 45, "hazy": 65, "clear": 85, "brilliant": 95}[clarity]

    raw = (0.30 * balance_fit + 0.20 * atten_fit + 0.20 * aroma_score
           + 0.15 * clarity_score + 0.15 * cond_fit)
    return round(_clamp(raw + _noise(-3, 3)), 1)


def fermentation_beer_outcome(starting_gravity, final_gravity, fermentation_days, yeast_strain,
                              hop_grams, hop_alpha_acid_percent, boil_time_minutes):
    """Full fermentation_beer outcome block. ABV and IBU are computed first;
    aroma and quality consume IBU, quality also consumes clarity."""
    abv = beer_abv(starting_gravity, final_gravity)
    ibu = beer_ibu(hop_grams, hop_alpha_acid_percent, boil_time_minutes, starting_gravity)
    clarity = beer_clarity(fermentation_days, final_gravity, yeast_strain)
    aroma = beer_aroma_score(fermentation_days, yeast_strain, ibu, hop_grams)
    return {
        "abv": abv,
        "ibu": ibu,
        "clarity": clarity,
        "aroma_score": aroma,
        "quality_score": beer_quality_score(
            ibu, clarity, aroma, fermentation_days, starting_gravity, final_gravity
        ),
    }


if __name__ == "__main__":
    random.seed(0)

    # doneness bands (red meat)
    assert doneness(48) == "rare", doneness(48)
    assert doneness(54) == "medium_rare", doneness(54)
    assert doneness(60) == "medium", doneness(60)
    assert doneness(66) == "medium_well", doneness(66)
    assert doneness(75) == "well_done", doneness(75)
    # poultry / fish bands differ
    assert doneness(60, "poultry") == "underdone", doneness(60, "poultry")
    assert doneness(76, "poultry") == "just_done", doneness(76, "poultry")
    assert doneness(50, "fish") == "medium_rare", doneness(50, "fish")
    assert doneness(75, "fish") == "overcooked", doneness(75, "fish")

    # food safety collapses below the safe temp, saturates above it, per meat
    assert food_safety_score(55, SAFE_TEMP["red_meat"]) < 20
    assert food_safety_score(75, SAFE_TEMP["red_meat"]) > 95
    # 68 C is safe for red meat/fish but still unsafe for poultry (74 C)
    assert food_safety_score(68, SAFE_TEMP["fish"]) > 85
    assert food_safety_score(68, SAFE_TEMP["poultry"]) < 25

    # juiciness falls as the meat goes from just-cooked to overcooked
    assert juiciness_score(55, "grill", 15, "red_meat") > juiciness_score(80, "grill", 60, "red_meat")
    # fish dries faster than red meat past its knee
    fish_drop = juiciness_score(55, "grill", 15, "fish") - juiciness_score(72, "grill", 15, "fish")
    meat_drop = juiciness_score(55, "grill", 15, "red_meat") - juiciness_score(72, "grill", 15, "red_meat")
    assert fish_drop > meat_drop, (fish_drop, meat_drop)

    # char rises with time and with a hotter source
    assert char_level(60, "grill", 45) > char_level(60, "oven", 10)

    # every numeric outcome stays inside 0-100 across the input grid, all meats
    # and every cut (plus no-cut and an unknown cut)
    for meat, fn in (("red_meat", grill_red_meat_outcome),
                     ("poultry", grill_poultry_outcome),
                     ("fish", grill_fish_outcome)):
        cuts = [None, "no-such-cut"] + list(CUT_PROFILE[meat])
        for t in range(35, 101, 5):
            for src in ("grill", "pan", "oven"):
                for d in (1, 20, 60, 120):
                    for cut in cuts:
                        out = fn(t, src, d, cut)
                        for k in ("char_level", "juiciness_score", "food_safety_score", "quality_score"):
                            assert 0.0 <= out[k] <= 100.0, (meat, t, src, d, cut, k, out[k])
                        assert out["doneness"] in {lbl for _, lbl, _ in DONENESS_BANDS[meat]}

    # cut moves quality and juiciness but never the food-safety reference
    b_hot = grill_red_meat_outcome(90, "grill", 40, "brisket")
    t_hot = grill_red_meat_outcome(90, "grill", 40, "tenderloin")
    assert b_hot["quality_score"] > t_hot["quality_score"], (b_hot, t_hot)   # collagen cut likes 90 C
    assert b_hot["food_safety_score"] > 95 and t_hot["food_safety_score"] > 95  # 90 C >> 63 C for both
    breast = grill_poultry_outcome(85, "grill", 30, "breast")
    thigh = grill_poultry_outcome(85, "grill", 30, "thigh")
    assert breast["juiciness_score"] < thigh["juiciness_score"], (breast, thigh)

    # blank / unknown cut == no cut effect (same noise seed -> identical result)
    random.seed(7)
    none_cut = grill_fish_outcome(55, "grill", 20, None)
    random.seed(7)
    bad_cut = grill_fish_outcome(55, "grill", 20, "not-a-cut")
    assert none_cut == bad_cut, (none_cut, bad_cut)

    # an undercooked chicken scores badly on safety and overall
    raw_bird = grill_poultry_outcome(63, "grill", 20)
    assert raw_bird["food_safety_score"] < 20, raw_bird
    assert raw_bird["quality_score"] <= raw_bird["food_safety_score"], raw_bird

    # unsafe food caps quality
    capped = quality_score(food_safety=8.0, juiciness=95.0, char=35.0, doneness_pref=90)
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

    # --- beer ---
    # ABV tracks the gravity drop
    assert 4.5 < beer_abv(1.050, 1.012) < 5.5, beer_abv(1.050, 1.012)

    # Tinseth reference: 30 g of 6% AA hops, 60 min boil, OG 1.050, 20 L
    # lands near 20 IBU on standard calculators
    ref_ibu = beer_ibu(30, 6.0, 60, 1.050)
    assert 16 < ref_ibu < 25, ref_ibu
    # more hops and a longer boil both raise IBU
    assert beer_ibu(60, 6.0, 60, 1.050) > ref_ibu
    assert beer_ibu(30, 6.0, 90, 1.050) > beer_ibu(30, 6.0, 15, 1.050)

    # a balanced, well-attenuated, conditioned beer beats a stuck, flabby one
    good_beer = fermentation_beer_outcome(1.052, 1.011, 21, "S-04", 45, 7.0, 60)
    poor_beer = fermentation_beer_outcome(1.038, 1.020, 6, "WB-06", 4, 3.0, 10)
    assert good_beer["quality_score"] > poor_beer["quality_score"], (good_beer, poor_beer)

    # every numeric beer outcome stays inside its clamp across the input grid
    for sg in (1.030, 1.055, 1.085, 1.115):
        for fg in (0.998, 1.008, 1.018, 1.028):
            for days in (3, 18, 45, 120):
                for strain in ("US-05", "W-34/70", "unknown-strain"):
                    for hg, aa, bt in ((0, 3.0, 0), (40, 7.0, 60), (200, 18.0, 90)):
                        out = fermentation_beer_outcome(sg, fg, days, strain, hg, aa, bt)
                        for k in ("aroma_score", "quality_score"):
                            assert 0.0 <= out[k] <= 100.0, (sg, fg, days, strain, k, out[k])
                        assert 0.0 <= out["ibu"] <= 120.0, out["ibu"]
                        assert out["clarity"] in {"cloudy", "hazy", "clear", "brilliant"}

    print("formulas self-check ok")
