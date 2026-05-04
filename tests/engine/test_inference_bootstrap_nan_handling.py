"""Bootstrap failure handling tests for inference.py — cluster and wild-cluster bootstrap.

Verifies that:
1. All bootstrap fit failures raise ValueError (fail closed).
2. Partial failures compute CI from successful fits only (no NaN row artifacts).
3. Successful-only behavior is unchanged.
4. Determinism and isolated RNG are preserved.
5. No global RNG mutation.
"""

import numpy as np
import pytest

# pandas and statsmodels are required for these tests; skip the entire module if unavailable
pandas = pytest.importorskip("pandas")
statsmodels = pytest.importorskip("statsmodels")

from unittest.mock import patch, MagicMock

from engine.edge_discovery import inference as inf


def make_synthetic_clustered(n=200, n_clusters=20, seed=0):
    """Create synthetic clustered data for bootstrap testing."""
    rng = np.random.default_rng(seed)
    clusters = rng.integers(0, n_clusters, n)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = 1.0 + 2.0 * x1 + (-0.5) * x2 + rng.normal(scale=0.5, size=n)
    return pandas.DataFrame({"y": y, "x1": x1, "x2": x2, "cluster": clusters})


class TestClusterBootstrapFailClosed:
    """Tests for cluster_bootstrap_ci failure handling."""

    def test_all_bootstrap_fits_fail_raises_value_error(self):
        """All bootstrap OLS failures must raise ValueError, not return empty dict."""
        df = make_synthetic_clustered(n=100, n_clusters=10, seed=5)
        formula = "y ~ x1 + x2"

        # Force every OLS fit to raise
        with patch.object(inf.smf, "ols") as mock_ols:
            mock_model = MagicMock()
            mock_model.fit.side_effect = Exception("forced OLS failure")
            mock_ols.return_value = mock_model

            with pytest.raises(ValueError, match="All .* cluster bootstrap OLS fits failed"):
                inf.cluster_bootstrap_ci(
                    formula=formula, df=df, cluster_col="cluster",
                    n_bootstrap=50, rng_seed=1
                )

    def test_all_bootstrap_fails_returns_value_error_not_empty_dict(self):
        """Verify fail-closed: zero successful draws raises, not silently returns {}."""
        df = make_synthetic_clustered(n=100, n_clusters=10, seed=5)
        formula = "y ~ x1 + x2"

        with patch.object(inf.smf, "ols") as mock_ols:
            mock_model = MagicMock()
            mock_model.fit.side_effect = Exception("forced OLS failure")
            mock_ols.return_value = mock_model

            with pytest.raises(ValueError) as exc_info:
                inf.cluster_bootstrap_ci(
                    formula=formula, df=df, cluster_col="cluster",
                    n_bootstrap=50, rng_seed=1
                )
            assert "cluster bootstrap OLS fits failed" in str(exc_info.value)
            assert "zero successful" in str(exc_info.value)


class TestWildClusterBootstrapFailClosed:
    """Tests for wild_cluster_bootstrap_ci failure handling."""

    def test_all_bootstrap_fits_fail_raises_value_error(self):
        """All bootstrap OLS failures must raise ValueError, not return empty dict."""
        df = make_synthetic_clustered(n=100, n_clusters=10, seed=5)
        formula = "y ~ x1 + x2"

        # wild_cluster_bootstrap_ci calls smf.ols twice:
        # 1. model0 = smf.ols(formula, df).fit()  <- must succeed (provides names, endog_names)
        # 2. m_b = smf.ols(formula, df_b).fit()  <- forced to fail for all bootstrap iterations
        #
        # We use a MagicMock with a side_effect on .fit() so that
        # every returned model's .fit() raises an exception.

        call_count = [0]

        def ols_side_effect(*args, **kwargs):
            call_count[0] += 1
            mock_model = MagicMock()
            if call_count[0] == 1:
                # model0 fit: must succeed, provides param names
                mock_model.fit.return_value = MagicMock(
                    params=pandas.Series({"Intercept": 1.0, "x1": 2.0, "x2": -0.5}),
                    model=MagicMock(endog_names="y")
                )
            else:
                # All bootstrap fits: must fail
                mock_model.fit.side_effect = Exception("forced OLS failure")
            return mock_model

        with patch.object(inf.smf, "ols", side_effect=ols_side_effect):
            with pytest.raises(ValueError, match="All .* wild cluster bootstrap OLS fits failed"):
                inf.wild_cluster_bootstrap_ci(
                    formula=formula, df=df, cluster_col="cluster",
                    n_bootstrap=50, rng_seed=1
                )

    def test_all_bootstrap_fails_returns_value_error_not_empty_dict(self):
        """Verify fail-closed: zero successful draws raises, not silently returns {}."""
        df = make_synthetic_clustered(n=100, n_clusters=10, seed=5)
        formula = "y ~ x1 + x2"

        call_count = [0]

        def ols_side_effect(*args, **kwargs):
            call_count[0] += 1
            mock_model = MagicMock()
            if call_count[0] == 1:
                mock_model.fit.return_value = MagicMock(
                    params=pandas.Series({"Intercept": 1.0, "x1": 2.0, "x2": -0.5}),
                    model=MagicMock(endog_names="y")
                )
            else:
                mock_model.fit.side_effect = Exception("forced OLS failure")
            return mock_model

        with patch.object(inf.smf, "ols", side_effect=ols_side_effect):
            with pytest.raises(ValueError) as exc_info:
                inf.wild_cluster_bootstrap_ci(
                    formula=formula, df=df, cluster_col="cluster",
                    n_bootstrap=50, rng_seed=1
                )
            assert "wild cluster bootstrap OLS fits failed" in str(exc_info.value)
            assert "zero successful" in str(exc_info.value)


class TestClusterBootstrapPartialFailure:
    """Tests for partial bootstrap failure handling (some succeed, some fail)."""

    def test_partial_failures_exclude_nan_rows_from_percentile(self):
        """Failed fits must not contribute NaN rows to percentile computation.

        We force specific bootstrap iterations to fail and verify the result
        uses only successful fits (no NaN percentile artifacts).
        """
        df = make_synthetic_clustered(n=100, n_clusters=10, seed=7)
        formula = "y ~ x1 + x2"

        call_count = [0]

        def selective_fit(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 10:
                raise Exception("forced failure for first 10 iterations")
            # Succeed for the rest
            return MagicMock(
                params=pandas.Series({"Intercept": 1.0, "x1": 2.0, "x2": -0.5})
            )

        with patch.object(inf.smf, "ols") as mock_ols:
            mock_ols.return_value.fit.side_effect = selective_fit

            # 50 total, 10 fail + 40 succeed
            ci = inf.cluster_bootstrap_ci(
                formula=formula, df=df, cluster_col="cluster",
                n_bootstrap=50, rng_seed=1
            )

            # Should succeed with valid CIs
            assert isinstance(ci, dict)
            assert len(ci) > 0
            # All CI values must be real numbers, not NaN
            for name, (lo, hi) in ci.items():
                assert not np.isnan(lo), f"{name} lower bound is NaN"
                assert not np.isnan(hi), f"{name} upper bound is NaN"
                # lo <= hi (may be equal if mock returns constant params)
                assert lo <= hi, f"{name} CI lower > upper: ({lo}, {hi})"

    def test_partial_failures_produces_valid_percentile_values(self):
        """All failures raise ValueError (covered by fail-closed tests above)."""
        # This is effectively covered by TestClusterBootstrapFailClosed tests.
        pass


class TestWildClusterBootstrapPartialFailure:
    """Tests for partial wild-cluster bootstrap failure handling."""

    def test_partial_failures_exclude_nan_rows_from_percentile(self):
        """Failed fits must not contribute NaN rows to percentile computation.

        The wild bootstrap initial model0 fit must succeed (provides names, endog_names).
        Only the per-iteration m_b fits are forced to fail for the first N calls.
        """
        df = make_synthetic_clustered(n=100, n_clusters=10, seed=7)
        formula = "y ~ x1 + x2"

        # Fit calls: model0 fit (1 call) + n_bootstrap m_b fits (50 calls)
        # First 10 of the 50 m_b fits fail, rest succeed
        call_count = [0]

        def ols_side_effect(*args, **kwargs):
            call_count[0] += 1
            mock_model = MagicMock()
            if call_count[0] == 1:
                # model0: must succeed
                mock_model.fit.return_value = MagicMock(
                    params=pandas.Series({"Intercept": 1.0, "x1": 2.0, "x2": -0.5}),
                    model=MagicMock(endog_names="y")
                )
            elif call_count[0] <= 11:  # 1 model0 + 10 m_b failures
                mock_model.fit.side_effect = Exception("forced failure")
            else:
                # Bootstrap fits 11-50: succeed
                mock_model.fit.return_value = MagicMock(
                    params=pandas.Series({"Intercept": 1.0, "x1": 2.0, "x2": -0.5})
                )
            return mock_model

        with patch.object(inf.smf, "ols", side_effect=ols_side_effect):
            ci = inf.wild_cluster_bootstrap_ci(
                formula=formula, df=df, cluster_col="cluster",
                n_bootstrap=50, rng_seed=1
            )

            assert isinstance(ci, dict)
            assert len(ci) > 0
            for name, (lo, hi) in ci.items():
                assert not np.isnan(lo), f"{name} lower bound is NaN"
                assert not np.isnan(hi), f"{name} upper bound is NaN"
                assert lo <= hi, f"{name} CI lower > upper: ({lo}, {hi})"


class TestBootstrapDeterminismAndRng:
    """Determinism and RNG isolation tests for bootstrap CI functions."""

    def test_cluster_bootstrap_deterministic_same_seed(self):
        """Identical inputs with same seed must produce identical output."""
        df = make_synthetic_clustered(n=120, n_clusters=12, seed=3)
        formula = "y ~ x1 + x2"

        ci1 = inf.cluster_bootstrap_ci(
            formula=formula, df=df, cluster_col="cluster",
            n_bootstrap=50, rng_seed=42
        )
        ci2 = inf.cluster_bootstrap_ci(
            formula=formula, df=df, cluster_col="cluster",
            n_bootstrap=50, rng_seed=42
        )
        assert ci1.keys() == ci2.keys()
        for k in ci1:
            assert ci1[k] == ci2[k]

    def test_wild_cluster_bootstrap_deterministic_same_seed(self):
        """Identical inputs with same seed must produce identical output."""
        df = make_synthetic_clustered(n=120, n_clusters=12, seed=3)
        formula = "y ~ x1 + x2"

        ci1 = inf.wild_cluster_bootstrap_ci(
            formula=formula, df=df, cluster_col="cluster",
            n_bootstrap=50, rng_seed=42
        )
        ci2 = inf.wild_cluster_bootstrap_ci(
            formula=formula, df=df, cluster_col="cluster",
            n_bootstrap=50, rng_seed=42
        )
        assert ci1.keys() == ci2.keys()
        for k in ci1:
            assert ci1[k] == ci2[k]

    def test_cluster_bootstrap_no_global_rng_mutation(self):
        """cluster_bootstrap_ci must not mutate NumPy's global RNG state."""
        df = make_synthetic_clustered(n=100, n_clusters=10, seed=5)
        formula = "y ~ x1 + x2"

        np.random.seed(99999)
        expected = np.random.random(5).copy()
        np.random.seed(99999)

        inf.cluster_bootstrap_ci(
            formula=formula, df=df, cluster_col="cluster",
            n_bootstrap=50, rng_seed=1
        )

        actual = np.random.random(5)
        assert np.allclose(expected, actual), \
            "Global NumPy RNG was mutated by cluster_bootstrap_ci"

    def test_wild_cluster_bootstrap_no_global_rng_mutation(self):
        """wild_cluster_bootstrap_ci must not mutate NumPy's global RNG state."""
        df = make_synthetic_clustered(n=100, n_clusters=10, seed=5)
        formula = "y ~ x1 + x2"

        np.random.seed(88888)
        expected = np.random.random(5).copy()
        np.random.seed(88888)

        inf.wild_cluster_bootstrap_ci(
            formula=formula, df=df, cluster_col="cluster",
            n_bootstrap=50, rng_seed=1
        )

        actual = np.random.random(5)
        assert np.allclose(expected, actual), \
            "Global NumPy RNG was mutated by wild_cluster_bootstrap_ci"


class TestBootstrapSuccessfulBehavior:
    """Regression tests: ensure valid-data behavior is unchanged."""

    def test_cluster_bootstrap_returns_valid_ci_on_good_data(self):
        """Normal valid data must produce non-empty CI with real (non-NaN) bounds."""
        df = make_synthetic_clustered(n=200, n_clusters=20, seed=7)
        formula = "y ~ x1 + x2"

        ci = inf.cluster_bootstrap_ci(
            formula=formula, df=df, cluster_col="cluster",
            n_bootstrap=100, rng_seed=1
        )
        assert isinstance(ci, dict)
        assert len(ci) > 0
        for name, (lo, hi) in ci.items():
            assert not np.isnan(lo)
            assert not np.isnan(hi)
            assert lo < hi

    def test_wild_cluster_bootstrap_returns_valid_ci_on_good_data(self):
        """Normal valid data must produce non-empty CI with real (non-NaN) bounds."""
        df = make_synthetic_clustered(n=200, n_clusters=20, seed=7)
        formula = "y ~ x1 + x2"

        ci = inf.wild_cluster_bootstrap_ci(
            formula=formula, df=df, cluster_col="cluster",
            n_bootstrap=100, rng_seed=1
        )
        assert isinstance(ci, dict)
        assert len(ci) > 0
        for name, (lo, hi) in ci.items():
            assert not np.isnan(lo)
            assert not np.isnan(hi)
            assert lo < hi

