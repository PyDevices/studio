"""Where each region of the window goes.

Every size here comes from the window size through one scale factor, and
each layout is a table of region rectangles. A phone or 1280x720 layout is
another table, not another set of screens.
"""

BASE_W, BASE_H = 1920, 1080


def scale_for(w, h):
    return max(0.6, min(w / BASE_W, h / BASE_H))


def desktop(w, h):
    s = scale_for(w, h)
    top = int(72 * s)
    left = int(300 * s)
    right = int(400 * s)
    keys = int(150 * s)
    status = int(30 * s)
    mid_h = h - top - keys - status
    return {
        "scale": s,
        "top": (0, 0, w, top),
        "tracks": (0, top, left, mid_h),
        "editor": (left, top, w - left - right, mid_h),
        "inspector": (w - right, top, right, mid_h),
        "keys": (0, top + mid_h, w, keys),
        "status": (0, h - status, w, status),
    }


def pick(w, h):
    """The layout for this window. Only the desktop table exists so far."""
    return desktop(w, h)
