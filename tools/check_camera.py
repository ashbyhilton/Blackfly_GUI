"""Quick hardware check -- run this first after installing the Spinnaker SDK.

    .venv\\Scripts\\python tools\\check_camera.py

Lists cameras, opens the first one, prints key features and grabs a few
frames, timing them. No GUI involved, so it isolates driver / PySpin issues.
"""

from __future__ import annotations

import sys
import time


def main() -> int:
    try:
        import PySpin
    except ImportError as exc:
        print(f"PySpin import failed: {exc}\nInstall the PySpin wheel into the venv (see README).")
        return 1
    v = PySpin.System.GetInstance().GetLibraryVersion()
    print(f"Spinnaker library version: {v.major}.{v.minor}.{v.type}.{v.build}")

    sys.path.insert(0, "src")
    from blackfly_gui.camera.spinnaker import SpinnakerCamera

    devices = SpinnakerCamera.enumerate()
    print(f"Found {len(devices)} camera(s):")
    for d in devices:
        print(f"  {d.label}  vendor={d.vendor} interface={d.interface}")
    if not devices:
        print("No cameras. Check Device Manager: the camera should NOT show a warning icon.")
        return 1

    cam = SpinnakerCamera()
    cam.open(devices[0].serial)
    try:
        for name in ("DeviceModelName", "DeviceFirmwareVersion", "PixelFormat", "Width", "Height",
                     "ExposureTime", "ExposureAuto", "Gain", "AcquisitionFrameRate",
                     "AcquisitionResultingFrameRate", "BinningHorizontal", "AdcBitDepth",
                     "DeviceLinkSpeed", "DeviceLinkThroughputLimit", "StreamBufferHandlingMode",
                     "StreamBufferCountManual", "DeviceTemperature"):
            info = cam.get_info(name)
            if info is None:
                print(f"  {name:32s} (not present)")
            else:
                rng = f" [{info.min:g} .. {info.max:g}]" if info.min is not None and info.max is not None else ""
                ent = f" {info.entries}" if info.entries else ""
                print(f"  {name:32s} = {info.value!r}{rng}{ent} {'W' if info.writable else 'R'} {info.note}")
        cam.start()
        t0 = time.perf_counter()
        n = 0
        while n < 30 and time.perf_counter() - t0 < 10:
            f = cam.grab(1000)
            if f is not None:
                n += 1
                if n == 1:
                    print(f"First frame: {f.image.shape} {f.image.dtype} {f.pixel_format} "
                          f"min={f.image.min()} max={f.image.max()} mean={f.image.mean():.1f}")
        dt = time.perf_counter() - t0
        cam.stop()
        print(f"Grabbed {n} frames in {dt:.2f}s ({n / dt:.1f} fps). Stats: {cam.stream_stats()}")
        cats = cam.feature_tree()
        print(f"Feature tree: {len(cats)} categories, {sum(len(n) for _, n in cats)} features")
    finally:
        cam.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
