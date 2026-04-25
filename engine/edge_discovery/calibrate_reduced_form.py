"""Legacy reduced-form calibrator placeholder.

This module previously contained a legacy reduced-form calibrator. It is
maintained as a lightweight placeholder for historical reference and should
NOT be used as the canonical implementation. The current canon is
`engine.edge_discovery.calibrate_costs` and `engine.edge_discovery.calibrate_ac_v2`.

This file is intentionally minimal and syntactically valid to avoid tooling
errors (coverage parsing, imports) while preserving a short historical
explanation.
"""

# Minimal public API kept for backward compatibility during refactors.

def legacy_calibrate(*args, **kwargs):
    """Placeholder function that signals the functionality has moved.

    Calling this function will raise NotImplementedError to encourage users to
    migrate to the newer APIs.
    """
    raise NotImplementedError("This legacy calibrator was removed; use calibrate_ac_v2 or calibrate_costs instead")
