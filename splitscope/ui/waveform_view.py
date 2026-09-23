"""Waveform overview: click to seek, drag to set a loop region,
drag the region edges to adjust it, right-click to clear it."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath
from PySide6.QtWidgets import QWidget

from . import theme as T


def fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    s = sec - 60 * m
    return f"{m:02d}:{s:06.3f}"


class WaveformView(QWidget):
    seekRequested = Signal(int)
    loopChanged = Signal(int, int)   # start, end (samples); end <= start means cleared

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.setMouseTracking(True)
        self.env = None      # (cols, 2): min, max
        self.length = 0
        self.sr = 44100
        self.pos = 0.0
        self.loop = (0, 0)
        self.loop_on = False
        self.color = QColor(T.ACCENT)
        self._drag = None
        self._hover_x = None

    def area(self) -> QRectF:
        return QRectF(40, 14, max(10, self.width() - 40 - 38), max(10, self.height() - 14 - 4))

    def set_audio(self, audio: np.ndarray | None, sr: int, color: str | None = None):
        self.sr = sr
        if audio is None or audio.shape[0] == 0:
            self.env = None
            self.length = 0
            self.update()
            return
        self.length = audio.shape[0]
        mono = audio.mean(axis=1)
        cols = 2000
        n = len(mono)
        per = max(1, n // cols)
        usable = (n // per) * per
        blk = mono[:usable].reshape(-1, per)
        self.env = np.stack([blk.min(axis=1), blk.max(axis=1)], 1)
        if color:
            self.color = QColor(color)
        self.update()

    def set_position(self, pos: float):
        self.pos = pos
        self.update()

    def set_loop(self, start, end, on):
        self.loop = (int(start), int(end))
        self.loop_on = on
        self.update()

    def _x(self, sample):
        a = self.area()
        return a.left() + (sample / max(1, self.length)) * a.width()

    def _s(self, x):
        a = self.area()
        return int(np.clip((x - a.left()) / max(1.0, a.width()), 0, 1) * self.length)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(T.BASE))
        a = self.area()
        p.fillRect(a, QColor("#12151b"))
        font = QFont(self.font())
        font.setPointSizeF(7.5)
        p.setFont(font)
        if self.env is None:
            p.setPen(QColor(T.MUTED))
            p.drawText(a, Qt.AlignCenter, "No audio loaded")
            p.end()
            return
        # time ruler
        dur = self.length / self.sr
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300) if dur / s <= 14), 600)
        p.setPen(QColor(T.MUTED))
        t = 0
        while t <= dur:
            x = self._x(t * self.sr)
            p.drawLine(QPointF(x, a.top() - 4), QPointF(x, a.top()))
            p.drawText(QRectF(x - 30, 0, 60, 12), Qt.AlignCenter, f"{int(t // 60)}:{int(t % 60):02d}")
            t += step
        # loop region
        if self.loop[1] > self.loop[0]:
            x0, x1 = self._x(self.loop[0]), self._x(self.loop[1])
            c = QColor(T.ACCENT)
            c.setAlpha(55 if self.loop_on else 22)
            p.fillRect(QRectF(x0, a.top(), x1 - x0, a.height()), c)
            p.setPen(QPen(QColor(T.ACCENT), 1))
            p.drawLine(QPointF(x0, a.top()), QPointF(x0, a.bottom()))
            p.drawLine(QPointF(x1, a.top()), QPointF(x1, a.bottom()))
        # envelope
        cols = self.env.shape[0]
        mid = a.center().y()
        h = a.height() / 2 * 0.95
        xs = a.left() + (np.arange(cols) + 0.5) / cols * a.width()
        path = QPainterPath()
        path.moveTo(QPointF(xs[0], mid - self.env[0, 1] * h))
        for x, v in zip(xs[1:], self.env[1:, 1]):
            path.lineTo(QPointF(float(x), float(mid - v * h)))
        for x, v in zip(xs[::-1], self.env[::-1, 0]):
            path.lineTo(QPointF(float(x), float(mid - v * h)))
        path.closeSubpath()
        c = QColor(self.color)
        c.setAlpha(170)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(path)
        # playhead
        x = self._x(self.pos)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
        if self._hover_x is not None:
            hs = self._s(self._hover_x)
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(self._hover_x + 4, a.top() + 2, 90, 12), Qt.AlignLeft, fmt_time(hs / self.sr))
        p.end()

    def mousePressEvent(self, ev):
        if self.env is None:
            return
        x = ev.position().x()
        if ev.button() == Qt.RightButton:
            self.loopChanged.emit(0, 0)
            return
        if ev.button() != Qt.LeftButton:
            return
        if self.loop[1] > self.loop[0]:
            x0, x1 = self._x(self.loop[0]), self._x(self.loop[1])
            if abs(x - x0) < 5:
                self._drag = ("edge0", x)
                return
            if abs(x - x1) < 5:
                self._drag = ("edge1", x)
                return
        self._drag = ("new", x)

    def mouseMoveEvent(self, ev):
        x = ev.position().x()
        self._hover_x = x
        if self._drag is None:
            near = False
            if self.loop[1] > self.loop[0]:
                near = min(abs(x - self._x(self.loop[0])), abs(x - self._x(self.loop[1]))) < 5
            self.setCursor(Qt.SizeHorCursor if near else Qt.IBeamCursor)
            self.update()
            return
        kind, x0 = self._drag
        if kind == "new":
            if abs(x - x0) > 4:
                a, b = sorted((self._s(x0), self._s(x)))
                self.loopChanged.emit(a, b)
        elif kind == "edge0":
            self.loopChanged.emit(min(self._s(x), self.loop[1] - 1), self.loop[1])
        elif kind == "edge1":
            self.loopChanged.emit(self.loop[0], max(self._s(x), self.loop[0] + 1))
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag is None:
            return
        kind, x0 = self._drag
        self._drag = None
        if kind == "new" and abs(ev.position().x() - x0) <= 4:
            self.seekRequested.emit(self._s(x0))

    def leaveEvent(self, ev):
        self._hover_x = None
        self.update()
