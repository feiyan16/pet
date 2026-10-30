import time
import framebuf

from peripherals import *
from logger import log, INFO
from bitmaps import stars


class Choice:
    def __init__(self, value):
        self.value = value


def centred(text, y):
    oled.text(text, (OLED_W - len(text) * 8) // 2, y)   # built-in font: 8 px per character


def back(optional_args=None):
    """must be used at the end of another function, return statement only returns from this function"""
    return


def btn_handler(l_value, r_value, l_pressed, r_pressed, l_action=None, r_action=None):
    """
    Returns (both_selected, l_pressed, r_pressed).
    True/False are passed by value, so the updated states are returned for the
    caller to keep and pass back in on the next call.
    """
    if l_value == 1:
        l_pressed = True
    elif l_pressed:
        l_pressed = False
        if l_action is not None:
            l_action()

    if r_value == 1:
        r_pressed = True
    elif r_pressed:
        r_pressed = False
        if r_action is not None:
            r_action()

    if l_pressed and r_pressed:
        return True, False, False

    return False, l_pressed, r_pressed


SCROLL_TIMEOUT_MS = 5000    # a menu/popup with no touch for this long closes by itself


def scroll(menu, timeout_ms=SCROLL_TIMEOUT_MS, on_idle=None):
    """Show a Menu or Popup. LEFT = previous, RIGHT = next, both = select.
    Returns True if an option was selected.
    Returns False if no pad was touched for timeout_ms: nothing is selected, so
    the caller acts as if "back"/"exit" was chosen. timeout_ms=None waits forever.
    on_idle: optional function called about every 100 ms while the menu is up
    (e.g. to keep the radio answering)."""
    menu.display()
    time.sleep(0.3)

    l_prsd, r_prsd = False, False
    last_touch = time.ticks_ms()
    while True:
        l, r = left_btn.value(), right_btn.value()
        if l or r:
            last_touch = time.ticks_ms()        # any touch restarts the 5 s

        selected, l_prsd, r_prsd = btn_handler(l, r, l_prsd, r_prsd, l_action=menu.prev, r_action=menu.next)

        if selected:
            menu.select()
            log(INFO, "selected:", menu.current)
            return True

        if timeout_ms is not None and time.ticks_diff(time.ticks_ms(), last_touch) >= timeout_ms:
            log(INFO, "menu timed out: nothing selected")
            return False                        # timed out: nothing selected

        if on_idle is not None:
            on_idle()
        time.sleep(0.1)


def wait_release():
    """Wait until neither touch pad is touched (so a held finger isn't read as a new tap)."""
    while left_btn.value() or right_btn.value():
        time.sleep_ms(10)


# the countdown star is the shared star from bitmaps/stars.py (11 x 10 px)
_star_fb = framebuf.FrameBuffer(stars.STAR, stars.STAR_W, stars.STAR_H, framebuf.MONO_HLSB)
STAR_GAP = 8                # space between the countdown stars (px)


def countdown(ready="READY", ready_ms=700, count=3, step_ms=700):
    """Before a game: "READY" in the middle for ready_ms, then stars in the middle
    of the screen (count of them) that disappear one by one, from the right, every step_ms.
    When the last star is gone the game starts. ready=None skips the READY frame."""
    if ready:
        oled.fill(0)
        centred(ready, 28)                    # middle row (built-in font, 8 px)
        oled.show()
        time.sleep_ms(ready_ms)
    total_w = count * stars.STAR_W + (count - 1) * STAR_GAP
    x0 = (OLED_W - total_w) // 2              # the stars stay in place as they go
    y = (OLED_H - stars.STAR_H) // 2
    for left in range(count, 0, -1):          # 3 stars, then 2, then 1
        oled.fill(0)
        for i in range(left):
            oled.blit(_star_fb, x0 + i * (stars.STAR_W + STAR_GAP), y, 0)
        oled.show()
        time.sleep_ms(step_ms)
    oled.fill(0)                              # all gone: go!
    oled.show()
