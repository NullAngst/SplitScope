<p align="center"><img src="assets/icon.png" width="128" alt="SplitScope icon"></p>

# SplitScope

An offline audio separation workstation for anyone who wants stems out of a finished mix. Open a song, pull out the vocals (or drums, bass, guitars and keys, lead or backing vocals, center or side channels) with one click, hear the result through a real-time EQ and multiband chain, and export any stem or the processed mix.

Every button works with or without AI. With the MDX-Net models installed you get model quality; with "DSP only" ticked (or no models on disk) each button runs a model-free method built from repetition, stereo position, sustain and frequency, which you can tune.

Runs on Windows, Linux and macOS (Apple Silicon). Nothing is sent anywhere: once the models are on disk, the program never touches the network.

## What it does

**One-click isolation.** Buttons on the left create new stems and solo the result:

| Button | With a model | Without (DSP) |
|---|---|---|
| Isolate vocals / Remove vocals | Kim Vocal 2 by default | Vocal estimate: non-repeating x center-panned x vocal band x not-a-transient |
| Lead vocal / Backing vocals | Vocals, then UVR Karaoke 2 | Vocals, then split by stereo position (center = lead) |
| Isolate drums | KUIELab drums | Percussive part with a margin, so only clear hits count |
| Isolate bass | KUIELab bass | Sustained, mostly-center content under 200 Hz from a long-window split |
| Guitars / keys | KUIELab "other" (keys and synths come along, there is no guitar-only model) | Sustained part of the instrumental above the bass range |
| Center channel / Side channels | | Spectral pan analysis. Backing vocals and double-tracked guitars are usually on the sides |
| Repeating / lead | | REPET-SIM: what repeats through the song vs what does not |
| Sustain / voice / hits | | Two-pass HPSS: steady instruments, voice and leads, drums |
| Mid / side split | | Plain L+R / L-R matrix |
| Harmonic / perc. | | Median-filter HPSS, optional third Residual stem |

Any stem can be separated further: use a stem's `...` menu, "Separate this stem further", then press another button. For example: Isolate vocals, then Backing vocals on the Vocals stem.

A button with a dashed border runs its DSP method, either because its model is missing or because "DSP only (no AI)" is ticked. The status bar says which method ran. DSP stems are named with "(DSP)" so they never get mixed up with model output. Every split sums back to its source exactly: unmute all the stems of one run and you hear the original.

## Separating without AI

I use DSP-only mode when I want to know exactly what happened to the signal, or on material the models were never trained on (live tapes, old mono-ish stereo, spoken word over music). Tick "DSP only (no AI)" under the isolate buttons and every button switches to its model-free method. Untick it and the models come back; both results stay cached, so you can A/B them.

What the methods actually look at:

- **Repetition (REPET-SIM, Rafii and Pardo 2012).** For each moment of the song it finds the 40 moments that look most like it (at least a second away) and takes their median. A chorus chord, a drum loop or a riff survives the median and becomes "Repeating". A lead vocal line or a solo usually does not repeat note for note and becomes "Lead (non-repeating)". This is the strongest cue for vocals in pop and rock.
- **Sustain vs hits (HPSS, FitzGerald 2010).** A median along time keeps steady partials, a median along frequency keeps broadband hits. With a split margin above 1 a third "Residual" stem catches whatever is neither (noise, breath, reverb tails).
- **Two window sizes (FitzGerald and Gainza 2010).** Through a 370 ms window a voice with vibrato looks smeared and lands with the drums; through a 23 ms window it looks steady and separates from them. "Sustain / voice / hits" runs both passes.
- **Stereo position.** The same pan and phase test the live Spatial panel uses.

The DSP vocal estimate multiplies the first, second and fourth cues with a vocal band (100 Hz to 12 kHz by default). Each cue is a soft 0 to 1 weight per frequency bin, and the instrumental is the exact remainder.

Tune all of it in Separate > DSP separation settings (or the "DSP settings" button). Every value has a tooltip. The ones worth trying first:

- Vocals bleeding the backing: raise "Background bias" to 1.5 or 2.
- Vocals too quiet or thin: lower "Background bias", lower "Center weighting" for wide or double-tracked vocals.
- Snare hits in the vocal: raise "Transient suppression". Too high dulls consonants.
- Songs with few repeats (through-composed, short clips): lower "Similar frames" to 15 or 20.

Changing a setting and pressing the button again replaces that stem with the new result. If a setting makes things worse, "Restore Defaults" in the dialog puts it back.

Stacking works well without AI. Some chains I use:

- Center channel, then Repeating / lead on the Center stem: a centered lead vocal without the centered loop under it.
- Remove vocals (DSP), then Harmonic / perc. on the instrumental: a cleaner drum or chord bed.
- Sustain / voice / hits, then the Focus panel on "Voice / leads" with the guitar solo preset, then Render processing chain to new stem.

**Real-time processing chain** (everything you hear, and exactly what "What you hear" exports):

- Spatial: center extract, remove center, extract or remove any pan position, mid/side levels with mute, left/right only, swap. Adjustable width, phase strictness, smoothing, depth and frequency range.
- Focus: instrument frequency presets (vocal range, vocal presence, guitar, guitar solo, bass, kick, snare, cymbals, drums, sub, piano) with one or two ranges, slope, and invert.
- Parametric EQ: any number of bands (bell, shelves, low/high cut up to 96 dB/oct, notch, band pass). Each band can act on stereo, mid, side, left or right.
- 15-band graphic EQ.
- 4-band multiband with per-band level, mute, solo and compression.
- Output: gain, balance, mono, tape-style saturation, compressor, limiter.
- Playback speed 0.25x to 2x, with pitch kept (phase vocoder) or varispeed.

The whole chain runs in the frequency domain with zero-phase gains, so EQ and crossovers add no phase shift, and bypassing it gives back the input unchanged.

**Analyser.** The large display shows the spectrum after processing (prism coloured by frequency), optionally the input spectrum behind it, the combined EQ curve and draggable band nodes, plus a strip showing typical instrument ranges. Below it: a scrolling spectrogram and a waveform with loop selection.

To find where something sits (a guitar solo, a buried vocal): hold **Shift** and drag across the analyser (or drag with the middle mouse button). Playback narrows to a band around the cursor. Sweep until the part you want is loudest, release, then double-click there to drop an EQ node and boost or cut it.

## Download and run

Get the archive for your system from the [Releases](../../releases) page.

**Windows:** unzip anywhere and run `SplitScope\SplitScope.exe`. The build is not code-signed, so SmartScreen may warn on first launch: "More info", then "Run anyway".

**Linux (x86-64):** download `SplitScope-<version>-x86_64.AppImage`, make it executable and run it:

```
chmod +x SplitScope-*-x86_64.AppImage
./SplitScope-*-x86_64.AppImage
```

Optional: `./SplitScope-*-x86_64.AppImage --install-desktop-entry` adds it to your application menu (run it again if you move the file, `--remove-desktop-entry` takes it out). Gear Lever or AppImageLauncher work too, and since the AppImage carries update information, they and AppImageUpdate can update it in place from the newest release.

The AppImage uses the static AppImage runtime, so it does not need `libfuse2`. Audio goes through your system's ALSA library, which on PipeWire or PulseAudio desktops routes to them as usual. Built on Ubuntu 22.04, so it needs glibc 2.35 or newer (Ubuntu 22.04+, Kubuntu, Debian 12, Fedora 36+, openSUSE Tumbleweed and Leap 15.6 are all fine). For portable models, put a `models` folder next to the `.AppImage` file.

**macOS (Apple Silicon):** unzip and move `SplitScope.app` to Applications. The app is not signed or notarized, so macOS will refuse to open it until you clear the download flag once:

```
xattr -dr com.apple.quarantine /Applications/SplitScope.app
```

Intel Macs are not built. Running from source works there.

## Models

Separation uses MDX-Net networks from the [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) project and [KUIELab](https://github.com/kuielab/mdx-net), run with ONNX Runtime on the CPU.

Release builds include these by default (about 210 MB):

| Model | Used for |
|---|---|
| Kim Vocal 2 | Vocals / instrumental |
| UVR MDX-Net Karaoke 2 | Lead vs backing vocals |
| KUIELab MDX-Net drums, bass, other | Drums, bass, guitars and keys |

More can be downloaded from inside the app (Models button): UVR MDX-Net Voc FT and Inst HQ 3 are alternatives for vocals. Any other MDX-Net `.onnx` can be imported; if it is in UVR's model table its settings are detected automatically, otherwise you are asked for its FFT size.

The app looks for models in this order:

1. a `models` folder next to the executable, or next to the `.AppImage` file on Linux (portable use)
2. the models bundled into the build
3. your user data folder (where in-app downloads go):
   - Windows: `%APPDATA%\SplitScope\models`
   - Linux: `~/.local/share/SplitScope/models`
   - macOS: `~/Library/Application Support/SplitScope/models`

**About model licensing.** The UVR application is MIT-licensed, but the model weight files are distributed without an explicit license. Bundling them in releases is common practice in the community and the authors publish them for free use, but it is not a formal grant. If that matters for your use, build without bundled models (see below) and let users download them in the app.

### Speed

Separation runs on the CPU. In development, on a single CPU core, Kim Vocal 2 needed about 1.2 seconds per second of audio on full-length material, and short clips take proportionally longer because of padding. ONNX Runtime uses all cores, so a multi-core desktop should be several times faster, but that has not been measured on typical desktop hardware. "High quality" runs every model twice and doubles the time. Results are cached per stem, so switching between Isolate vocals, Remove vocals, Lead vocal and Backing vocals does not rerun the vocal model.

## Honest limits

- The DSP methods have no idea what a voice or a guitar is. They know what repeats, what sustains and where it sits in the stereo field. A sung phrase that repeats exactly (a chorus hook, a doubled ad-lib) partly goes to "Repeating". A guitar riff that never repeats lands with the vocal. Expect clearly more bleed than the models give.
- The DSP methods hold the whole song's magnitude spectrogram in memory. On a 5-minute stereo song, the DSP vocal estimate took about 55 seconds and peaked around 1.2 GB on a 2-core test machine; "Sustain / voice / hits" took about 40 seconds. More cores help the similarity search.
- The default DSP settings were chosen on synthetic test mixes, not on a listening panel. Treat them as a starting point and tune per song.
- Frequency focus is a filter. "Guitar solo focus" keeps 500 Hz to 4.5 kHz, which also keeps the vocal, snare and keys in that range. Use it together with an AI stem or center/side extraction.
- There is no guitar-only model. "Guitars / keys" returns the KUIELab "other" stem.
- Center/side separation depends on the mix. Anything panned center (lead vocal, kick, snare, bass) lands in Center, anything wide lands in Sides. Mono recordings have nothing to separate, and the app tells you when a file is mono.
- Stronger spectral processing (narrow center extraction, extreme pan windows, time stretching) produces the usual phasey or watery artifacts.
- The multiband and main compressors detect level once per STFT hop (about 23 ms at 44.1 kHz). Fine for taming dynamics, too slow for transient shaping.
- Playback latency is roughly 100 to 200 ms because processing happens in 4096-sample FFT frames. Controls respond immediately, you hear the change a moment later.
- All stems are held in memory: roughly 100 MB per stem for a 5-minute stereo song.

## Export

File > Export (Ctrl+E), or a stem's `...` menu:

- What you hear: the audible stems through the chain
- Selected stem, unprocessed
- Every stem as its own file, unprocessed or each through the chain

Formats: WAV 16/24-bit and 32-bit float, FLAC 16/24-bit, MP3 (VBR), OGG Vorbis. Whole file or the loop region, optional peak normalize, optional playback speed.

Separate > "Render processing chain to new stem" bakes the current chain into a new stem and resets the chain, so you can stack steps (for example center extract first, then run a model on the result).

Import handles WAV, FLAC, MP3, OGG, Opus and AIFF directly, and M4A/AAC, WMA and video soundtracks through the bundled ffmpeg.

## Keyboard

| Key | Action |
|---|---|
| Space | Play / pause |
| Home | Return to start (or loop start) |
| L | Toggle loop (drag in the waveform to set it, right-click to clear) |
| B | Bypass the whole chain (A/B compare) |
| Ctrl+O / Ctrl+E | Open / export |
| Shift+drag in analyser | Solo a narrow frequency band |
| Delete | Remove the selected EQ band |

In the analyser: drag a node to move it, mouse wheel changes Q (or slope for cut filters), Ctrl while dragging for fine moves, double-click empty space to add a band, right-click a node for type and channel.

## Run from source

Python 3.10 or newer.

```
git clone https://github.com/NullAngst/SplitScope
cd SplitScope
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python SplitScope.py
```

On Linux, `sounddevice` needs PortAudio from your package manager (openSUSE: `sudo zypper install portaudio`, Debian/Ubuntu: `sudo apt install libportaudio2`). Without it the app still separates, analyses and exports, but cannot play audio.

`python SplitScope.py --selftest` checks every native dependency and prints a report.

## Building releases with GitHub Actions

The workflow in `.github/workflows/release.yml` builds all three platforms, runs the self-test on each frozen build (a real model inference and a DSP separation run), and packages the result: a zip for Windows and macOS, an AppImage plus its `.zsync` update file for Linux. The Linux AppImage is self-tested a second time as the finished image. Everything can be done from the GitHub website:

- **Publish a release:** Releases > Draft a new release > choose or create a tag such as `v1.1.0` > Publish. The workflow starts and attaches the builds to that release after 15 to 30 minutes.
- **Test build without a release:** Actions > Build release > Run workflow. Download the archives from the run's Artifacts section. Enter a tag in the form to also create a release.

To change which models are bundled, edit `BUNDLE_MODELS` at the top of the workflow (keys from `splitscope/models.py`), or untick "Bundle AI models" when running it manually. The Linux job compiles the latest PortAudio release with ALSA support only and ships it with the build; `libasound` itself is left out on purpose so the user's system ALSA (and its PipeWire/Pulse plugins) is used. The AppImage is packed with the rolling `continuous` build of appimagetool, and the PortAudio and PyInstaller versions float too, so nothing in the workflow needs a manual version bump.

To build locally: `pip install pyinstaller`, then `pyinstaller --noconfirm packaging/splitscope.spec`. See the comments at the top of the spec for the environment variables it reads. To turn that into an AppImage, copy the steps from the "Build AppImage (Linux)" step of the workflow: they only need `appimagetool` and the files in `packaging/linux/`.

## Project layout

```
SplitScope.py                  entry point
splitscope/
  app.py                       Qt application setup, --selftest
  engine.py                    playback engine (PortAudio via sounddevice)
  separation.py                one-click isolation recipes and caching
  models.py                    model registry, download, import, search paths
  audio_io.py                  import/export (libsndfile, ffmpeg fallback)
  dsp/mdx.py                   MDX-Net inference (NumPy STFT + ONNX Runtime)
  dsp/chain.py                 chain settings and compiled real-time parameters
  dsp/processor.py             STFT processing chain (real-time and offline)
  dsp/curves.py                EQ, focus, crossover and multiband curves
  dsp/dsp_separate.py          model-free separation: REPET-SIM, HPSS, two-pass HPSS,
                               DSP vocal/drum/bass estimates, mid/side, center/sides
  ui/                          widgets and windows
packaging/splitscope.spec      PyInstaller build
packaging/linux/               AppRun and .desktop file for the AppImage
tools/make_icon.py             regenerates assets/icon.*
tools/fetch_models.py          downloads models for bundling
```

## Credits and license

Model-free methods follow: Z. Rafii and B. Pardo, "Music/Voice Separation using the Similarity Matrix" (ISMIR 2012); D. FitzGerald, "Harmonic/Percussive Separation using Median Filtering" (DAFx 2010); D. FitzGerald and M. Gainza, "Single Channel Vocal Separation using Median Filtering and Factorisation Techniques" (ISAST 2010); J. Driedger, M. Müller and S. Disch, "Extending Harmonic-Percussive Separation of Audio Signals" (ISMIR 2014). The code is a NumPy/SciPy implementation written for this project.


SplitScope is licensed under the GNU General Public License v3.0 or later (see `LICENSE`).

- Separation models: Ultimate Vocal Remover (Anjok07, aufr33 and contributors, including Kim's vocal model) and KUIELab MDX-Net. `splitscope/resources/uvr_mdx_model_data.json` is the model parameter table from UVR, used to configure imported models. See `THIRD_PARTY.md`.
- Libraries: Qt / PySide6 (LGPLv3), NumPy, SciPy, ONNX Runtime (MIT), libsndfile via soundfile (LGPL), PortAudio via sounddevice (MIT), ffmpeg via imageio-ffmpeg (LGPL/GPL builds).
