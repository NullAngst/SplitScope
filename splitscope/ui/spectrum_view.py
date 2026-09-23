"""Spectrum analyser + EQ editor.

Mouse:
  drag a node ............ frequency / gain
  wheel over a node ...... Q (slope for cut filters)
  double-click empty ..... add a bell band
  double-click a node .... reset its gain to 0 dB
  right-click a node ..... type, channel, enable, delete
  Shift+drag or middle ... solo (audition) a narrow band under the cursor;
                           wheel while holding changes the band width
"""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QBrush, QLinearGradient, QFont,
                           QFontMetrics)
from PySide6.QtWidgets import QWidget, QMenu

from ..dsp import curves as C
from ..dsp.chain import ChainSettings, total_display_curve, N_FFT
from . import theme as T

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SLOPES = [6, 12, 18, 24, 36, 48, 72, 96]

GUIDE = [
    ("Sub", 20, 60, "#9b6bff"),
    ("Kick", 40, 120, "#ff6b6b"),
    ("Bass", 40, 400, "#ff9f43"),
    ("Snare body", 150, 300, "#ffd84d"),
    ("Snare crack", 2000, 5000, "#ffd84d"),
    ("Vocals (fund.)", 100, 1100, "#7be38a"),
    ("Vocal presence", 2000, 5000, "#7be38a"),
    ("Sibilance", 5000, 9000, "#7be38a"),
    ("Guitar", 80, 5000, "#45d4e6"),
    ("Piano / keys", 27, 4200, "#6a8dff"),
    ("Cymbals", 6000, 16000, "#b57bff"),
]


def note_name(f: float) -> str:
    if f <= 0:
        return ""
    n = 69 + 12 * math.log2(f / 440.0)
    k = int(round(n))
    cents = int(round((n - k) * 100))
    return f"{NOTE_NAMES[k % 12]}{k // 12 - 1}{'+' if cents >= 0 else ''}{cents}c"


def fmt_freq(f: float) -> str:
    return f"{f / 1000:.2f} kHz" if f >= 1000 else f"{f:.0f} Hz"


class SpectrumView(QWidget):
    eqChanged = Signal()
    bandSelected = Signal(int)
    auditionChanged = Signal(object)  # (freq, octaves) or None

    FMIN, FMAX = 20.0, 20000.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.settings: ChainSettings | None = None
        self.sr = 44100
        self.db_min, self.db_max = -96.0, 6.0
        self.eq_range = 24.0
        self.show_pre = True
        self.show_peak = False
        self.show_guide = True
        self.response = "medium"
        self._disp_pre = None
        self._disp_post = None
        self._peak = None
        self._last_t = time.monotonic()
        self._map = None
        self._map_key = None
        self.selected = -1
        self._drag = None
        self._hover = None
        self._audition = None
        self._aud_width = 1.0
        self._curves = None
        self._curve_key = None
        self.has_audio = False

    # ------------------------------------------------------------ geometry
    def plot_rect(self) -> QRectF:
        guide_h = 44 if self.show_guide else 0
        return QRectF(40, 10, max(10, self.width() - 40 - 38), max(10, self.height() - 10 - 22 - guide_h))

    def f2x(self, f):
        r = self.plot_rect()
        lo, hi = math.log10(self.FMIN), math.log10(self.FMAX)
        return r.left() + (np.log10(np.maximum(f, 1e-3)) - lo) / (hi - lo) * r.width()

    def x2f(self, x):
        r = self.plot_rect()
        lo, hi = math.log10(self.FMIN), math.log10(self.FMAX)
        t = (x - r.left()) / max(1.0, r.width())
        return float(10 ** (lo + t * (hi - lo)))

    def db2y(self, db):
        r = self.plot_rect()
        return r.top() + (self.db_max - np.asarray(db)) / (self.db_max - self.db_min) * r.height()

    def g2y(self, g):
        r = self.plot_rect()
        return r.top() + (self.eq_range - np.asarray(g)) / (2 * self.eq_range) * r.height()

    def y2g(self, y):
        r = self.plot_rect()
        return float(self.eq_range - (y - r.top()) / max(1.0, r.height()) * 2 * self.eq_range)

    # ------------------------------------------------------------ data
    def set_settings(self, s: ChainSettings, sr: int):
        self.settings = s
        self.sr = sr
        self._curve_key = None
        self.update()

    def invalidate_curves(self):
        self._curve_key = None
        self.update()

    def _column_map(self):
        r = self.plot_rect()
        key = (int(r.width()), self.sr)
        if self._map_key == key and self._map is not None:
            return self._map
        ncol = max(2, int(r.width()))
        xs = np.arange(ncol + 1)
        lo, hi = math.log10(self.FMIN), math.log10(self.FMAX)
        edges = 10 ** (lo + xs / ncol * (hi - lo))
        centers = np.sqrt(edges[:-1] * edges[1:])
        bf = np.fft.rfftfreq(N_FFT, 1.0 / self.sr)
        starts = np.clip(np.searchsorted(bf, edges[:-1]), 0, len(bf) - 1)
        ends = np.clip(np.searchsorted(bf, edges[1:]), 0, len(bf))
        has = ends > starts
        self._map = (centers, bf, starts, has)
        self._map_key = key
        return self._map

    def _to_columns(self, db_bins):
        centers, bf, starts, has = self._column_map()
        interp = np.interp(centers, bf, db_bins)
        red = np.maximum.reduceat(db_bins, starts)
        return np.where(has, np.maximum(red, interp), interp)

    def reset_display(self):
        self._disp_pre = self._disp_post = self._peak = None
        self.update()

    def set_frame(self, pre_db, post_db):
        now = time.monotonic()
        dt = min(0.2, now - self._last_t)
        self._last_t = now
        pre = self._to_columns(pre_db)
        post = self._to_columns(post_db)
        fall = {"fast": 90.0, "medium": 45.0, "slow": 20.0}.get(self.response, 45.0) * dt
        if self._disp_post is None or self._disp_post.shape != post.shape:
            self._disp_pre, self._disp_post = pre, post
        else:
            self._disp_pre = np.maximum(pre, self._disp_pre - fall)
            self._disp_post = np.maximum(post, self._disp_post - fall)
        if self.show_peak:
            if self._peak is None or self._peak.shape != post.shape:
                self._peak = post.copy()
            else:
                self._peak = np.maximum(self._peak - 3.0 * dt, post)
        self.update()

    def set_static(self, pre_db, post_db):
        self._disp_pre = self._to_columns(pre_db)
        self._disp_post = self._to_columns(post_db)
        self.update()

    # ------------------------------------------------------------ curves
    def _get_curves(self):
        s = self.settings
        if s is None:
            return None
        r = self.plot_rect()
        key = (int(r.width()), id(s))
        if self._curve_key == key and self._curves is not None:
            return self._curves
        ncol = max(2, int(r.width()))
        lo, hi = math.log10(self.FMIN), math.log10(self.FMAX)
        f = 10 ** (lo + (np.arange(ncol) + 0.5) / ncol * (hi - lo))
        tot = total_display_curve(s, f)
        out = {k: C.lin_to_db(v) for k, v in tot.items()}
        sp = s.spatial
        if not sp.mid_mute:
            out["mid"] = out["mid"] + sp.mid_db
        if not sp.side_mute:
            out["side"] = out["side"] + sp.side_db
        self._curves = (f, out)
        self._curve_key = key
        return self._curves

    def _node_pos(self, i):
        b = self.settings.eq[i]
        x = float(self.f2x(b.freq))
        if b.uses_gain:
            y = float(self.g2y(b.gain))
        else:
            y = float(self.g2y(0.0))
        return QPointF(x, y)

    def _hit(self, pos, radius=9):
        if self.settings is None or not self.settings.eq_enabled:
            return -1
        best, bd = -1, radius * radius
        for i in range(len(self.settings.eq)):
            p = self._node_pos(i)
            d = (p.x() - pos.x()) ** 2 + (p.y() - pos.y()) ** 2
            if d <= bd:
                best, bd = i, d
        return best

    # ------------------------------------------------------------ painting
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(T.BASE))
        r = self.plot_rect()
        p.fillRect(r, QColor("#12151b"))
        self._paint_grid(p, r)
        if self._audition is not None:
            f0, octs = self._audition
            half = 2 ** (octs / 2)
            x0, x1 = float(self.f2x(f0 / half)), float(self.f2x(f0 * half))
            p.fillRect(QRectF(x0, r.top(), x1 - x0, r.height()), QColor(122, 167, 255, 40))
        p.save()
        p.setClipRect(r)
        self._paint_spectrum(p, r)
        self._paint_curves(p, r)
        p.restore()
        self._paint_nodes(p, r)
        if self.show_guide:
            self._paint_guide(p, r)
        self._paint_readout(p, r)
        if not self.has_audio:
            p.setPen(QColor(T.MUTED))
            p.drawText(r, Qt.AlignCenter,
                       "Open or drop an audio file.\nWhile it plays, hold Shift and drag here to solo a frequency band.")
        p.end()

    def _paint_grid(self, p, r):
        font = QFont(self.font())
        font.setPointSizeF(8)
        p.setFont(font)
        grid = QPen(QColor("#232835"))
        grid.setWidth(1)
        for f in [30, 40, 50, 60, 70, 80, 90, 200, 300, 400, 600, 700, 800, 900, 3000, 4000, 6000, 7000, 8000, 9000]:
            x = float(self.f2x(f))
            p.setPen(grid)
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        major = QPen(QColor("#2e3544"))
        for f, lab in [(20, "20"), (50, "50"), (100, "100"), (500, "500"), (1000, "1k"), (2000, "2k"),
                       (5000, "5k"), (10000, "10k"), (20000, "20k")]:
            x = float(self.f2x(f))
            p.setPen(major)
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(x - 20, r.bottom() + 3, 40, 14), Qt.AlignHCenter | Qt.AlignTop, lab)
        # spectrum dB on the left
        for db in range(0, int(self.db_min) - 1, -12):
            y = float(self.db2y(db))
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(0, y - 7, 34, 14), Qt.AlignRight | Qt.AlignVCenter, f"{db}")
        # EQ gain on the right
        for g in (-18, -12, -6, 0, 6, 12, 18):
            y = float(self.g2y(g))
            p.setPen(QPen(QColor("#3a4254") if g == 0 else QColor("#232835"), 1, Qt.DashLine if g else Qt.SolidLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(r.right() + 4, y - 7, 34, 14), Qt.AlignLeft | Qt.AlignVCenter, f"{g:+d}" if g else "0")

    def _prism(self, r, alpha=255):
        g = QLinearGradient(r.left(), 0, r.right(), 0)
        for pos, col in T.PRISM:
            c = QColor(col)
            c.setAlpha(alpha)
            g.setColorAt(pos, c)
        return g

    def _path(self, r, vals, closed):
        n = len(vals)
        xs = r.left() + (np.arange(n) + 0.5) / n * r.width()
        ys = self.db2y(np.clip(vals, self.db_min - 10, self.db_max + 10))
        path = QPainterPath()
        if closed:
            path.moveTo(QPointF(xs[0], r.bottom()))
            for x, y in zip(xs, ys):
                path.lineTo(QPointF(float(x), float(y)))
            path.lineTo(QPointF(xs[-1], r.bottom()))
            path.closeSubpath()
        else:
            path.moveTo(QPointF(float(xs[0]), float(ys[0])))
            for x, y in zip(xs[1:], ys[1:]):
                path.lineTo(QPointF(float(x), float(y)))
        return path

    def _paint_spectrum(self, p, r):
        if self._disp_post is None:
            return
        if self.show_pre and self._disp_pre is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(140, 150, 170, 38))
            p.drawPath(self._path(r, self._disp_pre, True))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(150, 160, 180, 90), 1))
            p.drawPath(self._path(r, self._disp_pre, False))
        grad = self._prism(r, 70)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(self._path(r, self._disp_post, True))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(self._prism(r, 255)), 1.6))
        p.drawPath(self._path(r, self._disp_post, False))
        if self.show_peak and self._peak is not None:
            p.setPen(QPen(QColor(255, 255, 255, 110), 1))
            p.drawPath(self._path(r, self._peak, False))

    def _paint_curves(self, p, r):
        got = self._get_curves()
        if got is None:
            return
        f, cur = got
        xs = r.left() + (np.arange(len(f)) + 0.5) / len(f) * r.width()

        def draw(vals, color, width, style=Qt.SolidLine):
            ys = self.g2y(np.clip(vals, -self.eq_range * 1.5, self.eq_range * 1.5))
            path = QPainterPath()
            path.moveTo(QPointF(float(xs[0]), float(ys[0])))
            for x, y in zip(xs[1:], ys[1:]):
                path.lineTo(QPointF(float(x), float(y)))
            p.setPen(QPen(QColor(color), width, style))
            p.drawPath(path)

        for ch in ("mid", "side", "left", "right"):
            if not np.allclose(cur[ch], 0.0, atol=0.01):
                draw(cur[ch], T.CHANNEL_COLORS[ch], 1.5, Qt.DashLine)
        draw(cur["stereo"], T.CHANNEL_COLORS["stereo"], 2.0)

    def _paint_nodes(self, p, r):
        s = self.settings
        if s is None or not s.eq_enabled:
            return
        font = QFont(self.font())
        font.setPointSizeF(7.5)
        font.setBold(True)
        p.setFont(font)
        for i, b in enumerate(s.eq):
            pt = self._node_pos(i)
            col = QColor(T.CHANNEL_COLORS.get(b.channel, "#ffffff"))
            if not b.enabled:
                col.setAlpha(80)
            rad = 8 if i == self.selected else 6.5
            p.setPen(QPen(QColor("#12151b"), 2))
            p.setBrush(col)
            p.drawEllipse(pt, rad, rad)
            if i == self.selected:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(col, 1.2))
                p.drawEllipse(pt, rad + 4, rad + 4)
            p.setPen(QColor("#12151b"))
            p.drawText(QRectF(pt.x() - 8, pt.y() - 8, 16, 16), Qt.AlignCenter, str(i + 1))

    def _paint_guide(self, p, r):
        top = r.bottom() + 20
        lanes: list[list[tuple[float, float]]] = [[], [], []]
        font = QFont(self.font())
        font.setPointSizeF(7.5)
        p.setFont(font)
        fm = QFontMetrics(font)
        for name, lo, hi, col in GUIDE:
            x0, x1 = float(self.f2x(lo)), float(self.f2x(hi))
            lane = 0
            for li, used in enumerate(lanes):
                if all(x1 < a - 2 or x0 > b + 2 for a, b in used):
                    lane = li
                    break
            else:
                lane = len(lanes) - 1
            lanes[lane].append((x0, x1))
            y = top + lane * 8
            c = QColor(col)
            c.setAlpha(150)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x0, y, x1 - x0, 5), 2, 2)
        # labels on hover only (keeps the strip quiet)
        if self._hover is not None and self._hover.y() > r.bottom() + 16:
            f = self.x2f(self._hover.x())
            names = [n for n, lo, hi, _ in GUIDE if lo <= f <= hi]
            if names:
                txt = ", ".join(names)
                w = fm.horizontalAdvance(txt) + 12
                x = min(max(r.left(), self._hover.x() - w / 2), r.right() - w)
                p.setBrush(QColor(T.RAISED))
                p.setPen(QColor(T.LINE))
                p.drawRoundedRect(QRectF(x, r.bottom() - 22, w, 18), 4, 4)
                p.setPen(QColor(T.TEXT))
                p.drawText(QRectF(x, r.bottom() - 22, w, 18), Qt.AlignCenter, txt)

    def _paint_readout(self, p, r):
        lines = []
        if self._audition is not None:
            f0, octs = self._audition
            lines.append(f"Soloing {fmt_freq(f0)} ({note_name(f0)}), {octs:.2f} oct wide. Wheel changes width.")
        elif self._hover is not None and r.contains(self._hover):
            f = self.x2f(self._hover.x())
            txt = f"{fmt_freq(f)}  {note_name(f)}"
            if self._disp_post is not None:
                i = int(np.clip((self._hover.x() - r.left()) / r.width() * len(self._disp_post), 0,
                                len(self._disp_post) - 1))
                txt += f"   {self._disp_post[i]:.1f} dBFS"
            txt += f"   EQ {self.y2g(self._hover.y()):+.1f} dB"
            lines.append(txt)
        if self.selected >= 0 and self.settings is not None and self.selected < len(self.settings.eq):
            b = self.settings.eq[self.selected]
            desc = dict(C.BAND_TYPES).get(b.type, b.type)
            s = f"Band {self.selected + 1}: {desc} {fmt_freq(b.freq)}"
            if b.uses_gain:
                s += f" {b.gain:+.1f} dB"
            if b.uses_q:
                s += f" Q {b.q:.2f}"
            if b.type in ("low_cut", "high_cut"):
                s += f" {b.slope} dB/oct"
            s += f" [{b.channel}]"
            lines.append(s)
        if not lines:
            return
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        p.setFont(font)
        fm = QFontMetrics(font)
        w = max(fm.horizontalAdvance(t) for t in lines) + 14
        h = 16 * len(lines) + 6
        box = QRectF(r.left() + 6, r.top() + 6, w, h)
        p.setPen(QColor(T.LINE))
        p.setBrush(QColor(21, 24, 31, 220))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor(T.TEXT))
        for i, t in enumerate(lines):
            p.drawText(QRectF(box.left() + 7, box.top() + 3 + i * 16, w, 16), Qt.AlignVCenter | Qt.AlignLeft, t)

    # ------------------------------------------------------------ interaction
    def _changed(self):
        self._curve_key = None
        self.eqChanged.emit()
        self.update()

    def select(self, i):
        self.selected = i
        self.update()

    def mousePressEvent(self, ev):
        pos = ev.position()
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.LeftButton and ev.modifiers() & Qt.ShiftModifier):
            self._set_audition(self.x2f(pos.x()))
            return
        if ev.button() == Qt.LeftButton:
            i = self._hit(pos)
            if i >= 0:
                self.selected = i
                b = self.settings.eq[i]
                self._drag = (i, pos, b.freq, b.gain)
                self.bandSelected.emit(i)
                self.update()
        elif ev.button() == Qt.RightButton:
            i = self._hit(pos)
            if i >= 0:
                self.selected = i
                self.bandSelected.emit(i)
                self._band_menu(i, ev.globalPosition().toPoint())

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        self._hover = pos
        if self._audition is not None:
            self._set_audition(self.x2f(pos.x()))
            return
        if self._drag is not None:
            i, p0, f0, g0 = self._drag
            b = self.settings.eq[i]
            r = self.plot_rect()
            lo, hi = math.log10(self.FMIN), math.log10(self.FMAX)
            dlog = (pos.x() - p0.x()) / max(1.0, r.width()) * (hi - lo)
            fine = 0.2 if ev.modifiers() & Qt.ControlModifier else 1.0
            b.freq = float(np.clip(f0 * 10 ** (dlog * fine), 10.0, min(22000.0, self.sr / 2 - 10)))
            if b.uses_gain:
                dg = -(pos.y() - p0.y()) / max(1.0, r.height()) * 2 * self.eq_range * fine
                b.gain = float(np.clip(g0 + dg, -30.0, 30.0))
            self._changed()
            return
        hit = self._hit(pos)
        self.setCursor(Qt.OpenHandCursor if hit >= 0 else Qt.CrossCursor)
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._audition is not None and (ev.button() in (Qt.MiddleButton, Qt.LeftButton)):
            self._audition = None
            self.auditionChanged.emit(None)
            self.update()
            return
        self._drag = None

    def leaveEvent(self, ev):
        self._hover = None
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if self.settings is None or ev.button() != Qt.LeftButton:
            return
        pos = ev.position()
        i = self._hit(pos)
        if i >= 0:
            b = self.settings.eq[i]
            if b.uses_gain:
                b.gain = 0.0
                self._changed()
            return
        if not self.plot_rect().contains(pos) or len(self.settings.eq) >= 16:
            return
        self.settings.eq_enabled = True
        g = float(np.clip(self.y2g(pos.y()), -24, 24))
        self.settings.eq.append(C.EQBand("bell", self.x2f(pos.x()), round(g, 1), 1.0))
        self.selected = len(self.settings.eq) - 1
        self.bandSelected.emit(self.selected)
        self._changed()

    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        if self._audition is not None:
            self._aud_width = float(np.clip(self._aud_width * (0.85 ** steps), 0.2, 4.0))
            self._set_audition(self._audition[0])
            return
        i = self._hit(ev.position(), radius=14)
        if i < 0:
            i = self.selected if 0 <= self.selected < len(self.settings.eq if self.settings else []) else -1
            if i < 0 or not self._hit_near_selected(ev.position()):
                ev.ignore()
                return
        b = self.settings.eq[i]
        if b.type in ("low_cut", "high_cut"):
            k = SLOPES.index(b.slope) if b.slope in SLOPES else 3
            b.slope = SLOPES[int(np.clip(k + (1 if steps > 0 else -1), 0, len(SLOPES) - 1))]
        else:
            b.q = float(np.clip(b.q * (1.15 ** steps), 0.1, 30.0))
        self._changed()

    def _hit_near_selected(self, pos):
        if self.selected < 0 or self.settings is None:
            return False
        p = self._node_pos(self.selected)
        return abs(p.x() - pos.x()) < 60 and abs(p.y() - pos.y()) < 60

    def _set_audition(self, f):
        f = float(np.clip(f, 20.0, 20000.0))
        self._audition = (f, self._aud_width)
        self.auditionChanged.emit(self._audition)
        self.update()

    def _band_menu(self, i, gpos):
        b = self.settings.eq[i]
        m = QMenu(self)
        tm = m.addMenu("Type")
        for key, label in C.BAND_TYPES:
            a = tm.addAction(label)
            a.setCheckable(True)
            a.setChecked(b.type == key)
            a.triggered.connect(lambda _=False, k=key: self._set_attr(i, "type", k))
        cm = m.addMenu("Channel")
        for key, label in C.CHANNELS:
            a = cm.addAction(label)
            a.setCheckable(True)
            a.setChecked(b.channel == key)
            a.triggered.connect(lambda _=False, k=key: self._set_attr(i, "channel", k))
        en = m.addAction("Enabled")
        en.setCheckable(True)
        en.setChecked(b.enabled)
        en.triggered.connect(lambda checked: self._set_attr(i, "enabled", checked))
        m.addSeparator()
        dl = m.addAction("Delete band")
        dl.triggered.connect(lambda: self._delete(i))
        m.exec(gpos)

    def _set_attr(self, i, attr, val):
        setattr(self.settings.eq[i], attr, val)
        self.bandSelected.emit(i)
        self._changed()

    def _delete(self, i):
        if 0 <= i < len(self.settings.eq):
            del self.settings.eq[i]
            self.selected = -1
            self.bandSelected.emit(-1)
            self._changed()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected >= 0:
            self._delete(self.selected)
            return
        super().keyPressEvent(ev)
