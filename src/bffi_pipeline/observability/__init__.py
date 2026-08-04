"""Operator-facing observability surface for the bffi pipeline.

- :mod:`bffi_pipeline.observability.events` — per-stage start/end/
  skipped/failed event emission and the active-emitter registry.

Re-exports the operator-facing surface so callers can write
``from bffi_pipeline.observability import emit_if_active`` rather
than reaching into the submodule.
"""

from bffi_pipeline.observability.events import (
    StageEventEmitter,
    emit_failed,
    emit_if_active,
    emit_plan,
    emit_skipped,
    get_active_emitter,
    set_active_emitter,
)

__all__ = [
    "StageEventEmitter",
    "emit_failed",
    "emit_if_active",
    "emit_plan",
    "emit_skipped",
    "get_active_emitter",
    "set_active_emitter",
]
