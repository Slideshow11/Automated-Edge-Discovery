import numpy as np
from engine.edge_discovery import inference as inf


def make_synthetic(n=200, n_clusters=20, seed=0):
    rng = np.random.default_rng(seed)
    clusters = rng.integers(0, n_clusters, n)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    beta0 = 1.0
    beta1 = 2.0
    beta2 = -0.5
    y = beta0 + beta1 * x1 + beta2 * x2 + rng.normal(scale=0.5, size=n)
    import pandas as pd
    return pd.DataFrame({"y": y, "x1": x1, "x2": x2, "cluster": clusters})


def test_cluster_bootstrap_and_wild_return_ci_dict():
    df = make_synthetic(300, 30, seed=7)
    formula = "y ~ x1 + x2"
    ci = inf.cluster_bootstrap_ci(formula=formula, df=df, cluster_col="cluster", n_bootstrap=100, rng_seed=1)
    if ci:
        assert all(isinstance(v, tuple) and len(v) == 2 for v in ci.values())

    ci2 = inf.wild_cluster_bootstrap_ci(formula=formula, df=df, cluster_col="cluster", n_bootstrap=100, rng_seed=1)
    if ci2:
        assert all(isinstance(v, tuple) and len(v) == 2 for v in ci2.values())
