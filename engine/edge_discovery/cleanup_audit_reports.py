"""Cleanup utility for audit reports.
Removes files older than max_age_days or keeps only the most recent max_files.
"""
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def cleanup(out_dir: str = "audit_reports", max_age_days: int = 30, max_files: int = 1000) -> str:
    """Remove stale or excess audit report files.

    Removes files older than max_age_days. If total remaining files still
    exceed max_files, removes the oldest by modification time.

    Parameters
    ----------
    out_dir : str
        Directory containing audit report files. Defaults to 'audit_reports'.
    max_age_days : int
        Remove files older than this many days. Defaults to 30.
    max_files : int
        Maximum number of files to retain (most recent by mtime). Defaults to 1000.

    Returns
    -------
    str
        Summary of deleted files (one-per-line). Empty string if nothing deleted.
    """
    path = Path(out_dir)
    if not path.exists():
        logger.info("Audit reports directory does not exist: %s", out_dir)
        return ""

    cutoff = datetime.now() - timedelta(days=max_age_days)
    all_files: List[Path] = []

    for f in path.iterdir():
        if f.is_file():
            all_files.append(f)

    if not all_files:
        return ""

    # Sort by mtime ascending (oldest first)
    all_files.sort(key=lambda f: f.stat().st_mtime)

    # Identify old files to delete
    old_files: List[Path] = []
    for f in all_files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime < cutoff:
            old_files.append(f)

    # Determine how many more we need to remove to get under max_files
    remaining = [f for f in all_files if f not in old_files]
    excess = len(remaining) - max_files
    if excess > 0:
        # Remove oldest of the remaining (excluding already-queued old_files)
        remaining.sort(key=lambda f: f.stat().st_mtime)
        old_files.extend(remaining[:excess])

    if not old_files:
        logger.info("No cleanup needed for %s", out_dir)
        return ""

    # Remove files and build a simple summary
    summary_lines: List[str] = []
    for f in sorted(old_files, key=lambda f: f.stat().st_mtime):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            mtime = "unknown"
        try:
            os.remove(f)
            logger.debug("Removed: %s", f)
            summary_lines.append(f"REMOVED {f.name} @{mtime}")
        except OSError as e:
            logger.warning("Failed to remove %s: %s", f, e)

    logger.info("Cleaned up %d file(s) from %s", len(summary_lines), out_dir)
    return "\n".join(summary_lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    result = cleanup()
    if result:
        print(result)
