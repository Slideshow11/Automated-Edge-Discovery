"""Tests for auditor.py RNG isolation.

Verifies that deflated_sharpe_with_options does not mutate NumPy's global
RNG state, and that repeated calls with the same seed return the same result.
"""
import importlib
import threading
import numpy as np
import pytest

auditor = importlib.import_module('engine.edge_discovery.auditor')


def test_deflated_sharpe_global_rng_unchanged_after_lopez_call():
    """Global NumPy RNG stream is unchanged after a LOPEZ call.

    Demonstrates that the function is safe to call concurrently with other
    code that relies on NumPy's global RNG.
    """
    # Use an isolated RNG to create Y so the test itself does not consume
    # global RNG state (avoids np.random.randn which advances global RNG).
    Y = np.array([[1.0, 2.0, 3.0, 4.0],
                   [2.0, 3.0, 4.0, 5.0],
                   [0.5, 1.5, 2.5, 3.5]])

    # Save the exact global RNG state, then generate expected values.
    np.random.seed(98765)
    expected_after_seed = np.random.random(5).copy()

    # Reset to the exact same state and call the function under test.
    np.random.seed(98765)

    # Call the function — it must not alter the global RNG.
    result = auditor.deflated_sharpe_with_options(Y, method="lopez")

    # Capture the next 5 random numbers from the global stream.
    actual_after = np.random.random(5)

    # The two sequences must be identical: the call did not advance global RNG.
    np.testing.assert_array_equal(
        actual_after,
        expected_after_seed,
        err_msg="Global NumPy RNG was mutated by deflated_sharpe_with_options(LOPEZ)",
    )


def test_deflated_sharpe_global_rng_unchanged_after_bootstrap_call():
    """Global NumPy RNG stream is unchanged after a bootstrap call."""
    Y = np.array([[1.0, 2.0, 3.0, 4.0],
                   [2.0, 3.0, 4.0, 5.0],
                   [0.5, 1.5, 2.5, 3.5]])

    np.random.seed(98765)
    expected_after_seed = np.random.random(5).copy()

    np.random.seed(98765)

    result = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=100, seed=42
    )

    actual_after = np.random.random(5)

    np.testing.assert_array_equal(
        actual_after,
        expected_after_seed,
        err_msg="Global NumPy RNG was mutated by deflated_sharpe_with_options(bootstrap)",
    )


def test_deflated_sharpe_repeated_calls_with_same_seed_return_same_result():
    """Repeated calls with the same inputs and seed produce identical output."""
    Y = np.array([[1.0, 2.0, 3.0, 4.0],
                   [2.0, 3.0, 4.0, 5.0],
                   [0.5, 1.5, 2.5, 3.5]])

    result1 = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=50, seed=12345
    )
    result2 = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=50, seed=12345
    )

    np.testing.assert_array_equal(
        result1,
        result2,
        err_msg="Repeated calls with same seed returned different results",
    )


def test_deflated_sharpe_different_seeds_return_different_results():
    """Different seeds produce different bootstrap result distributions."""
    Y = np.array([[1.0, 2.0, 3.0, 4.0],
                   [2.0, 3.0, 4.0, 5.0],
                   [0.5, 1.5, 2.5, 3.5]])

    result1 = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=50, seed=11111
    )
    result2 = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=50, seed=22222
    )

    # With 50 iterations, distributions should differ.
    # We check the mean is unlikely to be identical by chance.
    assert abs(np.mean(result1) - np.mean(result2)) > 1e-6, (
        "Different seeds produced near-identical distributions"
    )


def test_deflated_sharpe_deterministic_seed_none_still_uses_isolated_rng():
    """When deterministic_seed=None, global RNG is still not mutated."""
    Y = np.array([[1.0, 2.0, 3.0, 4.0],
                   [2.0, 3.0, 4.0, 5.0],
                   [0.5, 1.5, 2.5, 3.5]])

    np.random.seed(54321)
    expected_after_seed = np.random.random(5).copy()

    np.random.seed(54321)

    result = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=20, seed=99999, deterministic_seed=None
    )

    actual_after = np.random.random(5)

    np.testing.assert_array_equal(
        actual_after,
        expected_after_seed,
        err_msg="Global NumPy RNG was mutated even with deterministic_seed=None",
    )


def test_deflated_sharpe_lopez_returns_correct_type():
    """LOPEZ method returns an ndarray (delegation to pbo_module.deflated_sharpe)."""
    Y = np.array([[1.0, 2.0, 3.0, 4.0],
                   [2.0, 3.0, 4.0, 5.0],
                   [0.5, 1.5, 2.5, 3.5]])
    result = auditor.deflated_sharpe_with_options(Y, method="lopez")
    assert isinstance(result, np.ndarray)


def test_deflated_sharpe_bootstrap_returns_correct_shape():
    """Bootstrap method returns n_iter results."""
    Y = np.array([[1.0, 2.0, 3.0, 4.0],
                   [2.0, 3.0, 4.0, 5.0],
                   [0.5, 1.5, 2.5, 3.5]])
    n_iter = 100
    result = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=n_iter, seed=42
    )
    assert isinstance(result, np.ndarray)
    assert result.shape == (n_iter,)


def test_deflated_sharpe_no_global_np_random_get_set_seed():
    """Static check: auditor.py source contains no np.random.get/set_state/seed."""
    import ast
    import inspect

    source = inspect.getsource(auditor.deflated_sharpe_with_options)

    # Parse the source to check for prohibited calls.
    tree = ast.parse(source)

    prohibited = {"get_state", "set_state", "seed"}
    found = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "np"
                and node.value.attr == "random"
                and node.attr in prohibited
            ):
                found.add(node.attr)

    assert not found, (
        f"deflated_sharpe_with_options contains prohibited np.random calls: {found}"
    )


def test_concurrent_calls_do_not_interfere():
    """Two simultaneous calls produce correct independent results.

    This is a basic smoke test for thread-safety of the isolated RNG approach.
    We call deflated_sharpe_with_options concurrently and verify each call
    returns its own deterministic result, and neither corrupts the other's output.
    """
    Y = np.array([[1.0, 2.0, 3.0, 4.0],
                   [2.0, 3.0, 4.0, 5.0],
                   [0.5, 1.5, 2.5, 3.5]])
    n_iter = 30
    seed_a = 11111
    seed_b = 22222

    result_a_ref = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=n_iter, seed=seed_a
    )
    result_b_ref = auditor.deflated_sharpe_with_options(
        Y, method="bootstrap", n_iter=n_iter, seed=seed_b
    )

    results_concurrent = [None, None]
    errors = [None, None]

    def worker(idx, seed):
        try:
            results_concurrent[idx] = auditor.deflated_sharpe_with_options(
                Y, method="bootstrap", n_iter=n_iter, seed=seed
            )
        except Exception as e:
            errors[idx] = e

    t1 = threading.Thread(target=worker, args=(0, seed_a))
    t2 = threading.Thread(target=worker, args=(1, seed_b))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors[0] is None, f"Thread 1 raised: {errors[0]}"
    assert errors[1] is None, f"Thread 2 raised: {errors[1]}"

    # Concurrent results must match sequential reference results.
    np.testing.assert_array_equal(
        results_concurrent[0],
        result_a_ref,
        err_msg="Concurrent call A produced different result than sequential",
    )
    np.testing.assert_array_equal(
        results_concurrent[1],
        result_b_ref,
        err_msg="Concurrent call B produced different result than sequential",
    )
