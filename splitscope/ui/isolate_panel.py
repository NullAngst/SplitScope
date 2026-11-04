"""One-click isolation buttons."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
                               QCheckBox, QGridLayout)

from .. import models as M
from ..separation import TARGETS

AI_TARGETS = {"vocals": "vocals", "instrumental": "vocals", "lead_vocal": "karaoke",
              "backing_vocals": "karaoke", "drums": "drums", "bass": "bass", "other": "other"}


class IsolatePanel(QWidget):
    isolateRequested = Signal(str)       # target key
    modelsRequested = Signal()
    dspSettingsRequested = Signal()
    dspOnlyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QLabel("Isolate")
        head.setObjectName("heading")
        lay.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("Source"))
        self.source = QComboBox()
        self.source.setToolTip("What gets separated: the original file, or a stem you already made.")
        row.addWidget(self.source, 1)
        lay.addLayout(row)

        grid = QGridLayout()
        grid.setSpacing(5)
        self.buttons: dict[str, QPushButton] = {}
        for i, (key, label, tip) in enumerate(TARGETS):
            b = QPushButton(label)
            b.setObjectName("primary" if key == "vocals" else "isolate")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.isolateRequested.emit(k))
            if key == "vocals":
                grid.addWidget(b, 0, 0, 1, 2)
            else:
                r, c = divmod(i + 1, 2)
                grid.addWidget(b, r, c)
            self.buttons[key] = b
        lay.addLayout(grid)

        self.denoise = QCheckBox("High quality (2x slower)")
        self.denoise.setToolTip("Runs each AI model twice with inverted phase and averages the result.\n"
                                "Removes some model noise. Doubles processing time.")
        lay.addWidget(self.denoise)

        drow = QHBoxLayout()
        self.dsp_only = QCheckBox("DSP only (no AI)")
        self.dsp_only.setToolTip("Every button uses its model-free method, even when models are installed.\n"
                                 "Use it to compare, or to work entirely without AI.")
        self.dsp_only.toggled.connect(self._dsp_toggled)
        drow.addWidget(self.dsp_only, 1)
        ds = QPushButton("DSP settings")
        ds.setToolTip("Tune the model-free methods: repetition, center weighting, vocal band, HPSS")
        ds.clicked.connect(self.dspSettingsRequested)
        drow.addWidget(ds)
        lay.addLayout(drow)

        mrow = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        mrow.addWidget(self.status, 1)
        mb = QPushButton("Models")
        mb.setToolTip("Download, remove or import AI separation models")
        mb.clicked.connect(self.modelsRequested)
        mrow.addWidget(mb)
        lay.addLayout(mrow)
        self.refresh_models()

    def _dsp_toggled(self, on: bool):
        self.dspOnlyChanged.emit(on)
        self.refresh_models()

    def refresh_models(self):
        dsp_only = self.dsp_only.isChecked() if hasattr(self, "dsp_only") else False
        have = {r: (not dsp_only) and M.first_installed(r) is not None for r in M.ROLES}
        missing = [r for r in M.ROLES if not have[r]]
        for key, btn in self.buttons.items():
            role = AI_TARGETS.get(key)
            base = next(lbl for k, lbl, _ in TARGETS if k == key)
            dsp = role is not None and not have[role]
            btn.setText(base)
            btn.setProperty("dsp", "true" if dsp else "false")
            tip = next(t for k, _, t in TARGETS if k == key)
            if dsp and dsp_only:
                tip += "\n\nDSP-only mode: uses the model-free method."
            elif dsp:
                tip += "\n\nNo model installed for this: uses the model-free method (lower quality than a model)."
            btn.setToolTip(tip)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if dsp_only:
            self.status.setText("DSP-only mode: no models are used. Dashed buttons run their model-free method.")
        elif not missing:
            self.status.setText("All AI models installed.")
        elif len(missing) == len(M.ROLES):
            self.status.setText("No AI models installed. Dashed buttons use model-free methods "
                                "(lower quality than models). Open Models to download.")
        else:
            self.status.setText("Missing models: " + ", ".join(missing) + ". Dashed buttons fall back to DSP.")

    def set_sources(self, stems, keep_id=None):
        cur = keep_id or self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        for s in stems:
            self.source.addItem(s.name, s.id)
        idx = self.source.findData(cur)
        self.source.setCurrentIndex(idx if idx >= 0 else 0)
        self.source.blockSignals(False)

    def source_id(self):
        return self.source.currentData()

    def set_busy(self, busy: bool):
        for b in self.buttons.values():
            b.setEnabled(not busy)
        self.source.setEnabled(not busy)
        self.dsp_only.setEnabled(not busy)
