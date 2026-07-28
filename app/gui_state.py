"""Small GUI state and undo/redo helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from app.enums import DestinationCategory


@dataclass(slots=True)
class ManualAssignment:
    file_ids: tuple[str, ...]
    old_categories: tuple[DestinationCategory, ...]
    new_category: DestinationCategory


@dataclass(slots=True)
class SeriesAssignment:
    file_ids: tuple[str, ...]
    old_series_ids: tuple[str | None, ...]
    new_series_id: str | None
    new_category: DestinationCategory | None


@dataclass(slots=True)
class GuiState:
    selected_filter: str = "all"
    search_text: str = ""
    result_folder: str = ""
    hidden_columns: set[str] = field(default_factory=set)
    preview_token: int = 0

    def next_preview_token(self) -> int:
        self.preview_token += 1
        return self.preview_token


@dataclass(slots=True)
class UndoAction:
    label: str
    undo: Callable[[], None]
    redo: Callable[[], None]


class UndoRedoStack:
    def __init__(self, limit: int = 30):
        self.limit = limit
        self._undo: deque[UndoAction] = deque(maxlen=limit)
        self._redo: deque[UndoAction] = deque(maxlen=limit)

    def push(self, action: UndoAction) -> None:
        self._undo.append(action)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        action = self._undo.pop()
        action.undo()
        self._redo.append(action)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        action = self._redo.pop()
        action.redo()
        self._undo.append(action)
        return True

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    @property
    def undo_count(self) -> int:
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        return len(self._redo)
