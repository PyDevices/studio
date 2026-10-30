"""Colours, spacing and the type scale, defined once, as shared LVGL styles.

Every widget takes its look from a handful of shared styles; a pressed,
checked or selected look is a state on the same style, never a restyle
(restyling objects one by one is what made a board UI take 600 ms a tap).
"""

import lvgl as lv

import fonts

BG = 0x0E1116          # the window
PANEL = 0x161A22       # regions
RAISED = 0x1F2430      # controls, rows
HOVER = 0x283042       # a button under the mouse
PRESSED = 0x313B52     # a button held down
LINE = 0x2A3140        # borders, grid lines
LINE_BAR = 0x3C4558    # bar lines on the grid
TEXT = 0xE6E9EF
MUTED = 0x8A93A6
ACCENT = 0xFF7A45      # play head, selection
ACCENT_HOVER = 0xFF9466  # a checked button under the mouse
REC = 0xE5484D
OK = 0x46A758
WARN = 0xF5A524

# One colour per track, in order.
TRACK_COLOURS = (0xFF7A45, 0x3DB4F2, 0xB28DFF, 0x46D39A, 0xF5C542, 0xF26CA7, 0x5AD2D2, 0xA3B4C8)

S = 1.0                # the layout's scale, set by setup()
_styles = {}


def c(hexval):
    return lv.color_hex(hexval)


def px(n):
    """A design size (at 1920x1080) in pixels at the current scale."""
    return max(1, int(n * S + 0.5))


def track_colour(index):
    return TRACK_COLOURS[index % len(TRACK_COLOURS)]


def _style(**props):
    st = lv.style_t()
    st.init()
    for name, value in props.items():
        getattr(st, "set_" + name)(value)
    return st


def setup(scale):
    """Build the shared styles for this scale. Call once fonts are loaded."""
    global S
    S = scale
    _styles["panel"] = _style(bg_color=c(PANEL), bg_opa=255, radius=0, border_width=0,
                              pad_all=0, shadow_width=0)
    _styles["panel_edge"] = _style(border_color=c(LINE), border_width=1)
    _styles["button"] = _style(bg_color=c(RAISED), bg_opa=255, radius=px(6), border_width=0,
                               shadow_width=0, pad_all=px(4), text_color=c(TEXT), text_font=fonts.t(px(16)))
    _styles["button_hovered"] = _style(bg_color=c(HOVER))
    _styles["button_pressed"] = _style(bg_color=c(PRESSED))
    _styles["button_checked"] = _style(bg_color=c(ACCENT), text_color=c(BG))
    # LVGL ranks HOVERED above CHECKED: without this, a checked button under
    # the mouse would lose its colour.
    _styles["button_checked_hovered"] = _style(bg_color=c(ACCENT_HOVER), text_color=c(BG))
    _styles["text"] = _style(text_color=c(TEXT), text_font=fonts.t(px(16)))
    _styles["muted"] = _style(text_color=c(MUTED), text_font=fonts.t(px(14)))
    _styles["title"] = _style(text_color=c(TEXT), text_font=fonts.t(px(20)))
    _styles["big"] = _style(text_color=c(TEXT), text_font=fonts.t(px(28)))
    _styles["icon"] = _style(text_font=fonts.i(px(24)))
    _styles["icon_big"] = _style(text_font=fonts.i(px(32)))


def style(obj, *names, selector=0):
    for n in names:
        obj.add_style(_styles[n], selector)


def panel(parent, rect, edge=True):
    x, y, w, h = rect
    p = lv.obj(parent)
    p.remove_style_all()
    style(p, "panel")
    if edge:
        style(p, "panel_edge")
    p.set_pos(x, y)
    p.set_size(w, h)
    p.remove_flag(lv.OBJ_FLAG.SCROLLABLE)
    return p


def box(parent, x, y, w, h, colour=None, radius=0):
    """A plain rectangle: no scrolling, no theme padding."""
    b = lv.obj(parent)
    b.remove_style_all()
    b.set_pos(x, y)
    b.set_size(w, h)
    if colour is not None:
        b.set_style_bg_color(c(colour), 0)
        b.set_style_bg_opa(255, 0)
    if radius:
        b.set_style_radius(radius, 0)
    b.remove_flag(lv.OBJ_FLAG.SCROLLABLE)
    return b


def label(parent, text, kind="text", colour=None):
    lb = lv.label(parent)
    style(lb, kind)
    if colour is not None:
        lb.set_style_text_color(c(colour), 0)
    lb.set_text(text)
    return lb


class Button:
    """An LVGL button and its label (LVGL objects take no new attributes)."""

    def __init__(self, obj, label):
        self.obj = obj
        self.label = label

    def on(self, event, fn):
        self.obj.add_event_cb(lambda e: fn(), event, None)
        return self

    @property
    def checked(self):
        return bool(self.obj.has_state(lv.STATE.CHECKED))

    @checked.setter
    def checked(self, on):
        if on:
            self.obj.add_state(lv.STATE.CHECKED)
        else:
            self.obj.remove_state(lv.STATE.CHECKED)

    def set_text(self, text):
        self.label.set_text(text)


def button(parent, text="", icon=None, checkable=False, w=None, h=None, kind="button"):
    """A themed button holding text, an icon (a Material Icons character), or both."""
    import icons

    b = lv.button(parent)
    b.remove_style_all()
    style(b, kind)
    style(b, "button_hovered", selector=lv.STATE.HOVERED)
    style(b, "button_pressed", selector=lv.STATE.PRESSED)
    style(b, "button_checked", selector=lv.STATE.CHECKED)
    style(b, "button_checked_hovered", selector=lv.STATE.CHECKED | lv.STATE.HOVERED)
    if checkable:
        b.add_flag(lv.OBJ_FLAG.CHECKABLE)
    if w and h:
        b.set_size(w, h)
    if icon and text:
        # An icon and a word: two labels in a centred row, each at its own
        # font's height. One label at the icon font's height cut the word's
        # descenders off ("Song" read "Sona"). The word takes the button's
        # colour, so checked and hovered states recolour it too.
        b.set_flex_flow(lv.FLEX_FLOW.ROW)
        b.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
        b.set_style_pad_column(px(6), 0)
        ic = lv.label(b)
        style(ic, "icon")
        ic.set_text(icons.ICONS[icon])
        lb = lv.label(b)
        lb.set_style_text_font(fonts.t(px(16)), 0)
        lb.set_text(text)
        return Button(b, lb)
    lb = lv.label(b)
    if icon:
        style(lb, "icon")
        lb.set_text(icons.ICONS[icon])
    else:
        lb.set_text(text)
    lb.center()
    return Button(b, lb)
