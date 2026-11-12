# utils.py - small helpers shared by the menus, games and pet modes
#
#   Choice        a box for one value that inner functions can change
#   centred()     text centred across the screen
#   back()        a do-nothing menu action ("Back")
#   btn_handler() turns pad readings into taps (on release) and "both pads" presses
#   scroll()      runs a Menu / Popup until something is selected or it times out
#   wait_release() waits until no pad is touched
#   countdown()   "READY" + 3 stars disappearing, before a game

import time  # sleep, ticks_ms
import framebuf  # FrameBuffer: wraps bitmap bytes so they can be blit()-ed

from peripherals import *  # oled, OLED_W, OLED_H, left_btn, right_btn
from logger import log, INFO
from bitmaps import stars  # the star bitmaps (countdown)


class Choice:
    """A box holding one value. An inner function (e.g. a menu's on_select
    callback) can't assign to a plain variable of the function around it, but
    it can change box.value, and the outer function then reads it."""

    def __init__(self, value):
        self.value = value  # the value in the box (anything)


def centred(text, y):
    """Draw text centred left-right, with its top at row y (doesn't call show())."""
    oled.text(text, (OLED_W - len(text) * 8) // 2, y)  # built-in font: 8 px per character


def back(optional_args=None):
    """A menu action that does nothing: picking "Back" just returns, and the
    caller's loop carries on (or ends). Takes an optional argument so it can be
    called like the other actions."""
    return


def btn_handler(l_value, r_value, l_pressed, r_pressed, l_action=None, r_action=None):
    """
    Call this in a loop with the current pad readings (1 = touched).
    A single pad counts as a tap when it is RELEASED: then l_action / r_action runs.
    Both pads down at the same moment counts as "both" (select): no tap actions
    run for that press.

    Returns (both_selected, l_pressed, r_pressed).
    True/False are passed by value, so the updated states are returned for the
    caller to keep and pass back in on the next call.
    """
    if l_value == 1:
        l_pressed = True  # finger is on the left pad
    elif l_pressed:  # the finger was on the pad last time and is now off: a tap
        l_pressed = False
        if l_action is not None:
            l_action()

    if r_value == 1:
        r_pressed = True  # finger is on the right pad
    elif r_pressed:  # released: a tap
        r_pressed = False
        if r_action is not None:
            r_action()

    if l_pressed and r_pressed:  # both pads held together
        return True, False, False  # "select"; forget both presses so releasing them isn't a tap

    return False, l_pressed, r_pressed


SCROLL_TIMEOUT_MS = 5000  # a menu/popup with no touch for this long closes by itself


def scroll(menu, timeout_ms=SCROLL_TIMEOUT_MS, on_idle=None):
    """Show a Menu or Popup. LEFT = previous, RIGHT = next, both = select.
    Returns True if an option was selected.
    Returns False if no pad was touched for timeout_ms: nothing is selected, so
    the caller acts as if "back"/"exit" was chosen. timeout_ms=None waits forever.
    on_idle: optional function called about every 100 ms while the menu is up
    (e.g. to keep the radio answering)."""
    menu.display()  # draw it once
    time.sleep(0.3)  # a short pause so the touch that opened the menu isn't read as a tap

    l_prsd, r_prsd = False, False  # no pad held yet
    last_touch = time.ticks_ms()  # for the timeout
    while True:
        l, r = left_btn.value(), right_btn.value()  # read both pads (1 = touched)
        if l or r:
            last_touch = time.ticks_ms()  # any touch restarts the 5 s

        selected, l_prsd, r_prsd = btn_handler(l, r, l_prsd, r_prsd, l_action=menu.prev, r_action=menu.next)

        if selected:  # both pads: pick the highlighted option
            menu.select()  # runs the menu's on_select callback
            log(INFO, "selected:", menu.current)
            return True

        if timeout_ms is not None and time.ticks_diff(time.ticks_ms(), last_touch) >= timeout_ms:
            log(INFO, "menu timed out: nothing selected")
            return False  # timed out: nothing selected

        if on_idle is not None:
            on_idle()
        time.sleep(0.1)  # check the pads about 10 times a second


def wait_release():
    """Wait until neither touch pad is touched (so a held finger isn't read as a new tap)."""
    while left_btn.value() or right_btn.value():
        time.sleep_ms(10)


# the countdown star is the shared star from bitmaps/stars.py (11 x 10 px)
_star_fb = framebuf.FrameBuffer(stars.STAR, stars.STAR_W, stars.STAR_H, framebuf.MONO_HLSB)
STAR_GAP = 8  # space between the countdown stars (px)


def countdown(ready="READY", ready_ms=700, count=3, step_ms=700):
    """Before a game: "READY" in the middle for ready_ms, then stars in the middle
    of the screen (count of them) that disappear one by one, from the right, every step_ms.
    When the last star is gone the game starts. ready=None skips the READY frame."""
    if ready:
        oled.fill(0)  # clear the screen
        centred(ready, 28)  # middle row (built-in font, 8 px)
        oled.show()
        time.sleep_ms(ready_ms)
    total_w = count * stars.STAR_W + (count - 1) * STAR_GAP  # width of the whole row of stars
    x0 = (OLED_W - total_w) // 2  # the stars stay in place as they go
    y = (OLED_H - stars.STAR_H) // 2  # vertically centred
    for left in range(count, 0, -1):  # 3 stars, then 2, then 1
        oled.fill(0)
        for i in range(left):
            # last argument 0 = the star's dark pixels are see-through
            oled.blit(_star_fb, x0 + i * (stars.STAR_W + STAR_GAP), y, 0)
        oled.show()
        time.sleep_ms(step_ms)
    oled.fill(0)  # all gone: go!
    oled.show()
