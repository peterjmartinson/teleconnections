"""Service orchestration layer for global indices and location climatologies."""
from __future__ import annotations

from .indices import bootstrap_global_indices
from .locations import bootstrap_location, slugify_location_id

__all__ = [
    "bootstrap_global_indices",
    "bootstrap_location",
    "slugify_location_id",
]
