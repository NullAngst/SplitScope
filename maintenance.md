# Maintenance

A check-up to run every couple of months. Most of it happens on the GitHub website, since the builds run in Actions. Plan on 30 minutes when nothing broke, longer when something did.

## 1. Run a test build

1. Go to Actions > Build release > Run workflow. Leave "tag" empty, keep "Bundle AI models" ticked.
2. Wait for all three jobs. Each one runs `--selftest`, which checks NumPy/SciPy, the DSP separation, libsndfile, PortAudio, ONNX Runtime (a real model inference), ffmpeg and the Qt GUI. The Linux job runs it twice: once on the PyInstaller folder, once on the finished AppImage.
3. If a job fails, open the "Self-test" step first. Each line says PASS, WARN or FAIL with the reason.

If everything passes, download the Linux artifact and check by hand on your own machine:

1. `chmod +x SplitScope-*-x86_64.AppImage && ./SplitScope-*-x86_64.AppImage`
2. Open a song, press Isolate vocals with and without "DSP only (no AI)" ticked, play the result.
3. Check playback actually comes out of your speakers (PipeWire/Pulse through ALSA).
4. Export a FLAC and an MP3 and play both.

## 2. Things that float, and what breaks when they move

Nothing in the build is pinned on purpose, so updates arrive by themselves. That also means a new upstream release can break a build without any change here. Where to look:

| Piece | Where it comes from | Typical breakage | Fix |
|---|---|---|---|
| Python | `PYTHON_VERSION` in the workflow | A dependency has no wheel for a new Python yet | Leave it one version behind the newest until wheels exist |
| PyInstaller | latest from PyPI | Spec options renamed, hooks moved | Read the PyInstaller changelog, adjust `packaging/splitscope.spec` |
| PySide6, NumPy, SciPy, onnxruntime, soundfile, sounddevice | `requirements.txt` ranges | API change in a major version | The upper bounds (`<7`, `<3`) stop surprise majors. Raise them after a test build passes |
| PortAudio | latest GitHub release, built from source | `configure` flags change | Edit the "Build PortAudio" step |
| appimagetool | `continuous` build | New flags, or zsync handling moves | The step already handles the .zsync in either place. Check `appimagetool --help` |
| Runner images | `windows-latest`, `ubuntu-22.04`, `macos-14` | GitHub retires an image | Switch Linux to `ubuntu-24.04` (glibc floor rises to 2.39), macOS to the next arm64 image |
| Actions | `actions/checkout@v4` etc. | GitHub deprecates a major | Bump to the next major tag |
| Models | UVR model repository on GitHub | A download URL moves | Update `UVR_URL` or the file names in `splitscope/models.py` |

## 3. Check the Ubuntu runner and glibc

1. Open the Linux job log and look at the "Set up job" section for the runner image version.
2. GitHub announces image retirements in the actions/runner-images repository. If 22.04 is on the list, move to 24.04 and update the glibc line in README.md.

## 4. Security

1. Look at the repository's Security tab for Dependabot alerts. Most will be Python packages: a fresh build picks up the fixed version on its own, since nothing is pinned.
2. ffmpeg comes from imageio-ffmpeg, PortAudio and libsndfile from their own projects. A new release build pulls current versions of all of them, so publishing a release is the fix for most library CVEs.

## 5. DSP separation sanity check

From a source checkout:

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python SplitScope.py --selftest
```

The "Model-free separation" line must say PASS. If NumPy or SciPy changed behaviour (median_filter, FFT), this is where it shows up first.

## 6. Publish

When the test build is good and you want users on it:

1. Bump `__version__` in `splitscope/__init__.py`.
2. Add an entry to `changes.md`.
3. Releases > Draft a new release > new tag `vX.Y.Z` > Publish.

AppImage users with Gear Lever, AppImageLauncher or AppImageUpdate get the update from the `.zsync` file attached to the newest release, so do not rename the AppImage pattern (`SplitScope-<version>-x86_64.AppImage`) in the workflow, or their updates stop finding it.
