from __future__ import annotations

from typing import Any


class QRDetectorUnavailable(RuntimeError):
    """The independent QR detector cannot run in this environment."""


def detect_qr(frame: Any) -> bool:
    try:
        import cv2
    except ImportError as exc:
        raise QRDetectorUnavailable("OpenCV QR detector is unavailable") from exc
    if frame is None:
        return False
    try:
        import numpy as np
        array = np.asarray(frame)
        detector = cv2.QRCodeDetector()
        value, points, _ = detector.detectAndDecode(array)
        return bool(value or points is not None)
    except Exception as exc:  # OpenCV backend differences must not crash the scan.
        raise QRDetectorUnavailable(f"OpenCV QR detector failed: {exc}") from exc
