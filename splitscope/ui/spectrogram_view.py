"""Scrolling spectrogram of the processed output (log-frequency rows)."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QFont
from PySide6.QtWidgets import QWidget

from ..dsp.chain import N_FFT
from . import theme as T


def _colormap() -> list[int]:
    stops = [
        (0.00, (18, 21, 27)),
        (0.25, (40, 30, 90)),
        (0.45, (120, 40, 130)),
        (0.62, (210, 70, 90)),
        (0.78, (250, 150, 60)),
        (0.92, (255, 225, 120)),
        (1.00, (255, 255, 235)),
    ]
    table = []
    for i in range(256):
        t = i / 255
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= t <= b:
                u = (t - a) / (b - a)
                c = [int(ca[k] + (cb[k] - ca[k]) * u) for k in range(3)]
                table.append(QColor(*c).rgb())
                break
    return table


class SpectrogramView(QWidget):
    FMIN, FMAX = 20.0, 20000.0
    ROWS = 160
    COLS = 480

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.sr = 44100
        self.db_min, self.db_max = -96.0, 0.0
        self.buf = np.zeros((self.ROWS, self.COLS), np.uint8)
        self.col = 0
        self.ctable = _colormap()
        self._map_sr = None
        self._rows = None

    def set_sr(self, sr):
        self.sr = sr
        self._map_sr = None

    def clear(self):
        self.buf[:] = 0
        self.update()

    def _row_freqs(self):
        if self._map_sr != self.sr:
            lo, hi = math.log10(self.FMIN), math.log10(self.FMAX)
            # row 0 is the top (highest frequency)
            t = 1.0 - (np.arange(self.ROWS) + 0.5) / self.ROWS
            self._rows = 10 ** (lo + t * (hi - lo))
            self._bf = np.fft.rfftfreq(N_FFT, 1.0 / self.sr)
            self._map_sr = self.sr
        return self._rows

    def push(self, post_db):
        rows = self._row_freqs()
        v = np.interp(rows, self._bf, post_db)
        u = np.clip((v - self.db_min) / (self.db_max - self.db_min), 0, 1)
        self.buf[:, self.col] = (u * 255).astype(np.uint8)
        self.col = (self.col + 1) % self.COLS
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BASE))
        target = QRectF(40, 2, max(10, self.width() - 40 - 38), max(10, self.height() - 4))
        # unroll the ring buffer so the newest column is on the right
        img_arr = np.ascontiguousarray(np.roll(self.buf, -self.col, axis=1))
        img = QImage(img_arr.data, self.COLS, self.ROWS, self.COLS, QImage.Format_Indexed8)
        img.setColorTable(self.ctable)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(target, img)
        font = QFont(self.font())
        font.setPointSizeF(7.5)
        p.setFont(font)
        p.setPen(QColor(T.MUTED))
        lo, hi = math.log10(self.FMIN), math.log10(self.FMAX)
        for f, lab in [(100, "100"), (1000, "1k"), (10000, "10k")]:
            y = target.top() + (1 - (math.log10(f) - lo) / (hi - lo)) * target.height()
            p.drawText(QRectF(0, y - 6, 34, 12), Qt.AlignRight | Qt.AlignVCenter, lab)
            p.setPen(QColor(255, 255, 255, 25))
            p.drawLine(int(target.left()), int(y), int(target.right()), int(y))
            p.setPen(QColor(T.MUTED))
        p.end()
