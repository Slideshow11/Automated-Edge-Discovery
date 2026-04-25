"""Configuration constants for edge discovery engine."""

import os
from typing import Any

# Enable audit logging for strategy evaluations.
AUDIT_ENABLED = True

# Default PBO (probability of backtest overfitting) threshold.
PBO_THRESHOLD_DEFAULT = 0.05

# Default minimum Sharpe ratio required for strategy acceptance.
SHARPE_MIN_DEFAULT = 1.0

# Behavior when audit fails: 'log' to log and continue, 'block' to raise.
AUDIT_ON_FAIL = 'log'  # options: 'log', 'block'

# Default bootstrap parameters.
BOOTSTRAP_N_DEFAULT = 1000
BOOTSTRAP_SEED_DEFAULT = 42

# Default deflation method for PBO computation.
DEFLATION_METHOD_DEFAULT = 'cv'

# Default audit output directory.
AUDIT_OUTDIR_DEFAULT = 'audit_reports'


def get_config(prefix: str = 'EDGE_DISCOVERY') -> dict[str, Any]:
    """Return configuration dict merged from defaults and environment overrides.

    Environment variables are read with names ``<prefix>_<NAME>`` where the suffix
    matches the key below. Type coercion is applied automatically:
    bool, float, int, or str.

    Args:
        prefix: Upper-case prefix for environment variable names (default: ``EDGE_DISCOVERY``).

    Returns:
        Dict with keys: ``audit_enabled``, ``pbo_threshold``, ``sharpe_min``,
        ``audit_on_fail``, ``bootstrap_n``, ``bootstrap_seed``,
        ``deflation_method``, ``audit_outdir``.

    Examples:
        >>> # Use defaults
        >>> cfg = get_config()

        >>> # Override via environment
        >>> # EDGE_DISCOVERY_AUDIT_ENABLED=false EDGE_DISCOVERY_PBO_THRESHOLD=0.01
        >>> # cfg = get_config()  # audit_enabled=False, pbo_threshold=0.01

        >>> # Custom prefix
        >>> # MY_AUDIT_PBO_THRESHOLD=0.1
        >>> # cfg = get_config(prefix='MY_AUDIT')  # pbo_threshold=0.1
    """
    defaults: dict[str, Any] = {
        'audit_enabled': AUDIT_ENABLED,
        'pbo_threshold': PBO_THRESHOLD_DEFAULT,
        'sharpe_min': SHARPE_MIN_DEFAULT,
        'audit_on_fail': AUDIT_ON_FAIL,
        'bootstrap_n': BOOTSTRAP_N_DEFAULT,
        'bootstrap_seed': BOOTSTRAP_SEED_DEFAULT,
        'deflation_method': DEFLATION_METHOD_DEFAULT,
        'audit_outdir': AUDIT_OUTDIR_DEFAULT,
    }

    env_mappings: dict[str, tuple[str, type]] = {
        'audit_enabled': ('AUDIT_ENABLED', bool),
        'pbo_threshold': ('PBO_THRESHOLD', float),
        'sharpe_min': ('SHARPE_MIN', float),
        'audit_on_fail': ('AUDIT_ON_FAIL', str),
        'bootstrap_n': ('BOOTSTRAP_N', int),
        'bootstrap_seed': ('BOOTSTRAP_SEED', int),
        'deflation_method': ('DEFLATION_METHOD', str),
        'audit_outdir': ('AUDIT_OUTDIR', str),
    }

    result = dict(defaults)
    for key, (env_suffix, type_fn) in env_mappings.items():
        env_name = f'{prefix}_{env_suffix}'
        env_value = os.environ.get(env_name)
        if env_value is not None:
            if type_fn is bool:
                result[key] = env_value.lower() in ('true', '1', 'yes')
            else:
                result[key] = type_fn(env_value)
    return result
