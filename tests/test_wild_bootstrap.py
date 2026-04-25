import numpy as np
import pandas as pd
from engine.edge_discovery import inference as inf


def make_clustered_df(n_clusters=6, cluster_size=5, rng_seed=2):
    rng = np.random.default_rng(seed=rng_seed)
    rows = []
    for c in range(n_clusters):
        cluster_effect = rng.normal(scale=0.3)
        for i in range(cluster_size):
            phi = rng.lognormal(mean=-3, sigma=0.6)
            sqrt_phi = np.sqrt(phi)
            eps = rng.normal(scale=0.05)
            y = 0.15 * sqrt_phi + 0.03 * phi + cluster_effect + eps
            rows.append({"y": y, "sqrt_phi": sqrt_phi, "phi": phi, "cluster": f"c{c}"})
    return pd.DataFrame(rows)


def test_wild_cluster_bootstrap_runs():
    df = make_clustered_df(n_clusters=8, cluster_size=4)
    formula = "y ~ sqrt_phi + phi"
    ci = inf.wild_cluster_bootstrap_ci(formula=formula, df=df, cluster_col="cluster", n_bootstrap=100, rng_seed=1)
    # expect CI keys matching params
    assert isinstance(ci, dict)
    assert len(ci) > 0
