import numpy as np

from blackfly_gui.camera.packed import unpack_mono10p, unpack_mono12p


def pack12p(px: np.ndarray) -> np.ndarray:
    p = px.reshape(-1, 2).astype(np.uint32)
    out = np.empty((p.shape[0], 3), dtype=np.uint8)
    out[:, 0] = p[:, 0] & 0xFF
    out[:, 1] = ((p[:, 0] >> 8) & 0x0F) | ((p[:, 1] & 0x0F) << 4)
    out[:, 2] = p[:, 1] >> 4
    return out.ravel()


def pack10p(px: np.ndarray) -> np.ndarray:
    bits = 0
    out = bytearray()
    acc = 0
    for v in px.ravel().tolist():
        acc |= v << bits
        bits += 10
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    return np.frombuffer(bytes(out), dtype=np.uint8)


def test_mono12p_roundtrip():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 4096, size=(6, 8), dtype=np.uint16)
    np.testing.assert_array_equal(unpack_mono12p(pack12p(img), 8, 6), img)


def test_mono10p_roundtrip():
    rng = np.random.default_rng(1)
    img = rng.integers(0, 1024, size=(4, 8), dtype=np.uint16)
    np.testing.assert_array_equal(unpack_mono10p(pack10p(img), 8, 4), img)
