# Changes

2026.11.04 - Backend overhaul / Build overhaul

* Model-free separation rewritten in `splitscope/dsp/dsp_separate.py`: REPET-SIM (repeating vs non-repeating), HPSS with an optional third Residual stem, two-pass HPSS (sustain / voice / hits), and new DSP estimates for vocals, drums and bass.
* DSP vocal estimate now combines four cues (non-repeating, center-panned, vocal band, not a transient) instead of center extraction plus a band-pass filter. On the synthetic test mix it went from about 0.6 dB to about 16 dB improvement over the raw mix.
* New buttons: "Repeating / lead" and "Sustain / voice / hits".
* "DSP only (no AI)" checkbox: every button uses its model-free method even with models installed.
* DSP separation settings dialog (Separate menu and the "DSP settings" button), saved between sessions.
* DSP drum and bass estimates also produce "Without drums" / "Without bass" stems.
* Every DSP split sums back to its source exactly.
* Separation cache now keys on the model, the High quality setting and the DSP settings, so changing any of them reruns instead of returning a stale result.
* Rerunning a button now replaces an existing stem of the same name instead of silently keeping the old audio.
* Self-test runs a DSP separation, so frozen builds fail CI if the model-free path breaks.
* Linux release is now an AppImage built by GitHub Actions (static runtime, no libfuse2 needed), with a `.zsync` file and embedded update information for AppImageUpdate, Gear Lever and AppImageLauncher.
* The AppImage itself is self-tested in CI, not only the PyInstaller folder.
* `--install-desktop-entry` and `--remove-desktop-entry` replace `install-desktop-entry.sh`.
* Portable `models` folder is looked for next to the `.AppImage` file.
* Workflow no longer pins PortAudio or PyInstaller: it builds the latest PortAudio release and uses the current PyInstaller and the rolling appimagetool build.
* Version 1.1.0.

Reasons for changes: the old DSP fallbacks were filters, not separation, which made the app close to useless without the AI models. The Linux tar.gz needed manual extraction and desktop setup; an AppImage is one file that runs and updates on any glibc 2.35+ distro.

2026.09.23 - Initial release

* SplitScope 1.0.0: AI stem separation (MDX-Net via ONNX Runtime), real-time spectral processing chain, analyser, export. Windows, Linux (tar.gz) and macOS builds by GitHub Actions.

Reasons for changes: replaces WebDAW Pro (WebDawUltimate.html).
