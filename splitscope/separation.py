"""One-click isolation recipes.

Each recipe returns a list of (stem name, audio) plus the name that should
be soloed. Results of expensive steps are cached per source, per model and
per DSP settings, so "Lead vocal" after "Isolate vocals" does not rerun the
vocal step, but changing a DSP setting or the preferred model does.

AI models are used when installed, unless DSP-only mode is on. Without a
model every recipe has a model-free path (see dsp/dsp_separate.py), and the
returned notes say which one ran.
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
    ("vocals", "Isolate vocals", "AI vocal model, or the DSP vocal estimate (repetition + center + band + "
                                 "transient cues). Produces Vocals + Instrumental and solos Vocals."),
    ("instrumental", "Remove vocals", "Same split as Isolate vocals. Solos the Instrumental (karaoke track)."),
    ("lead_vocal", "Lead vocal", "Vocals, then the Karaoke model (or center extraction) to keep the lead."),
    ("backing_vocals", "Backing vocals", "Vocals, then Karaoke model (or side extraction). Backing vocals are usually panned wide."),
    ("drums", "Isolate drums", "AI drum model, or a percussive split with a margin (clear hits only) without it."),
    ("bass", "Isolate bass", "AI bass model, or sustained low center content from a long-window split."),
    ("other", "Guitars / keys", "AI 'other' model: guitars, keys, synths. Without it: sustained, non-vocal content above the bass."),
    ("center", "Center channel", "Spectral center extraction (lead vocal, kick, snare, bass usually live here)."),
    ("sides", "Side channels", "Everything that is not center: wide guitars, backing vocals, reverb."),
    ("repeating", "Repeating / lead", "REPET-SIM, no model. Whatever repeats through the song (loops, riffs, "
                                      "backing) goes to Repeating; what does not (lead vocal, solos, fills) goes to Lead."),
    ("multistage", "Sustain / voice / hits", "Two-pass median filtering, no model. A long window pulls out steady "
                                             "instruments, a short one splits the rest into voice/leads and drums."),
    ("mid_side", "Mid / side split", "Plain matrix split: Mid = (L+R)/2, Side = (L-R)/2."),
    ("harm_perc", "Harmonic / perc.", "Median-filter split of sustained vs transient sound. Set an HPSS margin "
                                      "above 1 in DSP settings for a third Residual stem."),
]

# Recipes that never use a model; they ignore DSP-only mode.
DSP_TARGETS = {"center", "sides", "repeating", "multistage", "mid_side", "harm_perc"}


@dataclass
class RecipeResult:
    stems: list  # [(name, audio)]
    solo: Optional[str]
    notes: list = field(default_factory=list)


class Separator:
    def __init__(self):
        self.cache: dict[tuple, dict[str, np.ndarray]] = {}
        self.denoise = False
        self.dsp_only = False
        self.dsp = D.DSPSettings()
        self.preferred: dict[str, str] = {}

    def clear(self, source_id=None):
        if source_id is None:
            self.cache.clear()
        else:
            for k in [k for k in self.cache if k[0] == source_id]:
                del self.cache[k]

    # --------------------------------------------------------- building blocks
    def _model(self, role: str) -> Optional[M.ModelInfo]:
        if self.dsp_only:
            return None
        return M.first_installed(role, self.preferred.get(role))

    def _mkey(self, m: Optional[M.ModelInfo]) -> tuple:
        """Cache tag: which model (and quality) or which DSP settings made a result."""
        if m is None:
            return ("dsp",) + self.dsp.key()
        return (m.key, self.denoise)

    def _missing(self, notes, what: str, model: str):
        if self.dsp_only:
            notes.append(f"DSP-only mode: {what}.")
        else:
            notes.append(f"No {model} model installed: {what}. Install one in Models for real separation, "
                         "or turn on DSP-only mode to hide this note.")

    def _run_model(self, m: M.ModelInfo, audio, sr, progress, cancelled, label):
        path = M.find_model_file(m.file)
        cb = (lambda f: progress(f, label)) if progress else None
        return separate_file_audio(audio, sr, str(path), m.params, denoise=self.denoise,
                                   progress=cb, cancelled=cancelled)

    @staticmethod
    def _p(progress, label):
        return (lambda f: progress(f, label)) if progress else None

    def vocals(self, sid, audio, sr, progress, cancelled, notes):
        m = self._model("vocals")
        key = (sid, "vocals") + self._mkey(m)
        if m is None:
            self._missing(notes, "vocals estimated from repetition, stereo position, vocal band and transients. "
                                 "Tune it in Separate > DSP separation settings", "vocal")
        if key in self.cache:
            return self.cache[key]
        if m is None:
            voc, inst = D.vocals_estimate(audio, sr, self.dsp, self._p(progress, "DSP vocal estimate"), cancelled)
            res = {"Vocals (DSP)": voc, "Instrumental (DSP)": inst,
                   "_voc": "Vocals (DSP)", "_inst": "Instrumental (DSP)"}
        else:
            primary, secondary = self._run_model(m, audio, sr, progress, cancelled, f"{m.title}")
            if m.params.primary_stem.lower().startswith("instrument"):
                voc, inst = secondary, primary
            else:
                voc, inst = primary, secondary
            res = {"Vocals": voc, "Instrumental": inst, "_voc": "Vocals", "_inst": "Instrumental"}
        self.cache[key] = res
        return res

    def instrumental(self, sid, audio, sr, progress, cancelled, notes):
        v = self.vocals(sid, audio, sr, progress, cancelled, notes)
        return v[v["_inst"]]

    def lead_backing(self, sid, audio, sr, progress, cancelled, notes):
        v = self.vocals(sid, audio, sr, progress, cancelled, notes)
        voc = v[v["_voc"]]
        m = self._model("karaoke")
        key = (sid, "lead_backing") + self._mkey(self._model("vocals")) + self._mkey(m)
        if m is None:
            self._missing(notes, "lead/backing split by stereo position (center = lead, sides = backing)", "karaoke")
        if key in self.cache:
            return self.cache[key]
        if m is None:
            lead, back = D.center_sides(voc, sr, strict=0.7, progress=self._p(progress, "Center extraction"),
                                        cancelled=cancelled)
        else:
            back, lead = self._run_model(m, voc, sr, progress, cancelled, m.title)
        res = {"Lead vocal": lead, "Backing vocals": back}
        self.cache[key] = res
        return res

    def ai_stem(self, role, sid, audio, sr, progress, cancelled):
        m = self._model(role)
        if m is None:
            return None
        key = (sid, role) + self._mkey(m)
        if key in self.cache:
            return self.cache[key]
        primary, secondary = self._run_model(m, audio, sr, progress, cancelled, m.title)
        res = {m.params.primary_stem: primary, "_name": m.params.primary_stem}
        self.cache[key] = res
        return res

    def _cached(self, key, fn):
        key = key + self._mkey(None)
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]

    # --------------------------------------------------------- recipes
    def run(self, target: str, sid, audio: np.ndarray, sr: int,
            progress: Optional[Progress] = None, cancelled=None) -> RecipeResult:
        notes: list[str] = []
        prog = progress or (lambda f, s: None)
        s = self.dsp

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
            self._missing(notes, "drums are the clearly percussive part of the mix. Hard picking, strums "
                                 "and consonants land there too", "drum")
            d = self._cached((sid, "drums_dsp"), lambda: D.drums_estimate(
                audio, sr, s, self._p(prog, "Percussive split"), cancelled))
            return RecipeResult([("Drums (DSP)", d), ("Without drums (DSP)", (audio - d).astype(np.float32))],
                                "Drums (DSP)", notes)

        if target == "bass":
            r = self.ai_stem("bass", sid, audio, sr, prog, cancelled)
            if r is not None:
                return RecipeResult([(r["_name"], r[r["_name"]])], r["_name"], notes)
            self._missing(notes, f"bass is sustained, mostly-center content below {s.bass_cutoff:.0f} Hz. "
                                 "Kick drum tails and low keys can bleed in", "bass")
            b = self._cached((sid, "bass_dsp"), lambda: D.bass_estimate(
                audio, sr, s, self._p(prog, "Bass estimate"), cancelled))
            return RecipeResult([("Bass (DSP)", b), ("Without bass (DSP)", (audio - b).astype(np.float32))],
                                "Bass (DSP)", notes)

        if target == "other":
            r = self.ai_stem("other", sid, audio, sr, prog, cancelled)
            if r is not None:
                return RecipeResult([(r["_name"], r[r["_name"]])], r["_name"], notes)
            self._missing(notes, "estimated as sustained content of the instrumental above the bass range", "'other'")
            inst = self.instrumental(sid, audio, sr, prog, cancelled, notes)

            def make():
                h, _ = D.hpss(inst, sr, time_s=s.hpss_time_s, freq_hz=s.hpss_freq_hz,
                              progress=self._p(prog, "Harmonic/percussive split"), cancelled=cancelled)
                _, rest = D.split_low(h, sr, s.bass_cutoff)
                return rest

            o = self._cached((sid, "other_dsp") + self._mkey(self._model("vocals")), make)
            return RecipeResult([("Other (DSP)", o)], "Other (DSP)", notes)

        if target in ("center", "sides"):
            cs = self.cache.get((sid, "center_sides"))
            if cs is None:
                c, sd = D.center_sides(audio, sr, progress=lambda f: prog(f, "Center extraction"), cancelled=cancelled)
                cs = self.cache[(sid, "center_sides")] = {"Center": c, "Sides": sd}
            return RecipeResult([("Center", cs["Center"]), ("Sides", cs["Sides"])],
                                "Center" if target == "center" else "Sides", notes)

        if target == "repeating":
            fg, bg = self._cached((sid, "repet"), lambda: D.repet_sim(
                audio, sr, s, self._p(prog, "Finding repetition (REPET-SIM)"), cancelled))
            return RecipeResult([("Lead (non-repeating)", fg), ("Repeating", bg)], "Lead (non-repeating)", notes)

        if target == "multistage":
            su, vo, hi = self._cached((sid, "multistage"), lambda: D.multistage(
                audio, sr, s, self._p(prog, "Two-pass median filtering"), cancelled))
            return RecipeResult([("Sustained", su), ("Voice / leads", vo), ("Hits", hi)], "Voice / leads", notes)

        if target == "mid_side":
            m, sd = D.mid_side(audio)
            return RecipeResult([("Mid (L+R)", m), ("Side (L-R)", sd)], "Mid (L+R)", notes)

        if target == "harm_perc":
            three = s.hpss_margin > 1.0

            def make():
                return D.hpss(audio, sr, margin=s.hpss_margin, time_s=s.hpss_time_s, freq_hz=s.hpss_freq_hz,
                              three_way=three, progress=self._p(prog, "Harmonic/percussive split"),
                              cancelled=cancelled)

            parts = self._cached((sid, "hpss"), make)
            stems = [("Harmonic", parts[0]), ("Percussive", parts[1])]
            if len(parts) > 2:
                stems.append(("Residual", parts[2]))
            return RecipeResult(stems, "Harmonic", notes)

        raise ValueError(f"Unknown target {target}")

    def run_model(self, m: M.ModelInfo, sid, audio, sr, progress=None, cancelled=None) -> RecipeResult:
        prog = progress or (lambda f, s: None)
        primary, secondary = self._run_model(m, audio, sr, prog, cancelled, m.title)
        return RecipeResult([(m.params.primary_stem, primary), (m.secondary, secondary)], m.params.primary_stem, [])
