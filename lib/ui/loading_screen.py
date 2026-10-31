# loading_screen.py - star animation (with optional animated text) while something loads
#
# Star frames made with OLED Display Animation Maker (https://www.oledanimationmaker.com/,
# author: Ashish / ashish_y_794); artist: Feiyan Su.
#
# The frames are in loading_frames.bin (7 frames x 1024 bytes, MONO_HLSB, 128 x 64):
#   frames 0-4 = intro: the stars appear, played once, slow to fast (INTRO_MS)
#   frames 5-6 = loop: alternated during the pause and while loading
# Only ONE frame is in RAM at a time (1 KB): each frame is read from flash when it is shown.
#
# What run() plays, in order:
#   1. the intro, all 5 frames, with intro_text   (nothing loads yet, so it never stalls)
#   2. a pause of pause_ms: loop frames, still with intro_text
#   3. loading: loop frames with text, while loading_fn runs
#
# Usage:
#   from ui import loading_screen
#   loading_screen.run()                                    # intro only
#   loading_screen.run(intro_text="hello!", pause_ms=5000)  # intro + text, then a 5 s pause
#   loading_screen.run(intro_text=loading_screen.typed("hello!", 350))   # typed, 1 letter / 350 ms
#   loading_screen.run(my_generator, text=("wait.", "wait.."))   # intro, then loading
#   loading_screen.run(my_function, text="saving")          # one frame while it runs
#
# In your own loop (e.g. while searching the radio), use Animation directly:
#   anim = loading_screen.Animation(text=(".  ", ".. ", "..."))
#   while not done: ...; anim.tick()     # call often: the stars and text move on
#   anim.close()

import time
import framebuf

from peripherals import oled, OLED_W, OLED_H
from utils import centred
from logger import log, log_exception, WARN

FRAMES_FILE = "/lib/bitmaps/loading_frames.bin"  # in the board's /lib/bitmaps folder
FRAME_BYTES = OLED_W * OLED_H // 8  # 1024 bytes per 128 x 64 frame
INTRO_MS = (700, 550, 400, 280, 200)  # frames 0-4: each one shorter = stars appear slow to fast
LOOP_FIRST = 5  # frames 5-6: alternated
LOOP_FRAMES = 2
FRAME_MS = 300  # how long each loop frame stays up
TEXT_MS = 400  # how long each text step stays up
TEXT_Y = 28  # middle row: the stars leave the middle empty


def typed(text, letter_ms):
    """Text that types itself one letter at a time, every letter_ms (use as text or
    intro_text). Padded with spaces so the centred text doesn't move as it grows."""

    def step(elapsed_ms):
        n = min(len(text), 1 + elapsed_ms // letter_ms)
        return text[:n] + " " * (len(text) - n)

    return step


class Animation:
    """The star frames and text. Call tick() often; it never blocks, so your own
    code (imports, radio...) keeps running. With intro=True it starts with the
    stars appearing (frames 0-4, slow to fast), then loops frames 5-6."""

    def __init__(self, text=None, text_y=TEXT_Y, text_ms=TEXT_MS, intro=True):
        self.text_y = text_y
        self.text_ms = text_ms
        self.buf = bytearray(FRAME_BYTES)  # the one frame in RAM
        self.fb = framebuf.FrameBuffer(self.buf, OLED_W, OLED_H, framebuf.MONO_HLSB)
        try:
            self.file = open(FRAMES_FILE, "rb")
        except OSError:
            log(WARN, "loading_screen:", FRAMES_FILE, "not found, text only")
            self.file = None  # no stars, text still works
        self.loop_i = 0
        self.intro_i = None  # intro frame showing, None = looping
        self.last_frame = time.ticks_ms()
        self.drawn = None  # the text step last drawn
        self.set_text(text)
        if intro:
            self.start_intro()
        else:
            self.load(LOOP_FIRST)
        self.draw()

    def set_text(self, text):
        """text: None | "a string" | a list/tuple of steps | a function(elapsed_ms).
        A step is "text" (drawn at text_y), ("text", y), or several lines:
        (("line 1", y1), ("line 2", y2))."""
        self.text = text
        self.text_step = 0
        self.text_start = self.last_text = time.ticks_ms()
        self.text_moves = text is not None and not isinstance(text, str)

    def load(self, index):
        if self.file is not None:
            self.file.seek(index * FRAME_BYTES)
            self.file.readinto(self.buf)  # overwrite the 1 KB buffer

    def start_intro(self):
        self.intro_i = 0
        self.load(0)
        self.last_frame = time.ticks_ms()

    def play_intro(self):
        """Frames 0-4, each for its INTRO_MS, text kept moving. Always plays to the end."""
        self.start_intro()
        self.draw()
        while self.intro_i is not None:
            self.tick()
            time.sleep_ms(10)

    def _next_frame(self):
        if self.intro_i is not None:  # intro: frames 0-4 once
            self.intro_i += 1
            if self.intro_i < len(INTRO_MS):
                self.load(self.intro_i)
                return
            self.intro_i = None  # intro done: start the loop
            self.loop_i = 0
            self.load(LOOP_FIRST)
            return
        self.loop_i = (self.loop_i + 1) % LOOP_FRAMES  # loop: alternate frames 5-6
        self.load(LOOP_FIRST + self.loop_i)

    def tick(self):
        """Advance the stars and/or the text when their time is up, then redraw.
        Cheap to call often: it does nothing until something is due."""
        now = time.ticks_ms()
        frame_ms = INTRO_MS[self.intro_i] if self.intro_i is not None else FRAME_MS
        frame_due = time.ticks_diff(now, self.last_frame) >= frame_ms
        if callable(self.text):  # e.g. typed(): redraw when it changes
            text_due = self._text_now() != self.drawn
        else:
            text_due = self.text_moves and time.ticks_diff(now, self.last_text) >= self.text_ms
        if not (frame_due or text_due):
            return
        if frame_due:
            self.last_frame = now
            self._next_frame()
        if text_due:
            self.last_text = now
            self.text_step += 1
        self.draw()

    def hold(self, ms):
        """Keep the loop frames and text moving for ms milliseconds."""
        end = time.ticks_add(time.ticks_ms(), ms)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            self.tick()
            time.sleep_ms(10)

    def _text_now(self):
        t = self.text
        if t is None:
            return None
        if isinstance(t, str):
            step = t
        elif callable(t):
            step = t(time.ticks_diff(time.ticks_ms(), self.text_start))
        else:
            step = t[self.text_step % len(t)]  # cycle through the steps
        if step is None:
            return None
        if isinstance(step, str):
            return ((step, self.text_y),)
        if isinstance(step[0], str):
            return ((step[0], step[1]),)  # one ("text", y)
        return step  # several (("text", y), ...) lines

    def draw(self):
        oled.fill(0)
        if self.file is not None:
            oled.blit(self.fb, 0, 0)
        step = self._text_now()
        if step is not None:
            for text, y in step:
                centred(text, y)  # text on top of the stars
        self.drawn = step
        oled.show()

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


def run(
    loading_fn=None, text=None, intro_text=None, pause_ms=0, text_y=TEXT_Y, text_ms=TEXT_MS, min_ms=0, on_error="raise"
):
    """Play the star intro, an optional pause, then the loop while loading_fn runs.

    loading_fn : None                 -> no loading step
                 a normal function    -> runs once; the screen holds one frame while it runs
                 a generator function -> (a function that uses `yield`) the stars and text
                                         keep moving between its steps
    text       : shown while loading: None, "fixed text", a list/tuple of steps that
                 play in a loop, or a function(elapsed_ms) -> step. A step is "text"
                 or ("text", y). Keep text to 16 characters (8 px per character).
    intro_text : shown during the intro and the pause (same kinds as text)
    pause_ms   : after the intro, loop the stars this long before loading starts
    text_y     : row for steps that don't give their own (28 = middle of the screen)
    text_ms    : how long each text step stays up
    min_ms     : keep the loading part up at least this long
    on_error   : "raise"  -> show "loading failed", log the traceback, raise the error again
                 "return" -> the same, but return None instead of raising

    Returns what a normal loading_fn returned (None for a generator or no loading_fn).
    """
    screen = Animation(intro_text, text_y, text_ms, intro=True)  # intro starts at once

    def loading_fn_wrapper():
        """Run loading_fn and catch any error in it. Returns (ok, result_or_error)."""
        try:
            result = loading_fn()
            if hasattr(result, "send"):  # a generator function was given:
                for _ in result:  #   run it step by step,
                    screen.tick()  #   moving the animation between steps
                result = None
            return True, result
        except Exception as err:  # any error inside loading_fn lands here
            return False, err

    ok, value = True, None
    try:
        # 1. intro, always played in full (nothing is loading yet, so it can't stall)
        while screen.intro_i is not None:
            screen.tick()
            time.sleep_ms(10)
        # 2. pause
        if pause_ms:
            screen.hold(pause_ms)
        # 3. loading
        if loading_fn is not None or min_ms:
            screen.set_text(text)
            screen.draw()
            start = time.ticks_ms()
            if loading_fn is not None:
                ok, value = loading_fn_wrapper()
            left = min_ms - time.ticks_diff(time.ticks_ms(), start)
            if left > 0:
                screen.hold(left)
    finally:
        screen.close()  # always release the file, even on Ctrl+C

    if not ok:
        oled.fill(0)
        centred("loading failed", 28)
        oled.show()
        log_exception(value, "loading failed")  # full traceback to the console and log
        time.sleep_ms(2000)
        oled.fill(0)
        oled.show()
        if on_error == "raise":
            raise value
        return None

    oled.fill(0)
    oled.show()
    return value
