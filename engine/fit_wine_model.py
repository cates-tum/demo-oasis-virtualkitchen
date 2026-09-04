"""Offline, one-time fit for the coefficients baked into wine_model.py.

NOT imported at runtime. Run it by hand when you want to refit, then paste the
printed numbers into wine_model.QUALITY_INTERCEPT / QUALITY_COEF.

    curl -sSLO https://archive.ics.uci.edu/ml/machine-learning-databases/wine-quality/winequality-red.csv
    python3 engine/fit_wine_model.py winequality-red.csv

Stdlib only. OLS via the normal equations with Gauss-Jordan elimination.

    quality ~ alcohol + volatile_acidity + sulphates + residual_sugar + density
"""
import csv
import sys

FEATURES = ["alcohol", "volatile acidity", "sulphates", "residual sugar", "density"]


def solve(A, b):
    """Solve A x = b for a small dense square system."""
    m = [row[:] + [b[i]] for i, row in enumerate(A)]
    size = len(m)
    for col in range(size):
        piv = max(range(col, size), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        m[col] = [v / pv for v in m[col]]
        for r in range(size):
            if r != col:
                f = m[r][col]
                m[r] = [v - f * m[col][k] for k, v in enumerate(m[r])]
    return [m[i][size] for i in range(size)]


def main(path):
    with open(path) as f:
        rows = list(csv.DictReader(f, delimiter=";"))

    X = [[1.0] + [float(r[k]) for k in FEATURES] for r in rows]
    y = [float(r["quality"]) for r in rows]
    n, p = len(X), len(X[0])

    XtX = [[sum(X[i][a] * X[i][c] for i in range(n)) for c in range(p)] for a in range(p)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(p)]
    b = solve(XtX, Xty)

    pred = [sum(b[a] * X[i][a] for a in range(p)) for i in range(n)]
    ybar = sum(y) / n
    r2 = 1 - sum((y[i] - pred[i]) ** 2 for i in range(n)) / sum((v - ybar) ** 2 for v in y)

    key = ["intercept"] + [k.replace(" ", "_") for k in FEATURES]
    print(f"n={n}  R^2={r2:.4f}")
    print(f"QUALITY_INTERCEPT = {b[0]:.6f}")
    print("QUALITY_COEF = {")
    for name, coef in zip(key[1:], b[1:]):
        print(f'    "{name}": {coef:.6f},')
    print("}")
    print("FEATURE_RANGE (observed):")
    for k in FEATURES:
        col = [float(r[k]) for r in rows]
        print(f'    "{k.replace(" ", "_")}": ({min(col):.3f}, {max(col):.3f}),')


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 engine/fit_wine_model.py <winequality-red.csv>")
    main(sys.argv[1])
