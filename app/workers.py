"""Typed worker events for GUI queues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkerEvent:
    phase: str
    done: int = 0
    total: int = 0
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PhaseStarted(WorkerEvent):
    event_type: str = "PhaseStarted"


@dataclass(slots=True)
class PhaseProgress(WorkerEvent):
    event_type: str = "PhaseProgress"


@dataclass(slots=True)
class WarningRaised(WorkerEvent):
    event_type: str = "WarningRaised"


@dataclass(slots=True)
class ErrorRaised(WorkerEvent):
    event_type: str = "ErrorRaised"


@dataclass(slots=True)
class AnalysisCompleted(WorkerEvent):
    event_type: str = "AnalysisCompleted"


@dataclass(slots=True)
class CopyCompleted(WorkerEvent):
    event_type: str = "CopyCompleted"


@dataclass(slots=True)
class Cancelled(WorkerEvent):
    event_type: str = "Cancelled"
