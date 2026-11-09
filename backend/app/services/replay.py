"""Rebuild the log event stream of a finished run from what was persisted.

Used when the in-memory :class:`RunEventBus` no longer holds a run's history
(for example after a backend restart). The shape of each event matches what
the executor publishes live, so the frontend treats both the same way.
"""

from __future__ import annotations

from typing import Any

from app.models import Run, StepRun


def _ts(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def replay_events(run: Run, step_runs: list[StepRun]) -> list[dict[str, Any]]:
    """Return the events of ``run`` in chronological order."""
    events: list[dict[str, Any]] = [
        {
            "event": "run_status",
            "ts": _ts(run.started_at or run.created_at),
            "status": "running",
            "replayed": True,
        }
    ]
    for sr in step_runs:
        base = {"step_id": sr.step_id, "step_run_id": sr.id, "replayed": True}
        events.append(
            {
                "event": "step_start",
                "ts": _ts(sr.started_at),
                "type": sr.step_type,
                "attempt": sr.attempt,
                "label": sr.step_id,
                **base,
            }
        )
        tool_calls = list(sr.tool_calls or [])
        for msg in sr.messages or []:
            if msg.get("role") != "assistant":
                continue
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    events.append(
                        {"event": "text", "ts": _ts(sr.started_at), "text": block["text"], **base}
                    )
                elif block.get("type") == "tool_use":
                    events.append(
                        {
                            "event": "tool_call",
                            "ts": _ts(sr.started_at),
                            "tool": block.get("name"),
                            "input": block.get("input"),
                            "tool_use_id": block.get("id"),
                            **base,
                        }
                    )
                    if tool_calls:
                        rec = tool_calls.pop(0)
                        events.append(
                            {
                                "event": "tool_result",
                                "ts": _ts(sr.started_at),
                                **rec,
                                "tool_use_id": block.get("id"),
                                **base,
                            }
                        )
        if sr.step_type == "shell":
            # Shell steps have no messages; their command/output live in `output`.
            events.append(
                {
                    "event": "tool_result",
                    "ts": _ts(sr.finished_at),
                    "tool": "run_shell",
                    "output": sr.output,
                    "is_error": sr.status.value != "success",
                    **base,
                }
            )
        events.append(
            {
                "event": "step_end",
                "ts": _ts(sr.finished_at),
                "status": sr.status.value,
                "duration": sr.duration,
                "error": None,
                "output": (sr.output or "")[-2000:],
                **base,
            }
        )
    events.append(
        {
            "event": "run_status",
            "ts": _ts(run.finished_at),
            "status": run.status.value,
            "error": run.error,
            "replayed": True,
        }
    )
    return events
