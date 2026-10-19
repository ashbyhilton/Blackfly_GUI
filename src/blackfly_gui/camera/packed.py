"""Unpackers for GenICam packed pixel formats (pure numpy, no PySpin)."""

from __future__ import annotations

import numpy as np


def unpack_mono12p(raw: np.ndarray, width: int, height: int) -> np.ndarray:
    """GenICam Mono12p: 2 pixels in 3 bytes, LSB first."""
    b = raw[: width * height * 3 // 2].reshape(-1, 3).astype(np.uint16)
    out = np.empty((b.shape[0], 2), dtype=np.uint16)
    out[:, 0] = b[:, 0] | ((b[:, 1] & 0x0F) << 8)
    out[:, 1] = (b[:, 1] >> 4) | (b[:, 2] << 4)
    return out.reshape(height, width)


def unpack_mono10p(raw: np.ndarray, width: int, height: int) -> np.ndarray:
    """GenICam Mono10p: 4 pixels in 5 bytes, LSB first."""
    b = raw[: width * height * 5 // 4].reshape(-1, 5).astype(np.uint16)
    out = np.empty((b.shape[0], 4), dtype=np.uint16)
    out[:, 0] = b[:, 0] | ((b[:, 1] & 0x03) << 8)
    out[:, 1] = (b[:, 1] >> 2) | ((b[:, 2] & 0x0F) << 6)
    out[:, 2] = (b[:, 2] >> 4) | ((b[:, 3] & 0x3F) << 4)
    out[:, 3] = (b[:, 3] >> 6) | (b[:, 4] << 2)
    return out.reshape(height, width)
