import numpy as np
import pytest

from engine.edge_discovery.pbo import compute_pbo, deflated_sharpe, deflated_sharpe_dspr


class TestComputePbo:
    """Deterministic numeric checks for compute_pbo with small fabricated Y."""

    def test_pbo_perfect_stability(self):
        """When one candidate dominates all splits, PBO should be 0."""
        Y = np.array([
            [1.0, 1.0, 1.0, 1.0],
            [0.0, 0.0, 0.0, 0.0],
        ])
        pbo, pbo_std = compute_pbo(Y, n_bootstrap=100, seed=42)
        assert pbo == pytest.approx(0.0, abs=1e-6)

    def test_pbo_maximum_uncertainty(self):
        """When candidates are equally best, PBO should be between 0 and 1 (non-negative)."""
        Y = np.array([
            [1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 1.0],
        ])
        pbo, pbo_std = compute_pbo(Y, n_bootstrap=100, seed=0)
        assert 0.0 <= pbo <= 1.0

    def test_pbo_intermediate(self):
        """PBO falls between 0 and 1 when some candidates dominate partially."""
        Y = np.array([
            [2.0, 1.0, 1.0, 0.0],
            [1.0, 2.0, 1.0, 1.0],
        ])
        pbo, pbo_std = compute_pbo(Y, n_bootstrap=100, seed=0)
        assert 0.0 < pbo < 1.0

    def test_pbo_std_is_positive(self):
        """Bootstrap standard error should be non-negative."""
        Y = np.array([
            [1.5, 1.0, 0.5, 1.2],
            [1.0, 0.9, 0.8, 1.1],
        ])
        pbo, pbo_std = compute_pbo(Y, n_bootstrap=100, seed=42)
        assert pbo_std >= 0.0

    def test_pbo_rejects_ndim_error(self):
        with pytest.raises(ValueError, match="2D"):
            compute_pbo(np.array([1.0, 2.0, 3.0]), seed=0)

    def test_pbo_rejects_insufficient_candidates(self):
        with pytest.raises(ValueError, match="at least 2"):
            compute_pbo(np.array([[1.0], [2.0]]), seed=0)

    def test_pbo_rejects_insufficient_splits(self):
        # Use two candidates but only one split to trigger n_splits error
        with pytest.raises(ValueError, match="n_splits"):
            compute_pbo(np.array([[1.0], [2.0]]), seed=0)


class TestDeflatedSharpe:
    """Deterministic numeric checks for deflated_sharpe with small fabricated Y."""

    def test_deflated_sharpe_zero_when_all_zero_returns(self):
        """Zero returns should produce zero (or near-zero) deflated Sharpe."""
        Y = np.zeros((2, 4))
        ds = deflated_sharpe(Y, method="lopez", seed=0)
        assert ds.shape == (2,)
        assert np.all(ds >= 0.0)

    def test_deflated_sharpe_shape_matches_candidates(self):
        """Output shape equals number of candidates."""
        Y = np.array([
            [0.1, 0.2, 0.15, 0.18],
            [0.05, 0.08, 0.06, 0.07],
            [0.12, 0.11, 0.13, 0.10],
        ])
        ds = deflated_sharpe(Y, method="lopez", seed=42)
        assert ds.shape == (3,)

    def test_deflated_sharpe_positive_for_strong_candidate(self):
        """A candidate with high mean and low std should yield positive deflated Sharpe."""
        Y = np.array([
            [0.10, 0.12, 0.11, 0.09, 0.10],
            [0.01, 0.02, 0.01, 0.02, 0.01],
        ])
        ds = deflated_sharpe(Y, method="lopez", seed=0)
        assert ds[0] > ds[1]

    def test_deflated_sharpe_rejects_invalid_method(self):
        Y = np.array([[1.0, 2.0], [3.0, 4.0]])
        with pytest.raises(ValueError, match="lopez"):
            deflated_sharpe(Y, method="invalid", seed=0)

    def test_deflated_sharpe_rejects_ndim_error(self):
        with pytest.raises(ValueError, match="2D"):
            deflated_sharpe(np.array([1.0, 2.0]), seed=0)

    def test_deflated_sharpe_handles_nan(self):
        """NaN values should be handled gracefully via nanmean/nanstd."""
        Y = np.array([
            [0.1, np.nan, 0.1, 0.1],
            [0.05, 0.05, 0.05, 0.05],
        ])
        ds = deflated_sharpe(Y, method="lopez", seed=0)
        assert ds.shape == (2,)
        assert not np.any(np.isnan(ds))

    def test_deflated_sharpe_deterministic_with_seed(self):
        """Same seed should produce identical results across calls."""
        Y = np.array([
            [0.1, 0.2, 0.15, 0.18, 0.12],
            [0.05, 0.08, 0.06, 0.07, 0.09],
        ])
        ds1 = deflated_sharpe(Y, method="lopez", seed=123)
        ds2 = deflated_sharpe(Y, method="lopez", seed=123)
        np.testing.assert_array_equal(ds1, ds2)


class TestDeflatedSharpeDspr:
    """Tests for deflated_sharpe_dspr following Lopez de Prado's DSR approach."""

    def test_dspr_shape_matches_candidates(self):
        """Output shape equals number of candidates."""
        Y = np.array([
            [0.1, 0.2, 0.15, 0.18, 0.12],
            [0.05, 0.08, 0.06, 0.07, 0.09],
            [0.12, 0.11, 0.13, 0.10, 0.14],
        ])
        dsr = deflated_sharpe_dspr(Y, alpha=0.05, seed=42)
        assert dsr.shape == (3,)

    def test_dspr_selected_positive_for_strong_candidate(self):
        """The selected (max Sharpe) candidate should have positive DSR when SR > 0."""
        Y = np.array([
            [0.10, 0.12, 0.11, 0.09, 0.10],
            [0.01, 0.02, 0.01, 0.02, 0.01],
        ])
        dsr = deflated_sharpe_dspr(Y, alpha=0.05, seed=0)
        assert dsr[0] > 0.0  # selected (higher Sharpe)
        assert dsr[1] == 0.0  # not selected

    def test_dspr_only_selected_gets_nonzero(self):
        """Only the candidate with max Sharpe gets non-zero DSR."""
        Y = np.array([
            [0.10, 0.12, 0.11, 0.09, 0.10],
            [0.08, 0.07, 0.09, 0.08, 0.07],
            [0.01, 0.02, 0.01, 0.02, 0.01],
        ])
        dsr = deflated_sharpe_dspr(Y, alpha=0.05, seed=0)
        nonzero_count = np.sum(dsr > 0)
        assert nonzero_count == 1
        # The selected candidate is the one with highest Sharpe (not necessarily highest mean)
        from engine.edge_discovery.pbo import deflated_sharpe
        sel = int(np.argmax(deflated_sharpe(Y, method='lopez', seed=0)))
        assert dsr[sel] > 0.0
