"""Pre-earnings HypothesisSpec example loader.

Loads documented pre-earnings hypothesis fixtures by short name.
All examples live under ``examples/preearn_hypotheses/`` in the repo root.

Example
-------
>>> from engine.edge_discovery.examples import list_preearn_examples
>>> list_preearn_examples()
('basic', 'coarse')
>>> spec = load_preearn_example("basic")
>>> spec.hypothesis_id
'preearn-iv-ramp-basic-v1'
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.edge_discovery.hypotheses.spec import HypothesisSpec

# Repo-root-relative path to the examples directory.
# Path(__file__) is engine/edge_discovery/examples.py
# .resolve().parents[2] goes: examples.py -> edge_discovery -> engine -> repo root
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_EXAMPLES_DIR: Path = _REPO_ROOT / "examples" / "preearn_hypotheses"

# Explicit short-name -> filename map.  Adding a new example requires adding
# an entry here; this is intentional friction to keep the public API small.
_EXAMPLE_MAP: dict[str, str] = {
    "basic":  "basic_preearn_dpe2_delta50.json",
    "coarse": "coarse_grid_preearn.json",
}


def list_preearn_examples() -> tuple[str, ...]:
    """Return available pre-earnings example short names in sorted order."""
    return tuple(sorted(_EXAMPLE_MAP.keys()))


def preearn_example_path(name: str) -> Path:
    """Return the absolute path to a pre-earnings example JSON file.

    Parameters
    ----------
    name : str
        Short name of the example (``"basic"`` or ``"coarse"``).

    Returns
    -------
    Path
        Absolute path to the example JSON file.

    Raises
    ------
    ValueError
        If ``name`` is not a known example name.
    """
    if name not in _EXAMPLE_MAP:
        available = ", ".join(sorted(_EXAMPLE_MAP.keys()))
        raise ValueError(
            f"Unknown pre-earnings example {name!r}.  Available: {available}."
        )
    return _EXAMPLES_DIR / _EXAMPLE_MAP[name]


def load_preearn_example(name: str) -> HypothesisSpec:
    """Load a pre-earnings HypothesisSpec fixture by short name.

    Parameters
    ----------
    name : str
        Short name of the example (``"basic"`` or ``"coarse"``).

    Returns
    -------
    HypothesisSpec
        The deserialized hypothesis spec.

    Raises
    ------
    ValueError
        If ``name`` is not a known example name.
    FileNotFoundError
        If the example JSON file does not exist on disk.
    """
    path = preearn_example_path(name)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    return HypothesisSpec.from_dict(data)
