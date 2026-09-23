"""Export options and model manager dialogs."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal, QUrl
from PySide6.QtGui import QDesktopServices, QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QCheckBox,
                               QPushButton, QRadioButton, QButtonGroup, QGroupBox, QTableWidget,
                               QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar, QFileDialog,
                               QMessageBox, QInputDialog, QDialogButtonBox)

from .. import audio_io
from .. import models as M
from .jobs import Job

RECOMMENDED = ["Kim_Vocal_2", "UVR_MDXNET_KARA_2", "kuielab_a_drums", "kuielab_a_bass", "kuielab_a_other"]


# ------------------------------------------------------------------ export
@dataclass
class ExportSpec:
    what: str            # mix | stem | stems_raw | stems_chain
    fmt: str
    normalize: bool
    loop_only: bool
    apply_speed: bool


class ExportDialog(QDialog):
    WHAT = [
        ("mix", "What you hear (audible stems through the processing chain)"),
        ("stem", "Selected stem only, unprocessed"),
        ("stems_raw", "Every stem as its own file, unprocessed"),
        ("stems_chain", "Every stem as its own file, each through the processing chain"),
    ]

    def __init__(self, parent, selected_name: str, has_loop: bool, speed: float, last_fmt: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        g = QGroupBox("What to export")
        gl = QVBoxLayout(g)
        self.group = QButtonGroup(self)
        self.radios = {}
        for i, (k, lbl) in enumerate(self.WHAT):
            if k == "stem":
                lbl = f"Selected stem only, unprocessed ({selected_name})"
            r = QRadioButton(lbl)
            self.group.addButton(r, i)
            gl.addWidget(r)
            self.radios[k] = r
        self.radios["mix"].setChecked(True)
        lay.addWidget(g)

        grid = QGridLayout()
        grid.addWidget(QLabel("Format"), 0, 0)
        self.fmt = QComboBox()
        self.fmt.addItems(list(audio_io.EXPORT_FORMATS))
        if last_fmt in audio_io.EXPORT_FORMATS:
            self.fmt.setCurrentText(last_fmt)
        else:
            self.fmt.setCurrentText("WAV 24-bit")
        grid.addWidget(self.fmt, 0, 1)
        lay.addLayout(grid)
        self.loop = QCheckBox("Loop region only")
        self.loop.setEnabled(has_loop)
        self.loop.setChecked(has_loop)
        self.norm = QCheckBox("Normalize peak to -1 dBFS")
        self.speed = QCheckBox(f"Apply playback speed ({speed:.2f}x) to processed exports")
        self.speed.setVisible(abs(speed - 1.0) > 1e-3)
        self.speed.setChecked(False)
        for w in (self.loop, self.norm, self.speed):
            lay.addWidget(w)
        note = QLabel("Unprocessed exports are bit-identical to the stem (no EQ, no limiter). "
                      "MP3 and OGG are lossy; use WAV or FLAC for anything you will process further.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export...")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def spec(self) -> ExportSpec:
        what = self.WHAT[self.group.checkedId()][0]
        return ExportSpec(what, self.fmt.currentText(), self.norm.isChecked(),
                          self.loop.isChecked() and self.loop.isEnabled(),
                          self.speed.isChecked() and self.speed.isVisible())


# ------------------------------------------------------------------ models
class ModelDialog(QDialog):
    modelsChanged = Signal()

    def __init__(self, parent, preferred: dict):
        super().__init__(parent)
        self.setWindowTitle("AI separation models")
        self.resize(820, 520)
        self.preferred = preferred
        self.job: Job | None = None
        lay = QVBoxLayout(self)
        info = QLabel(
            "Models are MDX-Net ONNX networks from the Ultimate Vocal Remover project. They run on the CPU, "
            "fully offline, once present. Downloads come from the UVR model repository on GitHub and are "
            "stored in your user data folder. You can also drop .onnx files into a 'models' folder next "
            "to the program (portable mode).")
        info.setWordWrap(True)
        info.setObjectName("hint")
        lay.addWidget(info)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Model", "Role", "Size", "Status", "Description"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.table.horizontalHeader()
        for i in range(4):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.b_dl = QPushButton("Download")
        self.b_all = QPushButton("Download recommended")
        self.b_all.setToolTip("Kim Vocal 2, Karaoke 2, and the KUIELab drums, bass and other models (about 210 MB)")
        self.b_rm = QPushButton("Remove")
        self.b_imp = QPushButton("Import .onnx...")
        self.b_dir = QPushButton("Open models folder")
        for b in (self.b_dl, self.b_all, self.b_rm, self.b_imp, self.b_dir):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)

        prog = QHBoxLayout()
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 1000)
        self.plabel = QLabel("")
        self.plabel.setObjectName("hint")
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.setEnabled(False)
        prog.addWidget(self.plabel)
        prog.addWidget(self.pbar, 1)
        prog.addWidget(self.b_cancel)
        lay.addLayout(prog)

        pref = QGroupBox("Model used by each Isolate button")
        pl = QGridLayout(pref)
        self.pref_combos = {}
        for i, role in enumerate(M.ROLES):
            pl.addWidget(QLabel(role.capitalize()), i // 3, (i % 3) * 2)
            cb = QComboBox()
            cb.currentIndexChanged.connect(lambda _, r=role: self._pref(r))
            pl.addWidget(cb, i // 3, (i % 3) * 2 + 1)
            self.pref_combos[role] = cb
        lay.addWidget(pref)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.b_dl.clicked.connect(self._download_selected)
        self.b_all.clicked.connect(self._download_recommended)
        self.b_rm.clicked.connect(self._remove)
        self.b_imp.clicked.connect(self._import)
        self.b_dir.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(M.user_models_dir()))))
        self.b_cancel.clicked.connect(lambda: self.job and self.job.cancel())
        self.table.itemSelectionChanged.connect(self._sel)
        self.refresh()

    # -------------------------------------------------------------- table
    def refresh(self):
        self._models = M.all_models()
        self.table.setRowCount(len(self._models))
        for r, m in enumerate(self._models):
            size = f"{m.size / 1e6:.0f} MB" if m.size else ""
            cells = [m.title, m.role, size, M.location_label(m), m.description]
            for c, t in enumerate(cells):
                it = QTableWidgetItem(t)
                if c == 3 and t == "Not installed":
                    it.setForeground(QColor("#8a93a6"))
                self.table.setItem(r, c, it)
        for role, cb in self.pref_combos.items():
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("Automatic", "")
            for m in M.models_for_role(role):
                cb.addItem(m.title, m.key)
            idx = cb.findData(self.preferred.get(role, ""))
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)
        self._sel()

    def _current(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._models[rows[0].row()]

    def _sel(self):
        m = self._current()
        busy = self.job is not None
        self.b_dl.setEnabled(not busy and m is not None and m.role != "custom" and not M.is_installed(m))
        loc = M.location_label(m) if m else ""
        self.b_rm.setEnabled(not busy and m is not None and (loc == "Downloaded" or m.role == "custom"))
        self.b_all.setEnabled(not busy)
        self.b_imp.setEnabled(not busy)

    def _pref(self, role):
        key = self.pref_combos[role].currentData()
        if key:
            self.preferred[role] = key
        else:
            self.preferred.pop(role, None)
        self.modelsChanged.emit()

    # -------------------------------------------------------------- actions
    def _download_selected(self):
        m = self._current()
        if m is not None:
            self._download([m])

    def _download_recommended(self):
        todo = [M.BY_KEY[k] for k in RECOMMENDED if not M.is_installed(M.BY_KEY[k])]
        if not todo:
            QMessageBox.information(self, "Models", "All recommended models are already installed.")
            return
        self._download(todo)

    def _download(self, todo):
        total = sum(m.size for m in todo) or 1

        def work(progress, cancelled):
            done = 0
            for m in todo:
                M.download(m, lambda f, m=m, d=done: progress((d + f * m.size) / total, m.title), cancelled)
                done += m.size
            return len(todo)

        self._run(Job("Downloading", work, self))

    def _run(self, job: Job):
        self.job = job
        self.b_cancel.setEnabled(True)
        job.progress.connect(lambda f, s: (self.pbar.setValue(int(f * 1000)), self.plabel.setText(s)))
        job.finishedOk.connect(lambda _: self._done("Done."))
        job.failed.connect(lambda e: self._done("Failed.", e))
        job.wasCancelled.connect(lambda: self._done("Cancelled."))
        self._sel()
        job.start()

    def _done(self, text, err=None):
        self.job = None
        self.b_cancel.setEnabled(False)
        self.plabel.setText(text)
        self.pbar.setValue(0 if err else self.pbar.value())
        self.refresh()
        self.modelsChanged.emit()
        if err:
            QMessageBox.warning(self, "Download failed",
                                "Could not download the model. Check your connection, or download the .onnx "
                                "file manually and put it in the models folder.\n\n" + err.split("\n\n")[0])

    def _remove(self):
        m = self._current()
        if m is None:
            return
        if QMessageBox.question(self, "Remove model", f"Delete {m.title} from disk?") != QMessageBox.Yes:
            return
        if m.role == "custom":
            M.remove_custom_model(m.key)
        else:
            M.delete_download(m)
        for role, key in list(self.preferred.items()):
            if key == m.key:
                del self.preferred[role]
        self.refresh()
        self.modelsChanged.emit()

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import MDX-Net model", "", "ONNX models (*.onnx)")
        if not path:
            return
        try:
            known = M.lookup_uvr_params(path)
            if known:
                n_fft = int(known["mdx_n_fft_scale_set"])
                primary = known.get("primary_stem", "Primary")
                comp = float(known.get("compensate", 1.0))
            else:
                n_fft, ok = QInputDialog.getInt(
                    self, "Model settings",
                    "This model is not in the UVR table, so its FFT size is unknown.\n"
                    "Enter n_fft (UVR's 'N_FFT scale'; common values 6144, 7680, 4096, 8192).\n"
                    "A wrong value produces garbage output, not a crash.", 6144, 1024, 32768, 512)
                if not ok:
                    return
                primary, ok = QInputDialog.getText(self, "Model settings", "Name of the stem this model extracts:",
                                                   text="Vocals")
                if not ok:
                    return
                comp = 1.0
            secondary = "Instrumental" if primary.lower().startswith("vocal") else f"No {primary.lower()}"
            M.register_custom_model(path, n_fft, primary, secondary, comp)
        except Exception as e:
            QMessageBox.warning(self, "Import failed", f"Could not import this model:\n{e}")
            return
        self.refresh()
        self.modelsChanged.emit()

    def reject(self):
        if self.job is not None:
            self.job.cancel()
            self.job.wait(3000)
        super().reject()
