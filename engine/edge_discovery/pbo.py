import math
import math as _math
import numpy as np


def compute_pbo(Y: np.ndarray, n_bootstrap: int = 1000, seed: int = 0) -> tuple[float, float]:
    """Estimate PBO (probability of backtest overfitting) via bootstrap resampling of splits.

    Parameters
    ----------
    Y : np.ndarray
        2D array of shape (n_candidates, n_splits) containing candidate returns/scores.
    n_bootstrap : int, optional
        Number of bootstrap resamples. Capped at 5000 for memory safety. Default 1000.
    seed : int, optional
        Random seed for reproducibility. Default 0.

    Returns
    -------
    tuple[float, float]
        (pbo, pbo_std) where pbo is the probability of backtest overfitting
        and pbo_std is the bootstrap standard error.
    """
    if Y is None:
        return None, None
    if not isinstance(Y, np.ndarray):
        raise TypeError("Y must be a numpy array")
    if Y.ndim != 2:
        raise ValueError("Y must be a 2D array of shape (n_candidates, n_splits)")
    n_candidates, n_splits = Y.shape
    if n_splits < 2:
        raise ValueError("n_splits must be at least 2")
    if n_candidates < 2:
        raise ValueError("n_candidates must be at least 2")

    rng = np.random.default_rng(seed)
    n_bootstrap = min(n_bootstrap, 5000)

    # Compute best candidate per split with tie-breaking (uniform among ties)
    best_idx_per_split = np.array([
        rng.choice(np.flatnonzero(Y[:, j] == np.nanmax(Y[:, j])))
        for j in range(n_splits)
    ], dtype=int)
    # Empirical frequency of each candidate being best
    freqs = np.bincount(best_idx_per_split, minlength=n_candidates) / n_splits
    # PBO: proportion of splits where best candidate is not consistently the same
    pbo = 1.0 - float(np.max(freqs))

    # Bootstrap to estimate std: vectorized resampling of splits
    # Draw all bootstrap indices at once: shape (n_bootstrap, n_splits)
    all_idx = rng.integers(0, n_splits, size=(n_bootstrap, n_splits))

    # Chunk to stay memory-friendly for large n_bootstrap
    chunk_size = 1000
    boots = []
    for start in range(0, n_bootstrap, chunk_size):
        end = min(start + chunk_size, n_bootstrap)
        chunk_idx = all_idx[start:end]  # (chunk, n_splits)

        # Gather: Y[:, chunk_idx] -> shape (n_candidates, chunk, n_splits)
        values = Y[:, chunk_idx]

        # Tie-breaking: np.argmax picks first-encountered max, which biases
        # toward smallest index. Add tiny per-bootstrap noise to break ties
        # uniformly without materially affecting non-tied values.
        noise = rng.uniform(0, 1e-9, size=(end - start, n_splits))
        candidate_scale = np.arange(n_candidates).reshape(-1, 1, 1)  # (n_candidates, 1, 1)
        noisy_values = values + noise * candidate_scale

        best_idx = np.argmax(noisy_values, axis=0)  # (chunk, n_splits)

        # Compute frequencies and PBO per bootstrap sample
        boots_chunk = np.empty(end - start)
        for i, bidx in enumerate(best_idx):
            fb = np.bincount(bidx, minlength=n_candidates) / n_splits
            boots_chunk[i] = 1.0 - np.max(fb)
        boots.append(boots_chunk)

    boots = np.concatenate(boots)
    return float(pbo), float(boots.std(ddof=1))


def deflated_sharpe(Y: np.ndarray, method: str = 'lopez', alpha: float = 0.05, seed: int = 0) -> np.ndarray:
    """Compute deflated Sharpe proxy with multiple-testing penalty.

    Parameters
    ----------
    Y : np.ndarray
        2D array of shape (n_candidates, n_splits) containing candidate returns/scores.
    method : str, optional
        Method for computing deflated Sharpe. Currently only 'lopez' is supported.
        Default 'lopez'.
    alpha : float, optional
        Significance level for bootstrap null estimation. Default 0.05.
    seed : int, optional
        Random seed for reproducibility. Default 0.

    Returns
    -------
    np.ndarray
        Array of deflated Sharpe ratios per candidate.

    Notes
    -----
    The Lopez method computes per-candidate Sharpe = mean/std*sqrt(n_splits),
    then applies a multiple-testing penalty approximated by dividing by log1p(n_candidates)
    and adjusting by the quantile of max Sharpe under bootstrap null.
    The null distribution is estimated via resampling of candidate scores across splits.
    """
    if Y is None:
        return np.array([])
    if not isinstance(Y, np.ndarray):
        raise TypeError("Y must be a numpy array")
    if Y.ndim != 2:
        raise ValueError("Y must be a 2D array of shape (n_candidates, n_splits)")
    n_candidates, n_splits = Y.shape
    if n_splits < 2:
        raise ValueError("n_splits must be at least 2")
    if n_candidates < 1:
        raise ValueError("n_candidates must be at least 1")
    if method != 'lopez':
        raise ValueError(f"Unknown method '{method}'. Only 'lopez' is supported.")

    rng = np.random.default_rng(seed)

    # treat Y rows as candidate scores per split; compute mean/std
    means = np.nanmean(Y, axis=1)
    stds = np.nanstd(Y, axis=1, ddof=1)
    eps = 1e-12
    with np.errstate(divide='ignore', invalid='ignore'):
        sharpe = np.where(
            stds > 0,
            means / stds * np.sqrt(n_splits),
            np.where(means > 0, means / eps * np.sqrt(n_splits), 0.0),
        )

    # Estimate null distribution of max Sharpe via bootstrap resampling
    # Resample candidate scores across splits to create null scenario
    n_bootstrap = 1000
    max_sharpe_null = []
    for _ in range(n_bootstrap):
        # Resample splits for each candidate independently under null
        Y_boot = np.column_stack([
            Y[c, rng.integers(0, n_splits, size=n_splits)] for c in range(n_candidates)
        ])
        means_b = np.nanmean(Y_boot, axis=1)
        stds_b = np.nanstd(Y_boot, axis=1, ddof=1)
        with np.errstate(divide='ignore', invalid='ignore'):
            sharpe_b = np.where(stds_b > 0, means_b / stds_b * np.sqrt(n_splits), 0.0)
        max_sharpe_null.append(np.max(sharpe_b))
    max_sharpe_null = np.array(max_sharpe_null)

    # Adjust Sharpe by the alpha quantile of max Sharpe under null
    threshold = np.quantile(max_sharpe_null, 1 - alpha)
    penalty = np.log1p(n_candidates)
    # Deflate: subtract threshold and divide by penalty
    deflated = np.maximum(0, sharpe - threshold) / penalty
    return deflated


def deflated_sharpe_dspr(Y: np.ndarray, alpha: float = 0.05, seed: int = 0, n_bootstrap: int = 1000) -> np.ndarray:
    """Compute Deflated Sharpe Ratio following Lopez de Prado (2014).

    The DSR corrects for selection bias by estimating the probability that
    the observed maximum Sharpe ratio would have occurred by chance under
    a null hypothesis of no true edge.

    Parameters
    ----------
    Y : np.ndarray
        2D array of shape (n_candidates, n_splits) containing candidate returns/scores.
    alpha : float, optional
        Significance level for the expected maximum Sharpe under null.
        Default 0.05.
    seed : int, optional
        Random seed for reproducibility. Default 0.
    n_bootstrap : int, optional
        Number of bootstrap resamples for null estimation. Default 1000.

    Returns
    -------
    np.ndarray
        Array of deflated Sharpe ratios per candidate. Only the selected
        (maximum Sharpe) candidate receives a non-zero DSR; all others are 0.

    Notes
    -----
    The implementation follows Lopez de Prado's DSR approach:

      1. Compute empirical Sharpe ratio per candidate:
         SR_i = mean_i / std_i * sqrt(n_splits)

      2. Estimate the null distribution of maximum Sharpe via bootstrap
         resampling of returns-per-split, preserving cross-candidate
         correlations (joint resampling of splits across all candidates).

      3. Compute z = (SR_max - mu_null) / sigma_null and the one-tailed
         p-value = P(Z > z) under standard normal.

      4. Return DSR_i = SR_i * (1 - p_value) for the selected (max) candidate,
         0 for all others.

    The DSR reflects the probability-weighted Sharpe you would expect if the
    selected candidate were a true edge (1 - p_value) vs a false discovery
    (p_value inflates the chance the observed max was lucky).
    """
    if Y is None:
        return np.array([])
    if not isinstance(Y, np.ndarray):
        raise TypeError("Y must be a numpy array")
    if Y.ndim != 2:
        raise ValueError("Y must be a 2D array of shape (n_candidates, n_splits)")
    n_candidates, n_splits = Y.shape
    if n_splits < 2:
        raise ValueError("n_splits must be at least 2")
    if n_candidates < 1:
        raise ValueError("n_candidates must be at least 1")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1 exclusive")

    rng = np.random.default_rng(seed)
    n_bootstrap = min(n_bootstrap, 5000)

    # Step 1: Compute Sharpe ratio per candidate
    means = np.nanmean(Y, axis=1)
    stds = np.nanstd(Y, axis=1, ddof=1)
    eps = 1e-12
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(
            stds > 0,
            means / stds * np.sqrt(n_splits),
            np.where(means > 0, means / eps * np.sqrt(n_splits), 0.0),
        )

    # Identify selected candidate (maximum Sharpe)
    observed_max = float(np.max(sharpe))
    selected_idx = int(np.argmax(sharpe))

    # Step 2: Bootstrap null distribution of max Sharpe, preserving correlations
    # Joint resampling of splits across all candidates maintains cross-candidate structure
    max_sharpe_null = []
    for _ in range(n_bootstrap):
        Y_boot = Y[:, rng.integers(0, n_splits, size=n_splits)]
        means_b = np.nanmean(Y_boot, axis=1)
        stds_b = np.nanstd(Y_boot, axis=1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            sharpe_b = np.where(stds_b > 0, means_b / stds_b * np.sqrt(n_splits), 0.0)
        max_sharpe_null.append(float(np.max(sharpe_b)))
    max_sharpe_null = np.array(max_sharpe_null)

    # Step 3: Bootstrap null moments
    mu_null = float(np.mean(max_sharpe_null))
    sigma_null = float(np.std(max_sharpe_null, ddof=1))

    # z-score and one-tailed p-value for observed max Sharpe under null
    if sigma_null > 1e-10:
        z = (observed_max - mu_null) / sigma_null
        # one-tailed: P(Z > z) via erfc approximation of normal SF
        p_value = 0.5 * _math.erfc(z / _math.sqrt(2))
    else:
        p_value = 0.5  # degenerate: max always equals mu_null under null

    # Step 4: Deflated Sharpe — only the selected candidate receives DSR
    deflated = np.zeros(n_candidates)
    if observed_max > 0:
        deflated[selected_idx] = sharpe[selected_idx] * (1 - p_value)

    return deflated
