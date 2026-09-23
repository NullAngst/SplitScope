from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QSlider, QMenu,
                               QInputDialog, QFrame, QScrollArea, QSizePolicy)

from . import theme as T


class StemRow(QFrame):
    changed = Signal()
    clicked = Signal(object)
    action = Signal(str, object)

    def __init__(self, stem, parent=None):
        super().__init__(parent)
        self.stem = stem
        self.setObjectName("panel")
        self.setFrameShape(QFrame.NoFrame)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        sw = QLabel()
        sw.setFixedSize(10, 10)
        sw.setStyleSheet(f"background:{stem.color}; border-radius:5px; border:none;")
        top.addWidget(sw)
        self.name = QLabel(stem.name)
        self.name.setStyleSheet("border:none; background:transparent; font-weight:600;")
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name.setToolTip(stem.name)
        top.addWidget(self.name, 1)
        self.m = QToolButton()
        self.m.setText("M")
        self.m.setObjectName("mute")
        self.m.setCheckable(True)
        self.m.setChecked(stem.mute)
        self.m.setToolTip("Mute")
        self.s = QToolButton()
        self.s.setText("S")
        self.s.setObjectName("solo")
        self.s.setCheckable(True)
        self.s.setChecked(stem.solo)
        self.s.setToolTip("Solo (Ctrl+click to solo only this stem)")
        more = QToolButton()
        more.setText("...")
        more.setToolTip("Export, rename, delete")
        more.clicked.connect(self._menu)
        for b in (self.m, self.s, more):
            b.setFixedWidth(28)
            top.addWidget(b)
        lay.addLayout(top)
        bot = QHBoxLayout()
        self.gain = QSlider(Qt.Horizontal)
        self.gain.setRange(-480, 120)
        self.gain.setValue(int(stem.gain_db * 10))
        self.gain.setToolTip("Stem level. Double-click the value to reset.")
        self.val = QLabel(self._gtxt())
        self.val.setFixedWidth(52)
        self.val.setStyleSheet("border:none; background:transparent;")
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bot.addWidget(self.gain, 1)
        bot.addWidget(self.val)
        lay.addLayout(bot)
        self.m.toggled.connect(self._mute)
        self.s.clicked.connect(self._solo)
        self.gain.valueChanged.connect(self._gain)
        self.val.mouseDoubleClickEvent = lambda e: self.gain.setValue(0)

    def _gtxt(self):
        return f"{self.stem.gain_db:+.1f} dB"

    def _mute(self, on):
        self.stem.mute = on
        self.changed.emit()

    def _solo(self, _=False):
        from PySide6.QtWidgets import QApplication

        exclusive = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        self.action.emit("solo_exclusive" if exclusive else "solo", self.stem)

    def _gain(self, v):
        self.stem.gain_db = v / 10.0
        self.val.setText(self._gtxt())
        self.changed.emit()

    def _menu(self):
        m = QMenu(self)
        m.addAction("Export stem...", lambda: self.action.emit("export", self.stem))
        m.addAction("Separate this stem further", lambda: self.action.emit("source", self.stem))
        m.addAction("Rename...", self._rename)
        m.addSeparator()
        d = m.addAction("Delete stem", lambda: self.action.emit("delete", self.stem))
        d.setEnabled(not self.stem.original)
        m.exec(self.mapToGlobal(self.rect().bottomRight()))

    def _rename(self):
        name, ok = QInputDialog.getText(self, "Rename stem", "Name:", text=self.stem.name)
        if ok and name.strip():
            self.stem.name = name.strip()
            self.name.setText(self.stem.name)
            self.name.setToolTip(self.stem.name)
            self.action.emit("renamed", self.stem)

    def mousePressEvent(self, ev):
        self.clicked.emit(self.stem)
        super().mousePressEvent(ev)

    def set_selected(self, on):
        self.setStyleSheet(f"QFrame#panel {{ border-color: {T.ACCENT if on else T.LINE}; }}")


class StemsPanel(QWidget):
    mixChanged = Signal()
    stemAction = Signal(str, object)
    selectionChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QLabel("Stems")
        head.setObjectName("heading")
        lay.addWidget(head)
        hint = QLabel("Solo a stem to hear it alone. Everything you hear goes through the chain on the right.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.vbox = QVBoxLayout(self.inner)
        self.vbox.setContentsMargins(0, 0, 4, 0)
        self.vbox.setSpacing(6)
        self.vbox.addStretch(1)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.rows: list[StemRow] = []
        self.selected = None

    def rebuild(self, stems):
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for st in stems:
            row = StemRow(st)
            row.changed.connect(self.mixChanged)
            row.action.connect(self._on_action)
            row.clicked.connect(self.select)
            self.vbox.insertWidget(self.vbox.count() - 1, row)
            self.rows.append(row)
        if self.selected not in stems:
            self.selected = stems[0] if stems else None
        self._paint_sel()

    def select(self, stem):
        self.selected = stem
        self._paint_sel()
        self.selectionChanged.emit(stem)

    def _paint_sel(self):
        for r in self.rows:
            r.set_selected(r.stem is self.selected)

    def _on_action(self, kind, stem):
        if kind in ("solo", "solo_exclusive"):
            if kind == "solo_exclusive":
                for r in self.rows:
                    r.stem.solo = r.stem is stem
            else:
                stem.solo = not stem.solo
            self.sync()
            self.mixChanged.emit()
            return
        self.stemAction.emit(kind, stem)

    def sync(self):
        for r in self.rows:
            r.s.blockSignals(True)
            r.m.blockSignals(True)
            r.s.setChecked(r.stem.solo)
            r.m.setChecked(r.stem.mute)
            r.s.blockSignals(False)
            r.m.blockSignals(False)
