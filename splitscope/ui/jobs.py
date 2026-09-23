"""Background jobs with progress and cancellation."""
from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QThread, Signal

from ..dsp.mdx import Cancelled


class Job(QThread):
    progress = Signal(float, str)
    finishedOk = Signal(object)
    failed = Signal(str)
    wasCancelled = Signal()

    def __init__(self, title: str, fn, parent=None):
        """fn(progress(frac, label), cancelled() -> bool) -> result"""
        super().__init__(parent)
        self.title = title
        self.fn = fn
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self):
        try:
            res = self.fn(lambda f, s="": self.progress.emit(float(f), str(s)), self.cancelled)
        except Cancelled:
            self.wasCancelled.emit()
            return
        except Exception as e:  # report everything, the GUI decides what to show
            if self.cancelled():
                self.wasCancelled.emit()
                return
            self.failed.emit(f"{e}\n\n{traceback.format_exc(limit=6)}")
            return
        if self.cancelled():
            self.wasCancelled.emit()
        else:
            self.finishedOk.emit(res)
