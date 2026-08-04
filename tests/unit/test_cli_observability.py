"""The CLI must actually activate the stage-event emitter.

Regression guard for a silent gap: every runner called ``emit_if_active``,
but no CLI command ever called ``set_active_emitter``, so
``stage-events.jsonl`` was never written in production and the exporter
had nothing to tail. Events only appeared in tests that built an emitter
by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from bffi_pipeline.cli import _require_run_dir
from bffi_pipeline.observability.events import get_active_emitter, set_active_emitter


@pytest.fixture(autouse=True)
def _reset_emitter() -> None:
    set_active_emitter(None)
    yield
    set_active_emitter(None)


def _run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / "20260804-1055-abcdef"
    run.mkdir(parents=True)
    return run


def test_require_run_dir_activates_the_emitter_for_that_run(tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    _require_run_dir(run / "bffi", option_label="--output-dir")
    emitter = get_active_emitter()
    assert emitter is not None
    assert emitter.sidecar_path == run / "stage-events.jsonl"
    assert emitter.run_uuid == "20260804-1055-abcdef"


def test_activation_is_idempotent_across_several_validated_paths(tmp_path: Path) -> None:
    """A command validating both ``--output-dir`` and ``--html`` must not
    swap in a second emitter mid-run."""
    run = _run_dir(tmp_path)
    _require_run_dir(run / "bffi", option_label="--output-dir")
    first = get_active_emitter()
    _require_run_dir(run / "review" / "out.html", option_label="--html")
    assert get_active_emitter() is first


def test_sidecar_can_be_disabled_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``BFFI_OBSERVABILITY_SIDECAR=none`` keeps the exporter's own process
    from writing a sidecar it would then tail."""
    monkeypatch.setenv("BFFI_OBSERVABILITY_SIDECAR", "none")
    _require_run_dir(_run_dir(tmp_path) / "bffi", option_label="--output-dir")
    assert get_active_emitter() is None


def test_non_canonical_output_dir_exits_before_activating(tmp_path: Path) -> None:
    with pytest.raises(typer.Exit):
        _require_run_dir(tmp_path / "not-a-run", option_label="--output-dir")
    assert get_active_emitter() is None
