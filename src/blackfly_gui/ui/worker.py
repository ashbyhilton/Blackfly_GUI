"""Run analysis off the GUI thread so it never slows the live view.

Frames are never modified after they are made (each new frame, reference
correction or running average is a new array), so jobs can read them
without copying."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object, object)  # result (or the exception raised), context


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any], context: Any, signals: _Signals) -> None:
        super().__init__()
        self.fn, self.context = fn, context
        # Long-lived signals object owned by the GUI (see GaussianTab's _FitJob).
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001 - never kill the worker thread
            result = exc
        self.signals.done.emit(result, self.context)


class BackgroundTask(QObject):
    """Runs one job at a time on a worker thread. A request made while a job
    runs replaces any earlier waiting one, so -- like the camera stream --
    only the newest data is analysed and no backlog builds up.
    ``on_done(result, context)`` is called on the GUI thread."""

    def __init__(self, on_done: Callable[[Any, Any], None], parent: QObject) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _Signals(self)
        self._signals.done.connect(self._finished)
        self._on_done = on_done
        self._busy = False
        self._waiting: tuple[Callable[[], Any], Any] | None = None

    def submit(self, fn: Callable[[], Any], context: Any = None) -> None:
        if self._busy:
            self._waiting = (fn, context)
            return
        self._busy = True
        self._pool.start(_Job(fn, context, self._signals))

    def _finished(self, result: Any, context: Any) -> None:
        self._busy = False
        if self._waiting is not None:
            waiting, self._waiting = self._waiting, None
            self.submit(*waiting)
        self._on_done(result, context)
