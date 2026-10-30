"""P1 check: workerdisplay + appdev + display_driver + LVGL in a worker, with relayed input."""
import board_config  # board_configs/workerdisplay
import appdev

app = appdev.App.current() or appdev.App(board_config)
import display_driver  # noqa: F401
import lvgl as lv
from js import self as worker

clicks = 0
scr = lv.screen_active()
btn = lv.button(scr)
btn.set_size(240, 90)
btn.center()
label = lv.label(btn)
label.set_text("clicks: 0")
label.center()


def on_click(e):
    global clicks
    clicks += 1
    label.set_text("clicks: %d" % clicks)
    worker.clicks = clicks
    print("clicked", clicks)


btn.add_event_cb(on_click, lv.EVENT.CLICKED, None)
worker.clicks = 0
print("kit ui up", board_config.display_drv.width, board_config.display_drv.height)
app.run()


def _report():
    loop = display_driver.event_loop.current_instance()
    import multimer
    print("lvgl passes", loop.passes if loop else None, "paused", loop._pause if loop else None,
          "timer running", loop._timer.running if loop else None, "multimer", multimer.__name__)


from js import setTimeout
from pyodide.ffi import create_proxy

setTimeout(create_proxy(_report), 3000)
