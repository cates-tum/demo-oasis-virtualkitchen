"""Pre-fit wine quality model + yeast-strain profiles.

The quality coefficients below were fit offline, once, by ordinary least
squares on the UCI Wine Quality dataset (red, 1599 rows,
archive.ics.uci.edu/ml/machine-learning-databases/wine-quality/).

    quality ~ alcohol + volatile_acidity + sulphates + residual_sugar + density
    n = 1599   R^2 = 0.336

Nothing here is fetched or trained at runtime. `predict_quality` is a plain dot
product. The five inputs are derived from the fermentation form values in
engine/formulas.py (see wine_quality_features there).
"""

QUALITY_INTERCEPT = -7.695581
QUALITY_COEF = {
    "alcohol": 0.319112,
    "volatile_acidity": -1.217017,
    "sulphates": 0.657775,
    "residual_sugar": -0.007307,
    "density": 10.266907,
}

# Dataset feature ranges. Derived inputs are clamped to these so the linear
# model is never extrapolated far past the data it was fit on.
FEATURE_RANGE = {
    "alcohol": (8.4, 14.9),
    "volatile_acidity": (0.12, 1.58),
    "sulphates": (0.33, 2.0),
    "residual_sugar": (0.9, 15.5),
    "density": (0.990, 1.004),
}

# A few real wine yeast strains. `va_bump` nudges volatile acidity, `sulphates`
# feeds the quality model, `aroma_base` seeds aroma_score, `attenuation` is how
# far the strain typically ferments (kept for later use). Unknown strain ->
# DEFAULT_PROFILE, so a new strain still produces an entry.
STRAIN_PROFILES = {
    "EC-1118":  {"va_bump": 0.02, "sulphates": 0.62, "aroma_base": 55, "attenuation": 0.98},
    "71B":      {"va_bump": 0.05, "sulphates": 0.70, "aroma_base": 68, "attenuation": 0.92},
    "D47":      {"va_bump": 0.04, "sulphates": 0.66, "aroma_base": 72, "attenuation": 0.90},
    "RC-212":   {"va_bump": 0.03, "sulphates": 0.72, "aroma_base": 70, "attenuation": 0.93},
    "K1-V1116": {"va_bump": 0.03, "sulphates": 0.60, "aroma_base": 64, "attenuation": 0.95},
}
DEFAULT_PROFILE = {"va_bump": 0.04, "sulphates": 0.65, "aroma_base": 60, "attenuation": 0.94}


def get_profile(strain):
    return STRAIN_PROFILES.get(strain, DEFAULT_PROFILE)


def predict_quality(alcohol, volatile_acidity, sulphates, residual_sugar, density):
    """Return the fitted quality on the dataset's 0-10 scale, clamped to 0-10.
    Callers scale to 0-100."""
    feats = {
        "alcohol": alcohol,
        "volatile_acidity": volatile_acidity,
        "sulphates": sulphates,
        "residual_sugar": residual_sugar,
        "density": density,
    }
    q = QUALITY_INTERCEPT + sum(QUALITY_COEF[k] * feats[k] for k in QUALITY_COEF)
    return max(0.0, min(10.0, q))


if __name__ == "__main__":
    # dataset-mean inputs should land near the dataset-mean quality (~5.6)
    mid = predict_quality(10.42, 0.528, 0.658, 2.54, 0.9967)
    assert 5.0 < mid < 6.2, mid
    # a "good" wine scores higher than a "poor" one
    good = predict_quality(13.0, 0.30, 0.80, 2.0, 0.994)
    poor = predict_quality(9.0, 1.05, 0.50, 8.0, 1.002)
    assert good > mid > poor, (good, mid, poor)
    assert 0.0 <= poor and good <= 10.0
    assert get_profile("nope") is DEFAULT_PROFILE
    print(f"wine_model self-check ok: mid={mid:.2f} good={good:.2f} poor={poor:.2f}")
