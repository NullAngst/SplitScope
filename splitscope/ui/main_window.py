"""SplitScope main window."""
from __future__ import annotations

import json
import os
import re
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, QSettings, QSize, QRectF
from PySide6.QtGui import QActionGroup, QKeySequence, QPainter, QColor
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QSplitter, QToolBar, QLabel, QPushButton,
                               QDoubleSpinBox, QCheckBox, QSlider, QStatusBar, QProgressBar, QFileDialog,
                               QMessageBox, QFrame)

from .. import __version__
from .. import audio_io
from .. import models as M
from ..dsp.chain import ChainSettings, compile_params
from ..dsp.mdx import Cancelled
from ..dsp.processor import ArraySource, render_offline
from ..engine import AudioEngine, SD_ERROR, list_output_devices, sd
from ..separation import Separator, TARGETS
from . import theme as T
from .chain_panels import ChainPanel
from .dialogs import ExportDialog, ModelDialog
from .isolate_panel import IsolatePanel
from .jobs import Job
from .spectrogram_view import SpectrogramView
from .spectrum_view import SpectrumView
from .stems_panel import StemsPanel
from .waveform_view import WaveformView, fmt_time


def _safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', "_", s).strip(" .")
    return s or "stem"


class LevelMeter(QWidget):
    """Two thin horizontal peak bars (L, R), -60..0 dBFS, with slow fall."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(90, 18)
        self.vals = np.array([-90.0, -90.0])
        self.hold = np.array([-90.0, -90.0])
        self.setToolTip("Output peak level (after the limiter, before monitor volume)")

    def push(self, peak):
        db = 20 * np.log10(np.maximum(np.asarray(peak, dtype=np.float64), 1e-6))
        self.vals = np.maximum(db, self.vals - 1.5)
        self.hold = np.maximum(db, self.hold - 0.4)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        w = self.width()
        for i in range(2):
            y = 2 + i * 8
            p.fillRect(QRectF(0, y, w, 6), QColor(T.LINE))
            t = float(np.clip((self.vals[i] + 60) / 60, 0, 1))
            col = QColor(T.BAD) if self.vals[i] > -0.5 else QColor(T.WARN) if self.vals[i] > -6 else QColor(T.ACCENT)
            p.fillRect(QRectF(0, y, w * t, 6), col)
            th = float(np.clip((self.hold[i] + 60) / 60, 0, 1))
            p.fillRect(QRectF(w * th - 1, y, 2, 6), QColor(T.TEXT))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SplitScope")
        self.resize(1480, 900)
        self.setAcceptDrops(True)
        self.qs = QSettings("SplitScope", "SplitScope")
        self.engine = AudioEngine()
        self.sep = Separator()
        try:
            self.sep.preferred = json.loads(self.qs.value("preferred_models", "{}") or "{}")
        except ValueError:
            self.sep.preferred = {}
        self.job: Job | None = None
        self.file_path = None
        self.derived: dict[tuple, str] = {}     # (source stem id, stem name) -> stem id
        self._static_dirty = False
        self._static_t = 0.0
        self._loop_on = False

        self._build_ui()
        self._build_menus()
        self._restore()
        self._set_project_enabled(False)
        self.chain.set_settings(self.engine.settings)
        self.spectrum.set_settings(self.engine.settings, self.engine.sr)
        self._sync_transport()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)
        if sd is None:
            self.status.showMessage("Audio output unavailable (PortAudio not found). Separation, analysis and "
                                    "export still work. Details: " + str(SD_ERROR)[:200])

    # ================================================================ UI
    def _build_ui(self):
        tb = QToolBar("Transport")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        self.b_open = QPushButton("Open")
        self.b_open.setToolTip("Open an audio file (Ctrl+O). You can also drag a file onto the window.")
        self.b_export = QPushButton("Export")
        self.b_export.setToolTip("Export the mix, a stem or all stems (Ctrl+E)")
        self.b_play = QPushButton("Play")
        self.b_play.setObjectName("primary")
        self.b_play.setMinimumWidth(70)
        self.b_play.setToolTip("Play / pause (Space)")
        self.b_home = QPushButton("To start")
        self.b_home.setToolTip("Return to start or loop start (Home)")
        self.b_loop = QPushButton("Loop")
        self.b_loop.setCheckable(True)
        self.b_loop.setToolTip("Loop the selected region (L). Drag in the waveform to select a region, "
                               "right-click it to clear.")
        self.b_bypass = QPushButton("Bypass")
        self.b_bypass.setCheckable(True)
        self.b_bypass.setToolTip("A/B: hear the stems without any processing (B)")
        for w in (self.b_open, self.b_export):
            w.setFocusPolicy(Qt.NoFocus)
            tb.addWidget(w)
        tb.addSeparator()
        for w in (self.b_play, self.b_home, self.b_loop):
            w.setFocusPolicy(Qt.NoFocus)
            tb.addWidget(w)
        self.time = QLabel("0:00.0 / 0:00.0")
        self.time.setObjectName("time")
        self.time.setMinimumWidth(190)
        self.time.setAlignment(Qt.AlignCenter)
        tb.addWidget(self.time)
        tb.addSeparator()
        self.b_bypass.setFocusPolicy(Qt.NoFocus)
        tb.addWidget(self.b_bypass)
        tb.addSeparator()
        tb.addWidget(QLabel(" Speed "))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.25, 2.0)
        self.speed.setSingleStep(0.05)
        self.speed.setDecimals(2)
        self.speed.setSuffix("x")
        self.speed.setValue(1.0)
        self.speed.setToolTip("Playback speed. Also applied to exports if you choose so in the Export dialog.")
        tb.addWidget(self.speed)
        self.pitch = QCheckBox("Keep pitch")
        self.pitch.setChecked(True)
        self.pitch.setToolTip("On: phase-vocoder time stretch (pitch unchanged, some smearing).\n"
                              "Off: varispeed like a tape machine (pitch follows speed, no artifacts).")
        self.pitch.setFocusPolicy(Qt.NoFocus)
        tb.addWidget(self.pitch)
        tb.addSeparator()
        tb.addWidget(QLabel(" Volume "))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(110)
        self.vol.setToolTip("Monitor volume. Only affects what you hear, never exports.")
        self.vol.setFocusPolicy(Qt.NoFocus)
        tb.addWidget(self.vol)
        self.meter = LevelMeter()
        tb.addWidget(self.meter)

        # left column
        left = QWidget()
        left.setObjectName("panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 8, 10, 10)
        self.isolate = IsolatePanel()
        self.stems = StemsPanel()
        ll.addWidget(self.isolate)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color:{T.LINE};")
        ll.addWidget(line)
        ll.addWidget(self.stems, 1)
        left.setMinimumWidth(270)

        # center column
        self.spectrum = SpectrumView()
        self.spectrogram = SpectrogramView()
        self.wave = WaveformView()
        self.center_split = QSplitter(Qt.Vertical)
        for w in (self.spectrum, self.spectrogram, self.wave):
            self.center_split.addWidget(w)
        self.center_split.setStretchFactor(0, 6)
        self.center_split.setStretchFactor(1, 2)
        self.center_split.setStretchFactor(2, 2)
        self.center_split.setSizes([520, 140, 150])

        # right column
        self.chain = ChainPanel()

        self.main_split = QSplitter(Qt.Horizontal)
        self.main_split.addWidget(left)
        self.main_split.addWidget(self.center_split)
        self.main_split.addWidget(self.chain)
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setStretchFactor(2, 0)
        self.main_split.setSizes([300, 760, 410])
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(6, 6, 6, 6)
        wl.addWidget(self.main_split)
        self.setCentralWidget(wrap)

        # status
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 1000)
        self.pbar.setFixedWidth(260)
        self.pbar.setVisible(False)
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.setVisible(False)
        self.b_cancel.clicked.connect(self._cancel_job)
        self.status.addPermanentWidget(self.pbar)
        self.status.addPermanentWidget(self.b_cancel)

        # wiring
        self.b_open.clicked.connect(self.open_dialog)
        self.b_export.clicked.connect(lambda: self.export_dialog())
        self.b_play.clicked.connect(self.toggle_play)
        self.b_home.clicked.connect(self.to_start)
        self.b_loop.toggled.connect(self.set_loop_enabled)
        self.b_bypass.toggled.connect(self._bypass)
        self.speed.valueChanged.connect(self._speed)
        self.pitch.toggled.connect(self._speed)
        self.vol.valueChanged.connect(self._vol)
        self.isolate.isolateRequested.connect(self.isolate_target)
        self.isolate.modelsRequested.connect(self.models_dialog)
        self.isolate.denoise.toggled.connect(lambda v: setattr(self.sep, "denoise", v))
        self.stems.mixChanged.connect(self._mix_changed)
        self.stems.stemAction.connect(self._stem_action)
        self.stems.selectionChanged.connect(self._stem_selected)
        self.chain.changed.connect(self._chain_changed)
        self.chain.eqBandSelected.connect(self.spectrum.select)
        self.spectrum.eqChanged.connect(self._spectrum_eq)
        self.spectrum.bandSelected.connect(self._spectrum_band)
        self.spectrum.auditionChanged.connect(self._audition)
        self.wave.seekRequested.connect(self.seek)
        self.wave.loopChanged.connect(self._loop_changed)
        self._vol(self.vol.value())

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("File")
        self.a_open = f.addAction("Open audio...", self.open_dialog, QKeySequence.Open)
        self.a_export = f.addAction("Export...", lambda: self.export_dialog(), QKeySequence("Ctrl+E"))
        f.addSeparator()
        f.addAction("Save chain preset...", self.save_preset)
        f.addAction("Load chain preset...", self.load_preset)
        f.addAction("Reset processing chain", self.reset_chain)
        f.addSeparator()
        f.addAction("Quit", self.close, QKeySequence.Quit)

        pb = mb.addMenu("Playback")
        a = pb.addAction("Play / pause", self.toggle_play, QKeySequence(Qt.Key_Space))
        a.setShortcutContext(Qt.WindowShortcut)
        pb.addAction("Return to start", self.to_start, QKeySequence(Qt.Key_Home))
        pb.addAction("Toggle loop", lambda: self.b_loop.toggle(), QKeySequence(Qt.Key_L))
        pb.addAction("Clear loop region", lambda: self._loop_changed(0, 0))
        pb.addAction("Toggle bypass (A/B)", lambda: self.b_bypass.toggle(), QKeySequence(Qt.Key_B))
        pb.addSeparator()
        self.dev_menu = pb.addMenu("Output device")
        self.dev_menu.aboutToShow.connect(self._fill_devices)

        sm = mb.addMenu("Separate")
        for key, label, tip in TARGETS:
            act = sm.addAction(label, lambda k=key: self.isolate_target(k))
            act.setToolTip(tip)
        sm.addSeparator()
        self.run_menu = sm.addMenu("Run a specific model")
        self.run_menu.aboutToShow.connect(self._fill_run_menu)
        sm.addAction("Render processing chain to new stem", self.render_chain_to_stem)
        sm.addSeparator()
        sm.addAction("Clear separation cache", self._clear_cache)
        sm.setToolTipsVisible(True)

        mm = mb.addMenu("Models")
        mm.addAction("Manage models...", self.models_dialog)
        mm.addAction("Open models folder", lambda: self._open_folder(M.user_models_dir()))

        vm = mb.addMenu("View")
        self.a_pre = vm.addAction("Show input spectrum (before chain)")
        self.a_peak = vm.addAction("Peak hold")
        self.a_guide = vm.addAction("Instrument range guide")
        self.a_sgram = vm.addAction("Spectrogram")
        for a, attr in ((self.a_pre, "show_pre"), (self.a_peak, "show_peak"), (self.a_guide, "show_guide")):
            a.setCheckable(True)
            a.setChecked(getattr(self.spectrum, attr))
            a.toggled.connect(lambda v, at=attr: self._view_flag(at, v))
        self.a_sgram.setCheckable(True)
        self.a_sgram.setChecked(True)
        self.a_sgram.toggled.connect(self.spectrogram.setVisible)
        rm = vm.addMenu("Analyser response")
        grp = QActionGroup(self)
        for k in ("fast", "medium", "slow"):
            a = rm.addAction(k.capitalize())
            a.setCheckable(True)
            a.setChecked(self.spectrum.response == k)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, kk=k: self._response(kk))

        hm = mb.addMenu("Help")
        hm.addAction("Quick guide", self.quick_guide, QKeySequence.HelpContents)
        hm.addAction("About SplitScope", self.about)

    # ================================================================ settings
    def _restore(self):
        g = self.qs.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
        for key, sp in (("main_split", self.main_split), ("center_split", self.center_split)):
            st = self.qs.value(key)
            if st is not None:
                sp.restoreState(st)
        self.vol.setValue(int(self.qs.value("volume", 80)))
        self.isolate.denoise.setChecked(self.qs.value("denoise", "false") in ("true", True))
        for a, key in ((self.a_pre, "show_pre"), (self.a_peak, "show_peak"), (self.a_guide, "show_guide"),
                       (self.a_sgram, "show_sgram")):
            v = self.qs.value(key)
            if v is not None:
                a.setChecked(v in ("true", True))
        resp = self.qs.value("response")
        if resp in ("fast", "medium", "slow"):
            self.spectrum.response = resp

    def closeEvent(self, ev):
        if self.job is not None:
            self.job.cancel()
            self.job.wait(5000)
        self.engine.stop_stream()
        self.qs.setValue("geometry", self.saveGeometry())
        self.qs.setValue("main_split", self.main_split.saveState())
        self.qs.setValue("center_split", self.center_split.saveState())
        self.qs.setValue("volume", self.vol.value())
        self.qs.setValue("denoise", self.isolate.denoise.isChecked())
        self.qs.setValue("preferred_models", json.dumps(self.sep.preferred))
        self.qs.setValue("show_pre", self.a_pre.isChecked())
        self.qs.setValue("show_peak", self.a_peak.isChecked())
        self.qs.setValue("show_guide", self.a_guide.isChecked())
        self.qs.setValue("show_sgram", self.a_sgram.isChecked())
        self.qs.setValue("response", self.spectrum.response)
        super().closeEvent(ev)

    # ================================================================ files
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for u in ev.mimeData().urls():
            if u.isLocalFile():
                self.open_file(u.toLocalFile())
                return

    def open_dialog(self):
        if self.job is not None:
            return
        d = self.qs.value("last_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Open audio", d, audio_io.IMPORT_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path):
        if self.job is not None:
            self.status.showMessage("Wait for the current job to finish (or cancel it) before opening a file.", 5000)
            return
        self.qs.setValue("last_dir", os.path.dirname(path))
        self._start_job(f"Loading {os.path.basename(path)}", lambda prog, canc: audio_io.load(path),
                        lambda la: self._loaded(la), indeterminate=True)

    def _loaded(self, la):
        self.engine.set_project(la.audio, la.sr, os.path.splitext(os.path.basename(la.path))[0])
        self.file_path = la.path
        self.sep.clear()
        self.derived.clear()
        self._loop_on = False
        self.b_loop.blockSignals(True)
        self.b_loop.setChecked(False)
        self.b_loop.blockSignals(False)
        self.wave.set_loop(0, 0, False)
        self.spectrum.set_settings(self.engine.settings, la.sr)
        self.spectrum.has_audio = True
        self.spectrum.reset_display()
        self.spectrogram.set_sr(la.sr)
        self.spectrogram.clear()
        self._rebuild_stems()
        self.stems.select(self.engine.stems[0])
        self._set_project_enabled(True)
        mono = " (mono file, duplicated to both channels: center/side tools have nothing to separate)" \
            if la.channels_in_file == 1 else ""
        self.setWindowTitle(f"{os.path.basename(la.path)} - SplitScope")
        self.status.showMessage(f"Loaded {os.path.basename(la.path)}: {la.sr} Hz, "
                                f"{fmt_time(la.audio.shape[0] / la.sr)}{mono}", 10000)
        self._static_dirty = True

    def _set_project_enabled(self, on):
        for w in (self.b_export, self.b_play, self.b_home, self.b_loop):
            w.setEnabled(on)
        if sd is None:
            self.b_play.setEnabled(False)
            self.b_play.setToolTip("Audio output unavailable: " + str(SD_ERROR)[:200])
        self.isolate.set_busy(not on or self.job is not None)
        self.a_export.setEnabled(on)

    # ================================================================ jobs
    def _start_job(self, title, fn, on_done, indeterminate=False):
        job = Job(title, fn, self)
        self.job = job
        self.pbar.setVisible(True)
        self.pbar.setRange(0, 0 if indeterminate else 1000)
        self.pbar.setValue(0)
        self.pbar.setFormat(title)
        self.b_cancel.setVisible(not indeterminate)
        self.isolate.set_busy(True)
        self.b_open.setEnabled(False)
        self.b_export.setEnabled(False)
        self.status.showMessage(title + "...")
        t0 = time.monotonic()

        def prog(f, label):
            if self.job is not job:
                return
            if not indeterminate:
                self.pbar.setValue(int(f * 1000))
            el = time.monotonic() - t0
            eta = ""
            if f > 0.03 and not indeterminate:
                eta = f", about {fmt_time(el / f - el).split('.')[0]} left"
            self.status.showMessage(f"{label or title}: {f * 100:.0f}%{eta}")

        def finish():
            self.job = None
            self.pbar.setVisible(False)
            self.b_cancel.setVisible(False)
            self.b_open.setEnabled(True)
            self._set_project_enabled(bool(self.engine.stems))

        def ok(res):
            finish()
            try:
                on_done(res)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"{title} finished but applying the result failed:\n{e}")

        def fail(err):
            finish()
            self.status.showMessage(f"{title} failed.", 8000)
            QMessageBox.warning(self, "Error", f"{title} failed:\n\n{err}")

        def canc():
            finish()
            self.status.showMessage(f"{title} cancelled.", 5000)

        job.progress.connect(prog)
        job.finishedOk.connect(ok)
        job.failed.connect(fail)
        job.wasCancelled.connect(canc)
        job.start()

    def _cancel_job(self):
        if self.job is not None:
            self.job.cancel()
            self.status.showMessage("Cancelling...")

    # ================================================================ separation
    def _source_stem(self):
        sid = self.isolate.source_id()
        for s in self.engine.stems:
            if s.id == sid:
                return s
        return self.engine.stems[0] if self.engine.stems else None

    def isolate_target(self, key):
        if self.job is not None or not self.engine.stems:
            return
        src = self._source_stem()
        label = next(lbl for k, lbl, _ in TARGETS if k == key)
        audio, sr, sid = src.audio, self.engine.sr, src.id
        self._start_job(label, lambda prog, canc: self.sep.run(key, sid, audio, sr, prog, canc),
                        lambda res: self._apply_result(res, src))

    def _run_specific(self, m):
        if self.job is not None or not self.engine.stems:
            return
        src = self._source_stem()
        audio, sr, sid = src.audio, self.engine.sr, src.id
        self._start_job(m.title, lambda prog, canc: self.sep.run_model(m, sid, audio, sr, prog, canc),
                        lambda res: self._apply_result(res, src))

    def _apply_result(self, res, src):
        prefix = "" if src.original else f"{src.name}: "
        solo_stem = None
        for name, audio in res.stems:
            full = prefix + name
            key = (src.id, full)
            existing = None
            if key in self.derived:
                existing = next((s for s in self.engine.stems if s.id == self.derived[key]), None)
            if existing is None:
                existing = self.engine.add_stem(full, audio)
                self.derived[key] = existing.id
            if name == res.solo:
                solo_stem = existing
        if solo_stem is not None:
            for s in self.engine.stems:
                s.solo = s is solo_stem
                if s is solo_stem:
                    s.mute = False
        self.engine.update_mix()
        self._rebuild_stems()
        if solo_stem is not None:
            self.stems.select(solo_stem)
        self._static_dirty = True
        if res.notes:
            self.status.showMessage(" ".join(res.notes), 30000)
        else:
            self.status.showMessage(f"Done. '{solo_stem.name if solo_stem else ''}' is soloed. "
                                    "Export it from its ... menu or with Export.", 12000)

    def _rebuild_stems(self):
        self.stems.rebuild(self.engine.stems)
        self.isolate.set_sources(self.engine.stems)

    def _clear_cache(self):
        self.sep.clear()
        self.derived.clear()
        self.status.showMessage("Separation cache cleared. The next isolate run recomputes from scratch.", 5000)

    def _fill_run_menu(self):
        self.run_menu.clear()
        inst = [m for m in M.all_models() if M.is_installed(m)]
        if not inst:
            a = self.run_menu.addAction("No models installed")
            a.setEnabled(False)
            return
        for m in inst:
            a = self.run_menu.addAction(f"{m.title}  ({m.params.primary_stem} / {m.secondary})",
                                        lambda mm=m: self._run_specific(mm))
            a.setEnabled(self.job is None and bool(self.engine.stems))

    def models_dialog(self):
        dlg = ModelDialog(self, self.sep.preferred)
        dlg.modelsChanged.connect(self._models_changed)
        dlg.exec()
        self._models_changed()

    def _models_changed(self):
        self.isolate.refresh_models()
        self.qs.setValue("preferred_models", json.dumps(self.sep.preferred))

    def render_chain_to_stem(self):
        if self.job is not None or not self.engine.stems:
            return
        mix = self.engine.mix
        s = self.engine.settings.copy()
        s.speed = 1.0
        s.bypass = False
        params = compile_params(s, self.engine.sr)
        sr = self.engine.sr
        n = sum(1 for st in self.engine.stems if st.name.startswith("Processed")) + 1

        def work(prog, canc):
            return render_offline(mix, params, sr, 0, mix.length, lambda f: prog(f, "Rendering"), canc)

        def done(audio):
            st = self.engine.add_stem(f"Processed {n}", audio)
            for x in self.engine.stems:
                x.solo = x is st
            self.engine.update_mix()
            self.reset_chain(confirm=False)
            self._rebuild_stems()
            self.stems.select(st)
            self.status.showMessage(f"Rendered to 'Processed {n}' and reset the chain so it is not applied twice. "
                                    "You can now separate or process this stem further.", 15000)

        self._start_job("Rendering chain", work, done)

    # ================================================================ stems
    def _mix_changed(self):
        self.engine.update_mix()
        self._static_dirty = True

    def _stem_selected(self, st):
        if st is not None:
            self.wave.set_audio(st.audio, self.engine.sr, st.color)

    def _stem_action(self, kind, st):
        if kind == "export":
            self.export_dialog(preset="stem", stem=st)
        elif kind == "source":
            self.isolate.set_sources(self.engine.stems, keep_id=st.id)
            self.status.showMessage(f"Isolate buttons now work on '{st.name}'.", 6000)
        elif kind == "renamed":
            self.isolate.set_sources(self.engine.stems)
        elif kind == "delete":
            if st.original:
                return
            self.engine.stems = [s for s in self.engine.stems if s is not st]
            self.sep.clear(st.id)
            self.derived = {k: v for k, v in self.derived.items() if v != st.id and k[0] != st.id}
            self.engine.update_mix()
            self._rebuild_stems()
            self.stems.select(self.engine.stems[0])
            self._static_dirty = True

    # ================================================================ chain
    def _chain_changed(self):
        self.engine.update_params()
        self.spectrum.invalidate_curves()
        self._static_dirty = True

    def _spectrum_eq(self):
        self.engine.update_params()
        self.chain.eq.refresh_from_settings()
        self._static_dirty = True

    def _spectrum_band(self, i):
        self.chain.setCurrentWidget(self.chain.eq)
        self.chain.eq.select(i)

    def _audition(self, a):
        self.engine.audition = a
        self.engine.update_params()
        self._static_dirty = True

    def _bypass(self, on):
        self.engine.settings.bypass = on
        self.engine.update_params()
        self.spectrum.invalidate_curves()
        self._static_dirty = True
        self.b_bypass.setText("Bypassed" if on else "Bypass")

    def _speed(self, *_):
        self.engine.settings.speed = float(self.speed.value())
        self.engine.settings.preserve_pitch = self.pitch.isChecked()
        self.engine.update_params()

    def _vol(self, v):
        # 80% = unity; 0.5 dB per step below (down to -40 dB), 0.3 dB per step above (up to +6 dB)
        db = (v - 80) * 0.5 if v <= 80 else (v - 80) * 0.3
        self.engine.monitor = 0.0 if v <= 0 else float(10 ** (db / 20))

    def reset_chain(self, confirm=True):
        if confirm and QMessageBox.question(self, "Reset chain",
                                            "Reset every processing setting to default?") != QMessageBox.Yes:
            return
        s = ChainSettings()
        s.speed = self.engine.settings.speed
        s.preserve_pitch = self.engine.settings.preserve_pitch
        self._set_chain(s)

    def _set_chain(self, s: ChainSettings):
        s.bypass = self.b_bypass.isChecked()
        self.engine.settings = s
        self.engine.update_params()
        self.chain.set_settings(s)
        self.spectrum.set_settings(s, self.engine.sr)
        self.speed.blockSignals(True)
        self.speed.setValue(s.speed)
        self.speed.blockSignals(False)
        self.pitch.blockSignals(True)
        self.pitch.setChecked(s.preserve_pitch)
        self.pitch.blockSignals(False)
        self._static_dirty = True

    def save_preset(self):
        d = self.qs.value("preset_dir", str(M.user_data_dir()))
        path, _ = QFileDialog.getSaveFileName(self, "Save chain preset", d, "SplitScope preset (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        s = self.engine.settings.copy()
        s.bypass = False
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"splitscope_preset": 1, "chain": s.to_dict()}, fh, indent=1)
        self.qs.setValue("preset_dir", os.path.dirname(path))
        self.status.showMessage(f"Saved preset {os.path.basename(path)}", 5000)

    def load_preset(self):
        d = self.qs.value("preset_dir", str(M.user_data_dir()))
        path, _ = QFileDialog.getOpenFileName(self, "Load chain preset", d, "SplitScope preset (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            s = ChainSettings.from_dict(data.get("chain", data))
        except (OSError, ValueError, AttributeError) as e:
            QMessageBox.warning(self, "Load preset", f"Could not read preset:\n{e}")
            return
        self.qs.setValue("preset_dir", os.path.dirname(path))
        self._set_chain(s)
        self.status.showMessage(f"Loaded preset {os.path.basename(path)}", 5000)

    # ================================================================ export
    def export_dialog(self, preset=None, stem=None):
        if not self.engine.stems or self.job is not None:
            return
        stem = stem or self.stems.selected or self.engine.stems[0]
        lo, hi = self.engine.loop
        has_loop = self._loop_on and hi - lo > self.engine.sr // 10
        dlg = ExportDialog(self, stem.name, has_loop, self.engine.settings.speed, self.qs.value("export_fmt"))
        if preset:
            dlg.radios[preset].setChecked(True)
        if not dlg.exec():
            return
        spec = dlg.spec()
        self.qs.setValue("export_fmt", spec.fmt)
        ext = audio_io.EXPORT_FORMATS[spec.fmt][0]
        base = os.path.splitext(os.path.basename(self.file_path or "export"))[0]
        outdir = self.qs.value("export_dir", os.path.dirname(self.file_path or "") or os.path.expanduser("~"))
        sr = self.engine.sr
        start, end = (lo, hi) if spec.loop_only else (0, self.engine.length)

        s = self.engine.settings.copy()
        s.bypass = False
        if not spec.apply_speed:
            s.speed = 1.0
        params = compile_params(s, sr)
        stems = [st for st in self.engine.stems]

        if spec.what in ("mix", "stem"):
            name = f"{base} - {'processed' if spec.what == 'mix' else stem.name}"
            path, _ = QFileDialog.getSaveFileName(self, "Export", os.path.join(outdir, _safe_name(name) + "." + ext),
                                                  f"{spec.fmt} (*.{ext})")
            if not path:
                return
            path = audio_io.ensure_ext(path, spec.fmt)
            self.qs.setValue("export_dir", os.path.dirname(path))
            mix = self.engine.mix
            if spec.what == "mix" and not mix.parts:
                QMessageBox.information(self, "Export", "Every stem is muted, so there is nothing to export.")
                return

            def work(prog, canc):
                if spec.what == "mix":
                    audio = render_offline(mix, params, sr, start, end, lambda f: prog(f * 0.95, "Rendering"), canc)
                else:
                    audio = stem.audio[start:end]
                prog(0.97, "Writing file")
                audio_io.save(path, audio, sr, spec.fmt, spec.normalize)
                return path

            self._start_job("Exporting", work, lambda p: self.status.showMessage(f"Exported {p}", 10000))
            return

        folder = QFileDialog.getExistingDirectory(self, "Export stems to folder", outdir)
        if not folder:
            return
        self.qs.setValue("export_dir", folder)
        through = spec.what == "stems_chain"

        def work_all(prog, canc):
            written = []
            n = len(stems)
            for i, st in enumerate(stems):
                if canc():
                    raise Cancelled()
                label = f"{st.name} ({i + 1}/{n})"
                if through:
                    audio = render_offline(ArraySource(st.audio), params, sr, start, end,
                                           lambda f, i=i: prog((i + f * 0.95) / n, label), canc)
                else:
                    audio = st.audio[start:end]
                p = os.path.join(folder, _safe_name(f"{base} - {st.name}") + "." + ext)
                audio_io.save(p, audio, sr, spec.fmt, spec.normalize)
                written.append(p)
                prog((i + 1) / n, label)
            return written

        self._start_job("Exporting stems", work_all,
                        lambda w: self.status.showMessage(f"Exported {len(w)} files to {folder}", 10000))

    # ================================================================ transport
    def toggle_play(self):
        if not self.engine.stems:
            return
        if self.engine.playing:
            self.engine.pause()
            self._static_dirty = True
        else:
            try:
                self.engine.play()
            except Exception as e:
                QMessageBox.warning(self, "Playback", f"Could not start audio output:\n{e}")
                self.engine.stop_stream()
        self._sync_transport()

    def to_start(self):
        self.seek(self.engine.loop[0] if self._loop_on else 0)

    def seek(self, pos):
        self.engine.seek(int(pos))
        self.wave.set_position(pos)
        self._static_dirty = True

    def set_loop_enabled(self, on):
        lo, hi = self.engine.loop
        if on and hi - lo < self.engine.sr // 10:
            self.status.showMessage("Drag across the waveform to select a loop region first.", 5000)
            self.b_loop.blockSignals(True)
            self.b_loop.setChecked(False)
            self.b_loop.blockSignals(False)
            on = False
        self._loop_on = on
        self.engine.loop_enabled = on
        self.wave.set_loop(lo, hi, on)

    def _loop_changed(self, a, b):
        if b > a:
            self.engine.loop = (int(a), int(b))
            self._loop_on = True
            self.engine.loop_enabled = True
            self.b_loop.blockSignals(True)
            self.b_loop.setChecked(True)
            self.b_loop.blockSignals(False)
            self.wave.set_loop(a, b, True)
        else:
            self.engine.loop = (0, self.engine.length)
            self._loop_on = False
            self.engine.loop_enabled = False
            self.b_loop.blockSignals(True)
            self.b_loop.setChecked(False)
            self.b_loop.blockSignals(False)
            self.wave.set_loop(0, 0, False)

    def _sync_transport(self):
        self.b_play.setText("Pause" if self.engine.playing else "Play")

    def _fill_devices(self):
        self.dev_menu.clear()
        grp = QActionGroup(self.dev_menu)
        a = self.dev_menu.addAction("System default")
        a.setCheckable(True)
        a.setChecked(self.engine.device is None)
        a.triggered.connect(lambda: self._device(None))
        grp.addAction(a)
        devs = list_output_devices()
        if not devs:
            x = self.dev_menu.addAction("No output devices found" if sd is not None else "Audio output unavailable")
            x.setEnabled(False)
        for idx, name in devs:
            a = self.dev_menu.addAction(name)
            a.setCheckable(True)
            a.setChecked(self.engine.device == idx)
            a.triggered.connect(lambda _=False, i=idx: self._device(i))
            grp.addAction(a)

    def _device(self, idx):
        try:
            self.engine.set_device(idx)
        except Exception as e:
            QMessageBox.warning(self, "Output device", f"Could not open that device:\n{e}")
            self.engine.stop_stream()
        self._sync_transport()

    # ================================================================ view
    def _view_flag(self, attr, v):
        setattr(self.spectrum, attr, v)
        if attr == "show_peak" and not v:
            self.spectrum._peak = None
        self.spectrum.update()

    def _response(self, k):
        self.spectrum.response = k

    def _open_folder(self, path):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ================================================================ tick
    def _tick(self):
        e = self.engine
        if e.last_error:
            self.status.showMessage("Audio callback error: " + e.last_error, 10000)
            e.last_error = None
        if not e.stems:
            return
        pos = e.position()
        self.wave.set_position(pos)
        self.time.setText(f"{fmt_time(pos / e.sr)} / {fmt_time(e.length / e.sr)}")
        if e.playing:
            if e.ended:
                e.pause()
                e.seek(0)
                self._sync_transport()
                self._static_dirty = True
                return
            fr = e.pull_spectrum()
            if fr is not None:
                _, pre, post, gr = fr
                if post is not None:
                    pre_db = e.chain.spectrum_db(pre)
                    post_db = e.chain.spectrum_db(post)
                    self.spectrum.set_frame(pre_db, post_db)
                    if self.spectrogram.isVisible():
                        self.spectrogram.push(post_db)
                self._meters(gr)
            self.meter.push(e.peak)
        else:
            self.meter.push([0.0, 0.0])
            now = time.monotonic()
            if self._static_dirty and now - self._static_t > 0.08:
                self._static_dirty = False
                self._static_t = now
                try:
                    _, pre, post, gr = e.analyse_static(int(pos))
                    if post is not None:
                        self.spectrum.set_static(e.chain.spectrum_db(pre), e.chain.spectrum_db(post))
                except Exception as ex:  # analysis must never take the UI down
                    self.status.showMessage(f"Analysis error: {ex}", 5000)

    def _meters(self, gr):
        p = self.engine.params
        mb = None
        comp = 0.0
        if gr:
            if p.mb_dyn:
                mb = gr[:4]
            if p.comp:
                comp = gr[-1]
        self.chain.multiband.set_gr(mb)
        self.chain.output.set_gr(comp)

    # ================================================================ help
    def quick_guide(self):
        QMessageBox.information(self, "Quick guide", GUIDE)

    def about(self):
        QMessageBox.about(self, "About SplitScope", ABOUT.format(version=__version__))


GUIDE = """Open a file (Ctrl+O or drag it onto the window).

Isolate: the buttons on the left create new stems and solo the result. Vocals, drums, bass and \
guitars/keys use AI models when installed (Models button); otherwise they fall back to DSP and are \
marked (DSP). Center channel / Side channels work on stereo position and need no model.

Stems: M mutes, S solos (Ctrl+click S to solo only that stem). The ... menu exports a stem or makes \
it the source for further separation, for example Isolate vocals, then Backing vocals.

Analyser: the prism line is the output after processing. Drag EQ nodes to boost or cut. Hold Shift \
(or the middle button) and drag across the analyser to hear only a narrow band; sweep it to find \
where a guitar solo or vocal sits, then release and put an EQ node there.

Right panel: Spatial = center/side/pan isolation in real time. Focus = instrument frequency ranges. \
EQ = parametric bands, each on stereo, mid, side, left or right. Graphic = 15-band EQ. Multiband = \
4-band level and compression. Output = gain, balance, mono, saturation, compressor, limiter.

Everything you hear is exactly what Export > What you hear writes.

Keys: Space play/pause, Home to start, L loop, B bypass, Ctrl+E export."""

ABOUT = """<b>SplitScope {version}</b><br>
Audio separation workstation.<br><br>
License: GPL-3.0-or-later.<br>
Separation models: MDX-Net networks from the Ultimate Vocal Remover project and KUIELab, run with \
ONNX Runtime. Model weights belong to their authors.<br>
Built with PySide6 (Qt), NumPy, SciPy, soundfile (libsndfile) and sounddevice (PortAudio)."""
