"""JSONL-backed registry for HypothesisSpec records.

Provides persistent storage for hypothesis records.  Each hypothesis is
stored as one JSON line.  Repeated hypothesis_id entries are allowed; the
latest record (by position in the file) is the authoritative state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .. import config as ed_config
from .._file_lock import exclusive_file_lock
from .spec import HypothesisSpec


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

# Minimal transition map: HypothesisStatus → set of allowed next statuses.
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"registered"},
    "registered": {"testing", "rejected", "killed"},
    "testing": {"accepted", "rejected", "killed"},
    "accepted": set(),
    "rejected": set(),
    "killed": set(),
}


def _check_transition(current: str, new: str) -> None:
    """Raise ValueError if the transition is not allowed."""
    allowed = _VALID_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise ValueError(
            f"Invalid status transition: {current!r} -> {new!r}. "
            f"Allowed from {current!r}: {sorted(allowed) or 'none'}."
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class HypothesisRegistry:
    """JSONL-backed hypothesis registry.

    Parameters
    ----------
    path : str | Path
        Path to the JSONL registry file.  Parent directories are created
        on first write if they do not exist.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = ed_config.get_config()["hypothesis_registry_path"]
        self._path = Path(path)

    # -------------------------------------------------------------------------
    # Read
    # -------------------------------------------------------------------------

    def read_all(self) -> list[HypothesisSpec]:
        """Return all HypothesisSpec records in the registry.

        Records with duplicate ``hypothesis_id`` appear in the returned list
        in file order.  Callers that need the authoritative latest state
        should use :meth:`get` instead.
        """
        if not self._path.exists():
            return []

        records: list[HypothesisSpec] = []
        with self._path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped:
                    continue  # skip blank lines silently
                try:
                    records.append(HypothesisSpec.from_dict(json.loads(stripped)))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Malformed JSON at line {line_no}: {exc}"
                    ) from exc
        return records

    def get(self, hypothesis_id: str) -> HypothesisSpec | None:
        """Return the latest HypothesisSpec with ``hypothesis_id``, or None."""
        latest: HypothesisSpec | None = None
        for hyp in self.read_all():
            if hyp.hypothesis_id == hypothesis_id:
                latest = hyp
        return latest

    # -------------------------------------------------------------------------
    # Write
    # -------------------------------------------------------------------------

    def _write_all(self, records: list[HypothesisSpec]) -> None:
        """Rewrite the entire registry with the given records."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            for hyp in records:
                fh.write(json.dumps(hyp.to_dict(), sort_keys=True, ensure_ascii=True) + "\n")

    def register(self, hypothesis: HypothesisSpec) -> None:
        """Append a new hypothesis to the registry.

        Raises
        ------
        ValueError
            If a hypothesis with the same ``hypothesis_id`` already exists.
        """
        existing = self.get(hypothesis.hypothesis_id)
        if existing is not None:
            raise ValueError(
                f"Hypothesis {hypothesis.hypothesis_id!r} is already registered. "
                f"Use update_status() to change its status."
            )

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            with exclusive_file_lock(fh):
                fh.write(json.dumps(hypothesis.to_dict(), sort_keys=True, ensure_ascii=True) + "\n")

    def update_status(
        self,
        hypothesis_id: str,
        new_status: str,
        notes: str | None = None,
    ) -> HypothesisSpec:
        """Update the status of a hypothesis.

        The existing record is kept; a new record with the updated status is
        appended so the file is append-only.  The latest record in the file
        is the authoritative state.

        Parameters
        ----------
        hypothesis_id
            ID of the hypothesis to update.
        new_status
            The target status string.
        notes
            Optional notes to append to the hypothesis ``notes`` field.

        Returns
        -------
        HypothesisSpec
            The updated hypothesis spec.

        Raises
        ------
        ValueError
            If the hypothesis_id is not found, or if the status transition
            is invalid.
        """
        current = self.get(hypothesis_id)
        if current is None:
            raise ValueError(f"Hypothesis {hypothesis_id!r} not found in registry.")

        _check_transition(current.status.value, new_status)

        # Build updated spec (frozen so copy via from_dict)
        updated_dict = current.to_dict()
        updated_dict["status"] = new_status
        if notes is not None:
            existing_notes = current.notes or ""
            updated_dict["notes"] = (existing_notes + "\n" + notes).strip()

        updated = HypothesisSpec.from_dict(updated_dict)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            with exclusive_file_lock(fh):
                fh.write(json.dumps(updated.to_dict(), sort_keys=True, ensure_ascii=True) + "\n")

        return updated
