# Blackfly GUI

A fast, open-source GUI for Teledyne FLIR **Spinnaker** cameras (Blackfly S and
friends), written in Python. Built as a lower-latency, less frustrating
alternative to SpinView for lab and teaching use.

> Work in progress — see the plan / git history for status.

## Features (v1 target)

- Low-latency live view (newest-frame-only buffering)
- Exposure, gain, gamma, black level, frame rate
- Trigger mode / source / edge / delay, software trigger
- On-camera binning, decimation, ROI (draw it on the image), pixel format
- Camera user sets, and a searchable tree of *every* camera feature
- Histogram, saturation overlay, line profile, ROI statistics
- 2D Gaussian fit (centroid, sigma, FWHM, angle)
- Reference image subtraction / division (dark & flat-field correction)
- Simulated camera mode for trying it without hardware

## Requirements

1. **Spinnaker SDK** (Windows x64) from
   [Teledyne](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/)
   (free account). During install select the **USB3 driver**. This is needed even
   for the standalone `.exe`.
2. For running from source: Python **3.12** (PySpin has no 3.13 wheel yet) and the
   matching **PySpin wheel** (`spinnaker_python-…-cp312-cp312-win_amd64`) from the
   same download page.

## Developer setup (from source)

```powershell
pip install uv                      # or see https://docs.astral.sh/uv/
python -m uv python install 3.12
python -m uv venv --python 3.12
python -m uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
# the PySpin wheel you downloaded (use the real file name):
python -m uv pip install --python .venv\Scripts\python.exe vendor\spinnaker_python-4.4.0.246-cp312-cp312-win_amd64.whl

.venv\Scripts\blackfly-gui              # real camera
.venv\Scripts\blackfly-gui --simulate   # no camera needed
.venv\Scripts\python -m pytest
```

> Always pass `--python .venv\Scripts\python.exe` to `uv pip` when uv was
> installed with pip; otherwise `python -m uv` installs into the Python that
> launched it rather than the project venv.

## Troubleshooting

- **Device Manager shows "USB3 Vision Device" with a warning / code 28** —
  the Spinnaker USB3 driver is not installed. Install the Spinnaker SDK.
- **"Camera in use"** — close SpinView or any other program using the camera.
- **Low frame rate** — make sure the camera is on a USB 3 port (blue / SS),
  and use a smaller ROI or binning for higher speeds.

## License

MIT — see [LICENSE](LICENSE).
