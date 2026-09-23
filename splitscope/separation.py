"""One-click isolation recipes.

Each recipe returns a list of (stem name, audio) plus the name that should
be soloed. Results of expensive steps are cached per source so, for example,
"Lead vocal" after "Isolate vocals" does not rerun the vocal model.

AI models are used when installed; otherwise the recipe falls back to DSP
and says so in the returned note.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from . import models as M
from .dsp import dsp_separate as D
from .dsp.mdx import separate_file_audio

Progress = Callable[[float, str], None]

TARGETS = [
    # key, button label, tooltip
    ("vocals", "Isolate vocals", "AI vocal model. Produces Vocals + Instrumental and solos Vocals."),
    ("instrumental", "Remove vocals", "AI vocal model. Solos the Instrumental (karaoke track)."),
    ("lead_vocal", "Lead vocal", "Vocals, then the Karaoke model (or center extraction) to keep the lead."),
    ("backing_vocals", "Backing vocals", "Vocals, then Karaoke model (or side extraction). Backing vocals are usually panned wide."),
    ("drums", "Isolate drums", "AI drum model, or harmonic/percussive split without it."),
    ("bass", "Isolate bass", "AI bass model, or low-passed harmonic content without it."),
    ("other", "Guitars / keys", "AI 'other' model: guitars, keys, synths. Without it: instrumental minus drums and bass."),
    ("center", "Center channel", "Spectral center extraction (lead vocal, kick, snare, bass usually live here)."),
    ("sides", "Side channels", "Everything that is not center: wide guitars, backing vocals, reverb."),
    ("mid_side", "Mid / side split", "Plain matrix split: Mid = (L+R)/2, Side = (L-R)/2."),
    ("harm_perc", "Harmonic / perc.", "Median-filter split of sustained vs transient sound."),
]


@dataclass
class RecipeResult:
    stems: list  # [(name, audio)]
    solo: Optional[str]
    notes: list = field(default_factory=list)


class Separator:
    def __init__(self):
        self.cache: dict[tuple, dict[str, np.ndarray]] = {}
        self.denoise = False
        self.preferred: dict[str, str] = {}

    def clear(self, source_id=None):
        if source_id is None:
            self.cache.clear()
        else:
            for k in [k for k in self.cache if k[0] == source_id]:
                del self.cache[k]

    # --------------------------------------------------------- building blocks
    def _model(self, role: str) -> Optional[M.ModelInfo]:
        return M.first_installed(role, self.preferred.get(role))

    def _run_model(self, m: M.ModelInfo, audio, sr, progress, cancelled, label):
        path = M.find_model_file(m.file)
        cb = (lambda f: progress(f, label)) if progress else None
        return separate_file_audio(audio, sr, str(path), m.params, denoise=self.denoise,
                                   progress=cb, cancelled=cancelled)

    def vocals(self, sid, audio, sr, progress, cancelled, notes):
        key = (sid, "vocals")
        if key in self.cache:
            return self.cache[key]
        m = self._model("vocals")
        if m is None:
            notes.append("No vocal model installed: used center extraction + vocal-range focus instead. "
                         "Install Kim Vocal 2 in Models for real separation.")
            center, _ = D.center_sides(audio, sr, progress=(lambda f: progress(f, "Center extraction")) if progress else None,
                                       cancelled=cancelled)
            from .dsp.chain import ChainSettings, compile_params
            from .dsp.processor import ArraySource, render_offline
            s = ChainSettings()
            s.eq_enabled = False
            s.output.limiter = False
            s.focus.enabled = True
            s.focus.ranges[0].lo, s.focus.ranges[0].hi = 120.0, 9000.0
            voc = render_offline(ArraySource(center), compile_params(s, sr), sr)
            res = {"Vocals (DSP estimate)": voc, "Instrumental (DSP estimate)": (audio - voc).astype(np.float32)}
            res["_voc"], res["_inst"] = "Vocals (DSP estimate)", "Instrumental (DSP estimate)"
        else:
            primary, secondary = self._run_model(m, audio, sr, progress, cancelled, f"{m.title}")
            if m.params.primary_stem.lower().startswith("instrument"):
                voc, inst = secondary, primary
            else:
                voc, inst = primary, secondary
            res = {"Vocals": voc, "Instrumental": inst, "_voc": "Vocals", "_inst": "Instrumental"}
        self.cache[key] = res
        return res

    def lead_backing(self, sid, audio, sr, progress, cancelled, notes):
        key = (sid, "lead_backing")
        if key in self.cache:
            return self.cache[key]
        v = self.vocals(sid, audio, sr, progress, cancelled, notes)
        voc = v[v["_voc"]]
        m = self._model("karaoke")
        if m is None:
            notes.append("No karaoke model installed: split lead/backing by stereo position "
                         "(center = lead, sides = backing).")
            lead, back = D.center_sides(voc, sr, strict=0.7,
                                        progress=(lambda f: progress(f, "Center extraction")) if progress else None,
                                        cancelled=cancelled)
        else:
            back, lead = self._run_model(m, voc, sr, progress, cancelled, m.title)
        res = {"Lead vocal": lead, "Backing vocals": back}
        self.cache[key] = res
        return res

    def ai_stem(self, role, sid, audio, sr, progress, cancelled):
        key = (sid, role)
        if key in self.cache:
            return self.cache[key]
        m = self._model(role)
        if m is None:
            return None
        primary, secondary = self._run_model(m, audio, sr, progress, cancelled, m.title)
        res = {m.params.primary_stem: primary, "_name": m.params.primary_stem}
        self.cache[key] = res
        return res

    def hpss(self, sid, audio, sr, progress, cancelled):
        key = (sid, "hpss")
        if key in self.cache:
            return self.cache[key]
        h, p = D.hpss(audio, sr, progress=(lambda f: progress(f, "Harmonic/percussive split")) if progress else None,
                      cancelled=cancelled)
        res = {"Harmonic": h, "Percussive": p}
        self.cache[key] = res
        return res

    # --------------------------------------------------------- recipes
    def run(self, target: str, sid, audio: np.ndarray, sr: int,
            progress: Optional[Progress] = None, cancelled=None) -> RecipeResult:
        notes: list[str] = []
        prog = progress or (lambda f, s: None)

        if target in ("vocals", "instrumental"):
            v = self.vocals(sid, audio, sr, prog, cancelled, notes)
            stems = [(v["_voc"], v[v["_voc"]]), (v["_inst"], v[v["_inst"]])]
            return RecipeResult(stems, v["_voc"] if target == "vocals" else v["_inst"], notes)

        if target in ("lead_vocal", "backing_vocals"):
            lb = self.lead_backing(sid, audio, sr, prog, cancelled, notes)
            stems = [("Lead vocal", lb["Lead vocal"]), ("Backing vocals", lb["Backing vocals"])]
            return RecipeResult(stems, "Lead vocal" if target == "lead_vocal" else "Backing vocals", notes)

        if target == "drums":
            r = self.ai_stem("drums", sid, audio, sr, prog, cancelled)
            if r is not None:
                return RecipeResult([(r["_name"], r[r["_name"]])], r["_name"], notes)
            notes.append("No drum model installed: used harmonic/percussive separation. "
                         "Transients from other instruments will be included.")
            base = audio
            if self._model("vocals") is not None:
                base = self.vocals(sid, audio, sr, prog, cancelled, notes)["Instrumental"]
            h, p = D.hpss(base, sr, margin=2.0, progress=lambda f: prog(f, "Harmonic/percussive split"),
                          cancelled=cancelled)
            return RecipeResult([("Drums (percussive estimate)", p)], "Drums (percussive estimate)", notes)

        if target == "bass":
            r = self.ai_stem("bass", sid, audio, sr, prog, cancelled)
            if r is not None:
                return RecipeResult([(r["_name"], r[r["_name"]])], r["_name"], notes)
            notes.append("No bass model installed: used low-passed harmonic content (kick drum body may bleed in).")
            hp = self.hpss(sid, audio, sr, prog, cancelled)
            low, _ = D.split_low(hp["Harmonic"], sr, 220.0)
            return RecipeResult([("Bass (low-pass estimate)", low)], "Bass (low-pass estimate)", notes)

        if target == "other":
            r = self.ai_stem("other", sid, audio, sr, prog, cancelled)
            if r is not None:
                return RecipeResult([(r["_name"], r[r["_name"]])], r["_name"], notes)
            notes.append("No 'other' model installed: estimated as harmonic, non-vocal content above the bass range.")
            base = audio
            if self._model("vocals") is not None:
                base = self.vocals(sid, audio, sr, prog, cancelled, notes)["Instrumental"]
            h, p = D.hpss(base, sr, progress=lambda f: prog(f, "Harmonic/percussive split"), cancelled=cancelled)
            _, rest = D.split_low(h, sr, 180.0)
            return RecipeResult([("Other (estimate)", rest)], "Other (estimate)", notes)

        if target in ("center", "sides"):
            key = (sid, "center_sides")
            if key not in self.cache:
                c, s = D.center_sides(audio, sr, progress=lambda f: prog(f, "Center extraction"), cancelled=cancelled)
                self.cache[key] = {"Center": c, "Sides": s}
            cs = self.cache[key]
            return RecipeResult([("Center", cs["Center"]), ("Sides", cs["Sides"])],
                                "Center" if target == "center" else "Sides", notes)

        if target == "mid_side":
            m, s = D.mid_side(audio)
            return RecipeResult([("Mid (L+R)", m), ("Side (L-R)", s)], "Mid (L+R)", notes)

        if target == "harm_perc":
            hp = self.hpss(sid, audio, sr, prog, cancelled)
            return RecipeResult([("Harmonic", hp["Harmonic"]), ("Percussive", hp["Percussive"])], "Harmonic", notes)

        raise ValueError(f"Unknown target {target}")

    def run_model(self, m: M.ModelInfo, sid, audio, sr, progress=None, cancelled=None) -> RecipeResult:
        prog = progress or (lambda f, s: None)
        primary, secondary = self._run_model(m, audio, sr, prog, cancelled, m.title)
        return RecipeResult([(m.params.primary_stem, primary), (m.secondary, secondary)], m.params.primary_stem, [])
