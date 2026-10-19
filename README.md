# Blackfly GUI

A fast, open-source GUI for Teledyne FLIR **Spinnaker** cameras (Blackfly S,
Chameleon, Grasshopper, Oryx … anything Spinnaker supports), written in
Python. It was built as a lower-latency, less frustrating alternative to
SpinView for lab and teaching use, and developed against a
**BFS-U3-31S4M-C** (Sony IMX265, mono, 2048×1536, 3.45 µm pixels).

## Highlights

- **Low-latency live view** – the camera stream uses *NewestOnly* buffering
  and the display only ever draws the newest frame, so there is no backlog
  of stale images (the main cause of SpinView's delay).
- **No more "locked" settings** – the GUI resolves GenICam dependencies
  for you: setting Exposure switches ExposureAuto off, setting a frame rate
  enables the frame-rate limit, changing ROI / binning / pixel format pauses
  acquisition for a moment and restarts it, changing the trigger source
  temporarily turns the trigger off. Anything still unavailable says *why*
  in its tooltip.
- **Basic controls**: exposure (manual / once / continuous), gain, black
  level, gamma, frame rate, trigger mode / source / edge / delay /
  overlap, software trigger.
- **Advanced controls**: on-camera binning (sum / average) and decimation,
  ROI (type it, centre it, or draw it on the image), pixel format and ADC
  bit depth, reverse X/Y, camera user sets, and a searchable, editable tree
  of **every** feature the camera exposes.
- **Analysis**: histogram & display levels, saturation highlighting,
  cursor readout, crosshair, ROI statistics, line profile with FWHM.
- **Live 2D Gaussian fit**: centroid, σ / FWHM / 1/e² diameter along the
  major/minor axes and x/y, angle, ellipticity – in pixels and µm (pixel
  size, magnification and binning aware), with an overlay ellipse and
  copy-to-clipboard.
- **Reference correction**: capture (N-frame average) or load dark and
  reference images; display `I − D`, `I − R`, `I / R` or flat-field
  `(I − D)/(R − D)`.
- **Snapshots**: raw camera data as lossless TIFF plus a JSON file
  recording every camera setting; settings files to share a configuration.
- **Simulated camera** so anyone can try the software without hardware.

## For students: running the app

1. **Install the Spinnaker SDK** (free, needs a Teledyne account):
   <https://www.teledynevisionsolutions.com/products/spinnaker-sdk/>.
   Choose *Windows x64*, run the installer and keep the **USB3 driver**
   selected. This installs the camera driver; it is needed even for the
   standalone app.
2. Plug the camera into a **USB 3** port (blue / "SS").
3. Unzip `BlackflyGUI-<version>-win64.zip` anywhere and run
   **`BlackflyGUI.exe`**.
4. Pick the camera in the toolbar and click **Connect** (acquisition starts
   automatically). *Simulated Blackfly S* is always listed for practice.

Only one program can use a camera at a time – close SpinView first.

## Using it

| Area | What it does |
|---|---|
| Toolbar | camera picker, Connect, Start/Stop (**Space**), Snapshot (**Ctrl+S**), Save as (**Ctrl+Shift+S**), snapshot folder, Help (**F1**) |
| Image | wheel = zoom, drag = pan, double-click = fit; histogram on the right sets display levels (or tick *Auto levels*); red = saturated pixels |
| Acquisition | exposure, gain, black level, gamma, frame rate, *Single frame* |
| Trigger | trigger mode/source/edge/delay/overlap; **T** or the button fires a software trigger |
| Format | pixel format, ADC bit depth, binning (1×1 / 2×2 / 4×4 buttons), decimation, ROI, flips |
| User sets | camera-stored user sets, factory defaults, save/load settings files |
| All features | every camera feature; search, select, edit |
| Analysis ▸ Statistics | yellow analysis ROI with live statistics; *Set camera ROI to this box* crops on the camera (faster frame rate) |
| Analysis ▸ Line profile | blue line on the image, profile plot and FWHM |
| Analysis ▸ Gaussian fit | live 2D Gaussian fit in the yellow ROI (or whole image) |
| Analysis ▸ Reference | capture/load dark (D) and reference (R) images and choose a correction |

Tips

- **Saturation** biases every measurement. If pixels turn red, reduce
  exposure or gain. Use Mono12p/Mono16 for more dynamic range.
- **Higher frame rate**: smaller ROI, binning, Mono8, shorter exposure.
  The status bar shows the camera rate vs. the display rate; the display
  may skip frames when the PC is slower than the camera – the camera never
  falls behind.
- **Gaussian fit units**: set the sensor pixel size (3.45 µm for the IMX265)
  and the magnification of your optics. With *× binning/decimation* ticked,
  µm values follow the on-camera binning automatically. Angles are measured
  from +x toward +y; since image rows go down, positive angles appear
  clockwise on screen.
- **Dark frames** must use the same exposure, gain and pixel format as the
  images they correct; references must match the image size (ROI/binning).
- **Snapshots** save the *raw* camera data. When a reference correction is
  active a float32 `*_corrected.tif` is saved alongside.

## Developer setup (from source)

Requires Python **3.12** (PySpin has no 3.13 wheel yet) and the matching
**PySpin wheel** (`spinnaker_python-…-cp312-cp312-win_amd64`) from the
Spinnaker download page – put the `.whl` in `vendor/`.

```powershell
pip install uv                      # or see https://docs.astral.sh/uv/
python -m uv python install 3.12
python -m uv venv --python 3.12
python -m uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
# the PySpin wheel you downloaded (use the real file name):
python -m uv pip install --python .venv\Scripts\python.exe vendor\spinnaker_python-4.4.0.246-cp312-cp312-win_amd64.whl

.venv\Scripts\python tools\check_camera.py   # hardware sanity check (no GUI)
.venv\Scripts\blackfly-gui                   # real camera
.venv\Scripts\blackfly-gui --simulate        # no camera needed
.venv\Scripts\python -m pytest               # tests (use the simulator)
```

> Always pass `--python .venv\Scripts\python.exe` to `uv pip` when uv was
> installed with pip; otherwise `python -m uv` installs into the Python that
> launched it rather than the project venv.

If the PySpin wheel requires a specific NumPy version, pip will tell you;
install that version into the venv.

### Building the standalone app

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
```

This produces `dist\BlackflyGUI\BlackflyGUI.exe` and a zip to share. PySpin
is bundled automatically when installed in the venv (the build log says
*Bundling PySpin*); otherwise the build only offers the simulated camera.

### Project layout

```
src/blackfly_gui/
  camera/base.py         backend-independent camera interface
  camera/spinnaker.py    PySpin backend (any Spinnaker camera)
  camera/simulated.py    simulated IMX265 camera with realistic GenICam rules
  camera/controller.py   dependency resolution, stream pause/restart, acquisition thread
  analysis/              statistics, Gaussian fit, reference correction
  ui/                    main window, image view, panels, analysis dock
  settings_io.py         settings files;  snapshot.py  TIFF + JSON snapshots
tests/                   pytest suite (runs against the simulator)
tools/                   check_camera.py, build_exe.ps1
blackfly_gui.spec        PyInstaller spec
```

Adding a control for another feature is usually one line in a panel, e.g.
`form.add("Label", FeatureNumber(controller, "GenICamFeatureName"))` – the
widget fetches range, unit and description from the camera and hides itself
if the camera lacks the feature.

## Troubleshooting

- **Device Manager shows "USB3 Vision Device" with a warning (code 28)** –
  the Spinnaker USB3 driver is not installed. Install the Spinnaker SDK.
- **No cameras found / "PySpin not installed"** in the status bar – from
  source: install the PySpin wheel into the venv. Standalone: the build was
  made without PySpin; rebuild with PySpin installed.
- **"Could not initialise camera (in use…)"** – close SpinView or any other
  program using the camera, or unplug/replug it.
- **Low frame rate** – USB 2 port, large ROI, Mono16, long exposure, or the
  *Link limit* (DeviceLinkThroughputLimit) in the Acquisition panel.
- **Incomplete / dropped frames** shown in the status bar – use a USB 3
  port directly on the PC (not a hub), or lower the throughput.

## License

MIT – see [LICENSE](LICENSE).
