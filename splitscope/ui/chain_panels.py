"""Right-hand processing panels. Every widget edits the shared ChainSettings
in place and emits `changed`; the main window recompiles the chain."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QSlider, QComboBox,
                               QCheckBox, QPushButton, QTabWidget, QListWidget, QGroupBox, QScrollArea,
                               QFrame, QSizePolicy, QProgressBar)

from ..dsp import curves as C
from ..dsp.chain import SPATIAL_MODES, MASK_MODES, ChainSettings, default_eq


def fmt_hz(f: float) -> str:
    return f"{f / 1000:.2f} kHz" if f >= 1000 else f"{f:.0f} Hz"


def fmt_pan(v: float) -> str:
    if abs(v) < 0.01:
        return "Center"
    return f"{'L' if v < 0 else 'R'} {abs(v) * 100:.0f}%"


class SliderRow(QWidget):
    """Label + slider + value readout. Linear or logarithmic float mapping.
    Double-click the label to return to the default value."""

    valueChanged = Signal(float)

    def __init__(self, label, lo, hi, default, fmt=None, log=False, steps=1000, tip="", parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.default, self.log, self.steps = lo, hi, default, log, steps
        self.fmt = fmt or (lambda v: f"{v:.2f}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.lab = QLabel(label)
        self.lab.setFixedWidth(92)
        self.lab.setToolTip((tip + "\n" if tip else "") + "Double-click to reset.")
        self.lab.mouseDoubleClickEvent = lambda e: self.setValue(self.default, emit=True)
        self.sl = QSlider(Qt.Horizontal)
        self.sl.setRange(0, steps)
        if tip:
            self.sl.setToolTip(tip)
        self.val = QLabel()
        self.val.setFixedWidth(70)
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.lab)
        lay.addWidget(self.sl, 1)
        lay.addWidget(self.val)
        self._v = default
        self.sl.valueChanged.connect(self._moved)
        self.setValue(default)

    def _to_pos(self, v):
        v = min(max(v, self.lo), self.hi)
        if self.log:
            t = math.log(v / self.lo) / math.log(self.hi / self.lo)
        else:
            t = (v - self.lo) / (self.hi - self.lo)
        return int(round(t * self.steps))

    def _from_pos(self, p):
        t = p / self.steps
        if self.log:
            return self.lo * (self.hi / self.lo) ** t
        return self.lo + t * (self.hi - self.lo)

    def _moved(self, p):
        self._v = self._from_pos(p)
        self.val.setText(self.fmt(self._v))
        self.valueChanged.emit(self._v)

    def value(self) -> float:
        return self._v

    def setValue(self, v, emit=False):
        self._v = float(v)
        self.sl.blockSignals(True)
        self.sl.setValue(self._to_pos(v))
        self.sl.blockSignals(False)
        self.val.setText(self.fmt(self._v))
        if emit:
            self.valueChanged.emit(self._v)


def _hint(text):
    h = QLabel(text)
    h.setObjectName("hint")
    h.setWordWrap(True)
    return h


def _scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(inner)
    return sa


class _Tab(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s: ChainSettings | None = None
        self._loading = False

    def emit(self):
        if not self._loading:
            self.changed.emit()

    def set_settings(self, s: ChainSettings):
        self.s = s
        self._loading = True
        try:
            self.load()
        finally:
            self._loading = False

    def load(self):
        pass


# ------------------------------------------------------------------ spatial
class SpatialTab(_Tab):
    def __init__(self, parent=None):
        super().__init__(parent)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(6)
        lay.addWidget(_hint("Works on stereo position. Center extract keeps what is panned to the middle "
                            "(usually lead vocal, kick, snare, bass). Remove center keeps the sides "
                            "(wide guitars, backing vocals, reverb)."))
        quick = QHBoxLayout()
        for text, mode in (("Center only", "center"), ("Sides only", "remove_center"), ("Off", "stereo")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, m=mode: self._quick(m))
            quick.addWidget(b)
        lay.addLayout(quick)
        row = QHBoxLayout()
        row.addWidget(QLabel("Mode"))
        self.mode = QComboBox()
        for k, lbl in SPATIAL_MODES:
            self.mode.addItem(lbl, k)
        row.addWidget(self.mode, 1)
        lay.addLayout(row)

        self.target = SliderRow("Pan target", -1, 1, 0, fmt_pan, tip="Stereo position to extract or remove.")
        self.width = SliderRow("Width", 0.05, 1.0, 0.35, lambda v: f"{v:.2f}",
                               tip="How wide a pan window counts as the target. Smaller = stricter, more artifacts.")
        self.phase = SliderRow("Phase strict", 0, 1, 0.5, lambda v: f"{v * 100:.0f}%",
                               tip="Also require left and right to be in phase. Rejects reverb and wide stereo tricks.")
        self.smooth = SliderRow("Smoothing", 0, 0.95, 0.5, lambda v: f"{v * 100:.0f}%",
                                tip="Smooths the mask over time. Less warble, slower response.")
        self.depth = SliderRow("Depth", 0, 1, 1.0, lambda v: f"{v * 100:.0f}%",
                               tip="0% = no effect, 100% = full extraction or removal.")
        self.flo = SliderRow("Range low", 20, 20000, 20, fmt_hz, log=True,
                             tip="Only process this frequency range; outside it passes through untouched.")
        self.fhi = SliderRow("Range high", 20, 20000, 20000, fmt_hz, log=True)
        for w in (self.target, self.width, self.phase, self.smooth, self.depth, self.flo, self.fhi):
            lay.addWidget(w)

        g = QGroupBox("Mid / side levels")
        gl = QGridLayout(g)
        self.mid = SliderRow("Mid", -30, 12, 0, lambda v: f"{v:+.1f} dB")
        self.side = SliderRow("Side", -30, 12, 0, lambda v: f"{v:+.1f} dB")
        self.mid_m = QCheckBox("Mute")
        self.side_m = QCheckBox("Mute")
        gl.addWidget(self.mid, 0, 0)
        gl.addWidget(self.mid_m, 0, 1)
        gl.addWidget(self.side, 1, 0)
        gl.addWidget(self.side_m, 1, 1)
        gl.addWidget(_hint("Mid = L+R, side = L-R. Muting mid is the classic karaoke trick; it also removes "
                           "bass and kick. Use Center extract / Remove center for cleaner results."), 2, 0, 1, 2)
        lay.addWidget(g)
        lay.addStretch(1)
        out = QVBoxLayout(self)
        out.setContentsMargins(0, 0, 0, 0)
        out.addWidget(_scroll(inner))

        self.mode.currentIndexChanged.connect(self._mode)
        for w, attr in ((self.target, "target"), (self.width, "width"), (self.phase, "phase_strict"),
                        (self.smooth, "smoothing"), (self.depth, "depth"), (self.flo, "f_lo"),
                        (self.fhi, "f_hi"), (self.mid, "mid_db"), (self.side, "side_db")):
            w.valueChanged.connect(lambda v, a=attr: self._set(a, v))
        self.mid_m.toggled.connect(lambda v: self._set("mid_mute", v))
        self.side_m.toggled.connect(lambda v: self._set("side_mute", v))

    def _set(self, attr, v):
        if self.s is None:
            return
        setattr(self.s.spatial, attr, v)
        self.emit()

    def _quick(self, mode):
        self.mode.setCurrentIndex(self.mode.findData(mode))

    def _mode(self, _):
        if self.s is None:
            return
        self.s.spatial.mode = self.mode.currentData()
        self._enable()
        self.emit()

    def _enable(self):
        m = self.mode.currentData()
        masked = m in MASK_MODES
        for w in (self.width, self.phase, self.smooth, self.depth):
            w.setEnabled(masked)
        self.target.setEnabled(m in ("pan_focus", "pan_remove"))
        self.flo.setEnabled(m != "stereo")
        self.fhi.setEnabled(m != "stereo")

    def load(self):
        sp = self.s.spatial
        self.mode.setCurrentIndex(max(0, self.mode.findData(sp.mode)))
        self.target.setValue(sp.target)
        self.width.setValue(sp.width)
        self.phase.setValue(sp.phase_strict)
        self.smooth.setValue(sp.smoothing)
        self.depth.setValue(sp.depth)
        self.flo.setValue(sp.f_lo)
        self.fhi.setValue(sp.f_hi)
        self.mid.setValue(sp.mid_db)
        self.side.setValue(sp.side_db)
        self.mid_m.setChecked(sp.mid_mute)
        self.side_m.setChecked(sp.side_mute)
        self._enable()


# ------------------------------------------------------------------ focus
class FocusTab(_Tab):
    def __init__(self, parent=None):
        super().__init__(parent)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(6)
        lay.addWidget(_hint("Frequency focus keeps (or removes) the range an instrument mostly lives in. "
                            "It is a filter, so anything else in that range comes along. Combine it with "
                            "Center extract or an AI stem for real isolation."))
        self.en = QCheckBox("Enable frequency focus")
        lay.addWidget(self.en)
        row = QHBoxLayout()
        row.addWidget(QLabel("Preset"))
        self.preset = QComboBox()
        self.preset.addItem("Custom", "custom")
        for k, (lbl, _, _) in C.FOCUS_PRESETS.items():
            self.preset.addItem(lbl, k)
        row.addWidget(self.preset, 1)
        lay.addLayout(row)
        self.desc = _hint("")
        lay.addWidget(self.desc)
        self.lo1 = SliderRow("Range 1 low", 20, 20000, 150, fmt_hz, log=True)
        self.hi1 = SliderRow("Range 1 high", 20, 20000, 7000, fmt_hz, log=True)
        self.use2 = QCheckBox("Second range")
        self.lo2 = SliderRow("Range 2 low", 20, 20000, 2000, fmt_hz, log=True)
        self.hi2 = SliderRow("Range 2 high", 20, 20000, 6000, fmt_hz, log=True)
        for w in (self.lo1, self.hi1, self.use2, self.lo2, self.hi2):
            lay.addWidget(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Slope"))
        self.slope = QComboBox()
        for v in (12, 24, 48, 96):
            self.slope.addItem(f"{v} dB/oct", v)
        row.addWidget(self.slope, 1)
        lay.addLayout(row)
        self.invert = QCheckBox("Invert (remove the range, keep the rest)")
        lay.addWidget(self.invert)
        lay.addStretch(1)
        out = QVBoxLayout(self)
        out.setContentsMargins(0, 0, 0, 0)
        out.addWidget(_scroll(inner))

        self.en.toggled.connect(self._apply)
        self.preset.currentIndexChanged.connect(self._preset)
        for w in (self.lo1, self.hi1, self.lo2, self.hi2):
            w.valueChanged.connect(self._custom)
        self.use2.toggled.connect(self._custom)
        self.slope.currentIndexChanged.connect(self._apply)
        self.invert.toggled.connect(self._apply)

    def _preset(self, _):
        if self._loading or self.s is None:
            return
        key = self.preset.currentData()
        if key in C.FOCUS_PRESETS:
            lbl, ranges, desc = C.FOCUS_PRESETS[key]
            self._loading = True
            self.lo1.setValue(ranges[0][0])
            self.hi1.setValue(ranges[0][1])
            self.use2.setChecked(len(ranges) > 1)
            if len(ranges) > 1:
                self.lo2.setValue(ranges[1][0])
                self.hi2.setValue(ranges[1][1])
            self.en.setChecked(True)
            self._loading = False
            self.desc.setText(desc)
        self._apply()

    def _custom(self, *_):
        if self._loading:
            return
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(0)
        self.preset.blockSignals(False)
        self.desc.setText("")
        self._apply()

    def _apply(self, *_):
        if self._loading or self.s is None:
            return
        f = self.s.focus
        f.enabled = self.en.isChecked()
        f.preset = self.preset.currentData()
        rs = [C.FocusRange(min(self.lo1.value(), self.hi1.value()), max(self.lo1.value(), self.hi1.value()))]
        if self.use2.isChecked():
            rs.append(C.FocusRange(min(self.lo2.value(), self.hi2.value()), max(self.lo2.value(), self.hi2.value())))
        f.ranges = rs
        f.slope = int(self.slope.currentData())
        f.invert = self.invert.isChecked()
        self.lo2.setEnabled(self.use2.isChecked())
        self.hi2.setEnabled(self.use2.isChecked())
        self.changed.emit()

    def load(self):
        f = self.s.focus
        self.en.setChecked(f.enabled)
        idx = self.preset.findData(f.preset)
        self.preset.setCurrentIndex(idx if idx >= 0 else 0)
        self.desc.setText(C.FOCUS_PRESETS[f.preset][2] if f.preset in C.FOCUS_PRESETS else "")
        r = f.ranges or [C.FocusRange(150, 7000)]
        self.lo1.setValue(r[0].lo)
        self.hi1.setValue(r[0].hi)
        self.use2.setChecked(len(r) > 1)
        if len(r) > 1:
            self.lo2.setValue(r[1].lo)
            self.hi2.setValue(r[1].hi)
        self.lo2.setEnabled(len(r) > 1)
        self.hi2.setEnabled(len(r) > 1)
        self.slope.setCurrentIndex(max(0, self.slope.findData(f.slope)))
        self.invert.setChecked(f.invert)


# ------------------------------------------------------------------ parametric EQ
class EQTab(_Tab):
    bandSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.en = QCheckBox("Enable EQ")
        top.addWidget(self.en, 1)
        add = QPushButton("Add band")
        rem = QPushButton("Remove")
        flat = QPushButton("Reset")
        top.addWidget(add)
        top.addWidget(rem)
        top.addWidget(flat)
        lay.addLayout(top)
        lay.addWidget(_hint("Drag nodes in the analyser. Wheel = Q, double-click empty space = new band, "
                            "right-click a node for type/channel. Hold Shift and drag in the analyser to "
                            "solo a narrow band and find where an instrument sits."))
        self.list = QListWidget()
        self.list.setMaximumHeight(130)
        lay.addWidget(self.list)
        ed = QGroupBox("Selected band")
        el = QVBoxLayout(ed)
        r1 = QHBoxLayout()
        self.type = QComboBox()
        for k, lbl in C.BAND_TYPES:
            self.type.addItem(lbl, k)
        self.chan = QComboBox()
        for k, lbl in C.CHANNELS:
            self.chan.addItem(lbl, k)
        self.chan.setToolTip("Mid/Side bands let you EQ the center and the sides separately "
                             "(for example, boost backing vocals on the sides only).")
        self.on = QCheckBox("On")
        r1.addWidget(self.type, 1)
        r1.addWidget(self.chan, 1)
        r1.addWidget(self.on)
        el.addLayout(r1)
        self.freq = SliderRow("Frequency", 20, 20000, 1000, fmt_hz, log=True)
        self.gain = SliderRow("Gain", -24, 24, 0, lambda v: f"{v:+.1f} dB")
        self.q = SliderRow("Q", 0.1, 20, 1.0, lambda v: f"{v:.2f}", log=True)
        el.addWidget(self.freq)
        el.addWidget(self.gain)
        el.addWidget(self.q)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Cut slope"))
        self.slope = QComboBox()
        for v in (6, 12, 18, 24, 36, 48, 72, 96):
            self.slope.addItem(f"{v} dB/oct", v)
        r2.addWidget(self.slope, 1)
        el.addLayout(r2)
        lay.addWidget(ed)
        lay.addStretch(1)
        self.editor = ed

        self.en.toggled.connect(self._enable)
        add.clicked.connect(self._add)
        rem.clicked.connect(self._remove)
        flat.clicked.connect(self._reset)
        self.list.currentRowChanged.connect(self._row)
        self.type.currentIndexChanged.connect(lambda _: self._edit("type", self.type.currentData()))
        self.chan.currentIndexChanged.connect(lambda _: self._edit("channel", self.chan.currentData()))
        self.on.toggled.connect(lambda v: self._edit("enabled", v))
        self.freq.valueChanged.connect(lambda v: self._edit("freq", v))
        self.gain.valueChanged.connect(lambda v: self._edit("gain", v))
        self.q.valueChanged.connect(lambda v: self._edit("q", v))
        self.slope.currentIndexChanged.connect(lambda _: self._edit("slope", int(self.slope.currentData())))

    def _enable(self, v):
        if self.s is not None and not self._loading:
            self.s.eq_enabled = v
            self.changed.emit()

    def _label(self, i, b):
        t = dict(C.BAND_TYPES)[b.type]
        g = f" {b.gain:+.1f} dB" if b.uses_gain else ""
        ch = "" if b.channel == "stereo" else f" [{dict(C.CHANNELS)[b.channel]}]"
        off = "" if b.enabled else " (off)"
        return f"{i + 1}. {t} {fmt_hz(b.freq)}{g}{ch}{off}"

    def _refresh_list(self):
        cur = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for i, b in enumerate(self.s.eq):
            self.list.addItem(self._label(i, b))
        if self.s.eq:
            self.list.setCurrentRow(min(max(cur, 0), len(self.s.eq) - 1))
        self.list.blockSignals(False)

    def _add(self):
        self.s.eq.append(C.EQBand("bell", 1000.0, 0.0, 1.0))
        self._refresh_list()
        self.select(len(self.s.eq) - 1)
        self.bandSelected.emit(len(self.s.eq) - 1)
        self.changed.emit()

    def _remove(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.s.eq):
            del self.s.eq[i]
            self._refresh_list()
            self.select(min(i, len(self.s.eq) - 1))
            self.bandSelected.emit(self.list.currentRow())
            self.changed.emit()

    def _reset(self):
        self.s.eq = default_eq()
        self.s.eq_enabled = True
        self.set_settings(self.s)
        self.changed.emit()

    def _row(self, i):
        self._load_band(i)
        self.bandSelected.emit(i)

    def select(self, i):
        """Select band i without emitting bandSelected (used by the analyser)."""
        self.list.blockSignals(True)
        self.list.setCurrentRow(i)
        self.list.blockSignals(False)
        self._load_band(i)

    def _load_band(self, i):
        ok = self.s is not None and 0 <= i < len(self.s.eq)
        self.editor.setEnabled(ok)
        if not ok:
            return
        b = self.s.eq[i]
        was = self._loading
        self._loading = True
        self.type.setCurrentIndex(max(0, self.type.findData(b.type)))
        self.chan.setCurrentIndex(max(0, self.chan.findData(b.channel)))
        self.on.setChecked(b.enabled)
        self.freq.setValue(b.freq)
        self.gain.setValue(b.gain)
        self.q.setValue(b.q)
        self.slope.setCurrentIndex(max(0, self.slope.findData(b.slope)))
        self._loading = was
        self._enable_fields(b)

    def _enable_fields(self, b):
        self.gain.setEnabled(b.uses_gain)
        cut = b.type in ("low_cut", "high_cut")
        self.q.setEnabled(not cut)
        self.slope.setEnabled(cut)

    def _edit(self, attr, v):
        if self._loading or self.s is None:
            return
        i = self.list.currentRow()
        if not (0 <= i < len(self.s.eq)):
            return
        b = self.s.eq[i]
        setattr(b, attr, v)
        self._enable_fields(b)
        item = self.list.item(i)
        if item:
            item.setText(self._label(i, b))
        self.changed.emit()

    def refresh_from_settings(self):
        """Called after the analyser edited bands."""
        was = self._loading
        self._loading = True
        self.en.setChecked(self.s.eq_enabled)
        self._refresh_list()
        self._load_band(self.list.currentRow())
        self._loading = was

    def load(self):
        self.en.setChecked(self.s.eq_enabled)
        self._refresh_list()
        self._load_band(self.list.currentRow())


# ------------------------------------------------------------------ graphic EQ
class GraphicTab(_Tab):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.en = QCheckBox("Enable 15-band graphic EQ")
        top.addWidget(self.en, 1)
        flat = QPushButton("Flat")
        top.addWidget(flat)
        lay.addLayout(top)
        grid = QGridLayout()
        grid.setSpacing(2)
        self.sliders = []
        self.vals = []
        for i, f in enumerate(C.GRAPHIC_FREQS):
            s = QSlider(Qt.Vertical)
            s.setRange(-120, 120)
            s.setMinimumHeight(150)
            s.setToolTip(f"{fmt_hz(f)}  (double-click the value to reset)")
            v = QLabel("0")
            v.setAlignment(Qt.AlignCenter)
            v.setObjectName("hint")
            v.mouseDoubleClickEvent = lambda e, sl=s: sl.setValue(0)
            lab = QLabel(f"{f / 1000:g}k" if f >= 1000 else f"{f}")
            lab.setAlignment(Qt.AlignCenter)
            lab.setObjectName("hint")
            grid.addWidget(v, 0, i, Qt.AlignHCenter)
            grid.addWidget(s, 1, i, Qt.AlignHCenter)
            grid.addWidget(lab, 2, i, Qt.AlignHCenter)
            s.valueChanged.connect(lambda val, k=i: self._set(k, val))
            self.sliders.append(s)
            self.vals.append(v)
        lay.addLayout(grid)
        lay.addWidget(_hint("Range +/-12 dB per band, 2/3-octave bells. Stacks on top of the parametric EQ."))
        lay.addStretch(1)
        self.en.toggled.connect(self._en)
        flat.clicked.connect(self._flat)

    def _en(self, v):
        if self.s is not None and not self._loading:
            self.s.graphic_enabled = v
            self.changed.emit()

    def _set(self, k, val):
        self.vals[k].setText(f"{val / 10:+.1f}" if val else "0")
        if self.s is None or self._loading:
            return
        self.s.graphic[k] = val / 10.0
        if val and not self.s.graphic_enabled:
            self.en.setChecked(True)
        self.changed.emit()

    def _flat(self):
        self._loading = True
        for s in self.sliders:
            s.setValue(0)
        self._loading = False
        self.s.graphic = [0.0] * len(C.GRAPHIC_FREQS)
        self.changed.emit()

    def load(self):
        self.en.setChecked(self.s.graphic_enabled)
        for s, v in zip(self.sliders, self.s.graphic):
            s.setValue(int(round(v * 10)))


# ------------------------------------------------------------------ multiband
class MultibandTab(_Tab):
    NAMES = ("Low", "Low mid", "High mid", "High")

    def __init__(self, parent=None):
        super().__init__(parent)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(6)
        self.en = QCheckBox("Enable multiband")
        lay.addWidget(self.en)
        lay.addWidget(_hint("Splits the signal into 4 bands with phase-free crossovers. Level, mute and "
                            "solo work per band; turn on dynamics to compress bands independently."))
        self.xo = [SliderRow(f"Crossover {i + 1}", 30, 16000, d, fmt_hz, log=True)
                   for i, d in enumerate((120, 1000, 5000))]
        for w in self.xo:
            lay.addWidget(w)
            w.valueChanged.connect(self._xo)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        self.cols = []
        for i, name in enumerate(self.NAMES):
            box = QGroupBox(name)
            bl = QVBoxLayout(box)
            gain = SliderRow("Level", -24, 12, 0, lambda v: f"{v:+.1f} dB")
            thr = SliderRow("Threshold", -60, 0, -20, lambda v: f"{v:.0f} dB")
            ratio = SliderRow("Ratio", 1, 20, 1, lambda v: f"{v:.1f}:1", log=True)
            rowb = QHBoxLayout()
            mute = QCheckBox("Mute")
            solo = QCheckBox("Solo")
            gr = QProgressBar()
            gr.setRange(0, 200)
            gr.setTextVisible(False)
            gr.setFixedHeight(8)
            gr.setToolTip("Gain reduction (0 to 20 dB)")
            rowb.addWidget(mute)
            rowb.addWidget(solo)
            rowb.addWidget(QLabel("GR"))
            rowb.addWidget(gr, 1)
            for w in (gain, thr, ratio):
                bl.addWidget(w)
            bl.addLayout(rowb)
            grid.addWidget(box, i, 0)
            gain.valueChanged.connect(lambda v, k=i: self._band(k, "gain", v))
            thr.valueChanged.connect(lambda v, k=i: self._band(k, "threshold", v))
            ratio.valueChanged.connect(lambda v, k=i: self._band(k, "ratio", v))
            mute.toggled.connect(lambda v, k=i: self._band(k, "mute", v))
            solo.toggled.connect(lambda v, k=i: self._band(k, "solo", v))
            self.cols.append(dict(gain=gain, thr=thr, ratio=ratio, mute=mute, solo=solo, gr=gr))
        lay.addLayout(grid)
        dyn = QGroupBox("Dynamics")
        dl = QVBoxLayout(dyn)
        self.dyn = QCheckBox("Enable band compression")
        self.att = SliderRow("Attack", 1, 200, 15, lambda v: f"{v:.0f} ms", log=True)
        self.rel = SliderRow("Release", 20, 1000, 150, lambda v: f"{v:.0f} ms", log=True)
        dl.addWidget(self.dyn)
        dl.addWidget(self.att)
        dl.addWidget(self.rel)
        dl.addWidget(_hint("Detection runs per STFT hop (about 23 ms at 44.1 kHz), so attack times below "
                           "that behave like 23 ms. Good for taming, not for fast transient shaping."))
        lay.addWidget(dyn)
        lay.addStretch(1)
        out = QVBoxLayout(self)
        out.setContentsMargins(0, 0, 0, 0)
        out.addWidget(_scroll(inner))
        self.en.toggled.connect(lambda v: self._mb("enabled", v))
        self.dyn.toggled.connect(lambda v: self._mb("dynamics", v))
        self.att.valueChanged.connect(lambda v: self._mb("attack_ms", v))
        self.rel.valueChanged.connect(lambda v: self._mb("release_ms", v))

    def _mb(self, attr, v):
        if self.s is None or self._loading:
            return
        setattr(self.s.multiband, attr, v)
        self.changed.emit()

    def _xo(self, _):
        if self.s is None or self._loading:
            return
        xs = sorted(w.value() for w in self.xo)
        # keep at least a third of an octave between crossovers
        for i in range(1, 3):
            xs[i] = max(xs[i], xs[i - 1] * 1.26)
        self.s.multiband.crossovers = xs
        if not self.s.multiband.enabled:
            self.en.setChecked(True)
        self.changed.emit()

    def _band(self, k, attr, v):
        if self.s is None or self._loading:
            return
        setattr(self.s.multiband.bands[k], attr, v)
        if not self.s.multiband.enabled:
            self.en.setChecked(True)
        if attr == "ratio" and v > 1.01 and not self.s.multiband.dynamics:
            self.dyn.setChecked(True)
        self.changed.emit()

    def set_gr(self, vals):
        for i, c in enumerate(self.cols):
            v = vals[i] if vals is not None and i < len(vals) else 0.0
            c["gr"].setValue(int(min(20.0, max(0.0, v)) * 10))

    def load(self):
        mb = self.s.multiband
        self.en.setChecked(mb.enabled)
        for w, x in zip(self.xo, mb.crossovers):
            w.setValue(x)
        for c, b in zip(self.cols, mb.bands):
            c["gain"].setValue(b.gain)
            c["thr"].setValue(b.threshold)
            c["ratio"].setValue(b.ratio)
            c["mute"].setChecked(b.mute)
            c["solo"].setChecked(b.solo)
        self.dyn.setChecked(mb.dynamics)
        self.att.setValue(mb.attack_ms)
        self.rel.setValue(mb.release_ms)


# ------------------------------------------------------------------ output
class OutputTab(_Tab):
    def __init__(self, parent=None):
        super().__init__(parent)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(6)
        self.gain = SliderRow("Output gain", -24, 24, 0, lambda v: f"{v:+.1f} dB")
        self.bal = SliderRow("Balance", -1, 1, 0, fmt_pan)
        self.mono = QCheckBox("Mono")
        for w in (self.gain, self.bal, self.mono):
            lay.addWidget(w)
        g = QGroupBox("Tape saturation")
        gl = QVBoxLayout(g)
        self.drive = SliderRow("Drive", 0, 1, 0, lambda v: f"{v * 100:.0f}%")
        gl.addWidget(self.drive)
        lay.addWidget(g)
        c = QGroupBox("Compressor")
        cl = QVBoxLayout(c)
        self.comp = QCheckBox("Enable")
        self.thr = SliderRow("Threshold", -60, 0, -18, lambda v: f"{v:.1f} dB")
        self.ratio = SliderRow("Ratio", 1, 20, 3, lambda v: f"{v:.1f}:1", log=True)
        self.att = SliderRow("Attack", 1, 200, 10, lambda v: f"{v:.0f} ms", log=True)
        self.rel = SliderRow("Release", 20, 1000, 120, lambda v: f"{v:.0f} ms", log=True)
        self.mk = SliderRow("Makeup", 0, 24, 0, lambda v: f"{v:+.1f} dB")
        grr = QHBoxLayout()
        grr.addWidget(QLabel("Gain reduction"))
        self.gr = QProgressBar()
        self.gr.setRange(0, 200)
        self.gr.setTextVisible(False)
        self.gr.setFixedHeight(8)
        grr.addWidget(self.gr, 1)
        for w in (self.comp, self.thr, self.ratio, self.att, self.rel, self.mk):
            cl.addWidget(w)
        cl.addLayout(grr)
        lay.addWidget(c)
        lm = QGroupBox("Limiter")
        ll = QVBoxLayout(lm)
        self.lim = QCheckBox("Enable (prevents clipping when boosting)")
        self.ceil = SliderRow("Ceiling", -12, 0, -0.3, lambda v: f"{v:.1f} dB")
        ll.addWidget(self.lim)
        ll.addWidget(self.ceil)
        lay.addWidget(lm)
        lay.addStretch(1)
        out = QVBoxLayout(self)
        out.setContentsMargins(0, 0, 0, 0)
        out.addWidget(_scroll(inner))
        for w, a in ((self.gain, "gain_db"), (self.bal, "balance"), (self.drive, "drive"),
                     (self.thr, "comp_threshold"), (self.ratio, "comp_ratio"), (self.att, "comp_attack_ms"),
                     (self.rel, "comp_release_ms"), (self.mk, "comp_makeup_db"), (self.ceil, "ceiling_db")):
            w.valueChanged.connect(lambda v, attr=a: self._set(attr, v))
        self.mono.toggled.connect(lambda v: self._set("mono", v))
        self.comp.toggled.connect(lambda v: self._set("comp_enabled", v))
        self.lim.toggled.connect(lambda v: self._set("limiter", v))

    def _set(self, attr, v):
        if self.s is None or self._loading:
            return
        setattr(self.s.output, attr, v)
        self.changed.emit()

    def set_gr(self, v):
        self.gr.setValue(int(min(20.0, max(0.0, v or 0.0)) * 10))

    def load(self):
        o = self.s.output
        self.gain.setValue(o.gain_db)
        self.bal.setValue(o.balance)
        self.mono.setChecked(o.mono)
        self.drive.setValue(o.drive)
        self.comp.setChecked(o.comp_enabled)
        self.thr.setValue(o.comp_threshold)
        self.ratio.setValue(o.comp_ratio)
        self.att.setValue(o.comp_attack_ms)
        self.rel.setValue(o.comp_release_ms)
        self.mk.setValue(o.comp_makeup_db)
        self.lim.setChecked(o.limiter)
        self.ceil.setValue(o.ceiling_db)


# ------------------------------------------------------------------ container
class ChainPanel(QTabWidget):
    changed = Signal()
    eqBandSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.spatial = SpatialTab()
        self.focus = FocusTab()
        self.eq = EQTab()
        self.graphic = GraphicTab()
        self.multiband = MultibandTab()
        self.output = OutputTab()
        for w, name, tip in ((self.spatial, "Spatial", "Center / side / pan isolation"),
                             (self.focus, "Focus", "Instrument frequency focus"),
                             (self.eq, "EQ", "Parametric EQ with mid/side bands"),
                             (self.graphic, "Graphic", "15-band graphic EQ"),
                             (self.multiband, "Multiband", "4-band level and dynamics"),
                             (self.output, "Output", "Gain, saturation, compressor, limiter")):
            i = self.addTab(w, name)
            self.setTabToolTip(i, tip)
            w.changed.connect(self.changed)
        self.eq.bandSelected.connect(self.eqBandSelected)
        self.setMinimumWidth(340)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

    def tabs(self):
        return (self.spatial, self.focus, self.eq, self.graphic, self.multiband, self.output)

    def set_settings(self, s: ChainSettings):
        for t in self.tabs():
            t.set_settings(s)
