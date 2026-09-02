#!/usr/bin/env python3
"""Compatibility alias for :mod:`install_skills`.

Use ``install_skills.py`` for new automation; this name is retained because
some host setup recipes naturally call the operation a Skill sync.
"""

from __future__ import annotations

try:  # direct ``python scripts/sync_skills.py``
    from install_skills import main
except ImportError:  # ``import scripts.sync_skills`` from a test/host
    from .install_skills import main


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
