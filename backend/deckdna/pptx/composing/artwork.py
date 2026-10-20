"""Geometry shared by donor cleanup, frame extraction and layer restoration."""


def covers_canvas(x: int, y: int, w: int, h: int, sw: int, sh: int) -> bool:
    """Area alone cannot distinguish an inset figure from a slide background."""
    if sw <= 0 or sh <= 0:
        return False
    covered = max(0, min(sw, x + w) - max(0, x)) * max(0, min(sh, y + h) - max(0, y))
    return w >= .95 * sw and h >= .95 * sh and covered >= .9 * sw * sh
