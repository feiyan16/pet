# roboface.py - animated robot face (eyes + mouth) for MicroPython
#
# Draws an animated face from YOUR eye bitmap (framebuf.MONO_HLSB) into any
# FrameBuffer-based display. API modelled on micropython-roboeyes by mchobby
# (itself a port of FluxGarage RoboEyes). Written from scratch.
#
# Requires MicroPython v1.20 or newer (FrameBuffer.ellipse / FrameBuffer.poly).
#
# Copyright (C) 2026 RoboFace contributors
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. This program is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.txt.

import framebuf as _framebuf
import time as _time
import random as _random
from array import array as _array

__version__ = "1.0.0"

# --- Switches ---------------------------------------------------------------
ON = 1
OFF = 0

# --- Moods (DEFAULT is shared with positions) -------------------------------
DEFAULT = 0
TIRED = 1
ANGRY = 2
HAPPY = 3
FROZEN = 4
SCARY = 5
CURIOUS = 6
LOVE = 7
SAD = 8
SURPRISED = 9

# --- Positions --------------------------------------------------------------
N = 1
NE = 2
E = 3
SE = 4
S = 5
SW = 6
W = 7
NW = 8

_MOUTH_H = 8          # vertical room reserved for the mouth (px)
_SPACING = 28         # default gap between the eye bitmaps (px)
_MOUTH_GAP = -4       # mouth top vs eye bottom (px); negative = raised
                      # into the space between the eyes
_MOUTH_W_PCT = 104    # mouth width as % of eye width
_MOUTH_T = 3          # mouth line thickness (px)
_CHEEK_R = 3          # laughing cheek radius (px)
_LOVE_FRAME_MS = 80   # duration of each of the 4 LOVE transition frames
_CACHE_MAX = 24       # max number of resized bitmaps kept in RAM
_POS = {N: (0, -1), NE: (1, -1), E: (1, 0), SE: (1, 1),
        S: (0, 1), SW: (-1, 1), W: (-1, 0), NW: (-1, -1), DEFAULT: (0, 0)}
_WAVE = (0, 1, 2, 1, 0, -1, -2, -1)

# ANGRY lean-in zoom: the face grows toward the viewer, more at the top
_ANG_STEPS = (0, 55, 85, 97, 100)   # zoom progress (%) of each stage
_ANG_STAGE_MS = 50                  # time per stage (4 stages = 200 ms)
_ANG_TOP = 1.32                     # full zoom at the top of the face
_ANG_BOT = 1.06                     # full zoom at the bottom of the face
_ANG_LID = 50                       # angry eyelid depth (% of eye height)
# anger vein: one quadrant as a pixel path around the mark's centre
# (the other three are mirror images), and its pop / throb sizes in %
_VEIN = ((-3, -6), (-2, -5), (-2, -2), (-5, -2), (-6, -3))
_VEIN_POP = (50, 125, 125, 100)     # one size per 40 ms after it appears
_VEIN_THROB_MS = 400                # then alternates 100% / 125%

# feed(): stars pour straight down into the EAT mouth
_FEED_STARS = 6                     # stars per feed() call
_FEED_SPAWN_MS = 110                # one new star every 110 ms
_FEED_FALL_MS = 520                 # time for a star to fall into the mouth
_EAT_SCALE = 40                     # the whole face shrinks to this %
# stars get smaller as they fall: 7 px, 5 px, then 3 px near the mouth
_FEED_STAR = (("...#...", "...#...", "#######", ".#####.", "..###..",
               ".##.##.", ".#...#."),
              ("..#..", "..#..", "#####", ".###.", ".#.#."),
              (".#.", "###", ".#."))


def _ticks():
    return _time.ticks_ms()


def _since(t):
    return _time.ticks_diff(_time.ticks_ms(), t)


def _rand(n):
    # random.getrandbits exists on every port; randint may not
    if n <= 0:
        return 0
    return _random.getrandbits(16) % (n + 1)


def _ease(cur, target):
    # move half-way to target, guaranteed to arrive
    d = target - cur
    if -1 <= d <= 1:
        return target
    return cur + d // 2 if d > 0 else cur - ((-d) // 2)


class _Bitmap:
    # A MONO_HLSB bitmap with its FrameBuffer and a few measurements
    def __init__(self, buf, w, h):
        self.buf = buf
        self.w = w
        self.h = h
        self.fb = _framebuf.FrameBuffer(buf, w, h, _framebuf.MONO_HLSB)
        span = 0
        px = self.fb.pixel
        for y in range(h):
            first = -1
            last = -1
            for x in range(w):
                if px(x, y):
                    if first < 0:
                        first = x
                    last = x
            if first >= 0 and last - first + 1 > span:
                span = last - first + 1
        self.span = span if span else w   # widest row, used for closed eyes
        lpad = w                          # empty columns left / right of
        rpad = w                          # the drawing
        for y in range(h):
            for x in range(w):
                if px(x, y):
                    lpad = min(lpad, x)
                    rpad = min(rpad, w - 1 - x)
        self.lpad = lpad if lpad < w else 0
        self.rpad = rpad if rpad < w else 0
        bottom = 0                        # empty rows under the drawing
        for y in range(h - 1, -1, -1):
            row = False
            for x in range(w):
                if px(x, y):
                    row = True
                    break
            if row:
                break
            bottom += 1
        self.bottom = bottom if bottom < h else 0



def _check_len(buf, w, h, name):
    need = ((w + 7) // 8) * h
    if len(buf) != need:
        raise ValueError("%s must be %d bytes for %dx%d MONO_HLSB, got %d"
                         % (name, need, w, h, len(buf)))


def _scaled(src, w, h):
    # Smooth resize of a 1-bit bitmap (integer maths only, so it also works
    # on boards built without float support):
    #  - shrinking (both axes <= source): area average, a pixel is on when
    #    at least half of the source pixels it covers are on
    #  - otherwise: bilinear interpolation thresholded at 50%, which turns
    #    blocky enlargements into rounded, even edges
    buf = bytearray(((w + 7) // 8) * h)
    dst = _framebuf.FrameBuffer(buf, w, h, _framebuf.MONO_HLSB)
    sw = src.w
    sh = src.h
    get = src.fb.pixel
    put = dst.pixel
    if w <= sw and h <= sh:
        xs = [(x * sw // w, max(x * sw // w + 1, (x + 1) * sw // w))
              for x in range(w)]
        for y in range(h):
            y0 = y * sh // h
            y1 = max(y0 + 1, (y + 1) * sh // h)
            for x in range(w):
                x0, x1 = xs[x]
                on = 0
                for yy in range(y0, y1):
                    for xx in range(x0, x1):
                        on += get(xx, yy)
                if 2 * on >= (x1 - x0) * (y1 - y0):
                    put(x, y, 1)
    else:
        def axis(n, sn):
            # source position of each target pixel centre, in 1/256 px
            out = []
            for i in range(n):
                p = ((2 * i + 1) * sn * 256) // (2 * n) - 128
                a = p >> 8
                f = p & 255
                if a < 0:
                    a, f = 0, 0
                b = a + 1 if a + 1 < sn else a
                out.append((a, b, f))
            return out
        xs = axis(w, sw)
        for y, (ya, yb, fy) in enumerate(axis(h, sh)):
            for x in range(w):
                xa, xb, fx = xs[x]
                top = get(xa, ya) * (256 - fx) + get(xb, ya) * fx
                bot = get(xa, yb) * (256 - fx) + get(xb, yb) * fx
                if top * (256 - fy) + bot * fy >= 32768:
                    put(x, y, 1)
    return _Bitmap(buf, w, h)


def _mirrored(src):
    buf = bytearray(len(src.buf))
    dst = _framebuf.FrameBuffer(buf, src.w, src.h, _framebuf.MONO_HLSB)
    get = src.fb.pixel
    last = src.w - 1
    for y in range(src.h):
        for x in range(src.w):
            if get(x, y):
                dst.pixel(last - x, y, 1)
    return _Bitmap(buf, src.w, src.h)


def _heart(w, h, k=4):
    # Filled heart, as wide as the box, height capped so it stays
    # heart-shaped, centred vertically. Drawn k times larger, then shrunk
    # with area averaging, which gives much smoother edges than drawing
    # directly at the final size.
    W = w * k
    H = h * k
    big = _Bitmap(bytearray(((W + 7) // 8) * H), W, H)
    fb = big.fb
    hh = min(H, (W * 9) // 10)
    top = (H - hh) // 2
    r = max(1, (W + 2) // 4)                            # lobe radius
    cy = top + r
    fb.ellipse(r - 1, cy, r, r, 1, True)                # left lobe
    fb.ellipse(W - r, cy, r, r, 1, True)                # right lobe
    mid = cy + r // 3
    fb.poly(0, 0, _array("h", [0, mid, W - 1, mid, W // 2, top + hh - 1]),
            1, True)
    fb.fill_rect(r - 1, cy, W - 2 * r + 2, mid - cy + 1, 1)
    out = _scaled(big, w, h)
    del big
    return out


def _keystone(src, ex, ey, top, bot, st, sb):
    # Lean-in warp of one eye bitmap. Coordinates are relative to the face
    # centre: the eye's top-left is (ex, ey); the face spans rows top..bot.
    # A row at height y is scaled horizontally by s(y), which goes from st at
    # the top of the face to sb at the bottom; rows are moved vertically by
    # the integral of s, so the face stays in one piece.
    # Works on runs of pixels per row (fast), and blends the edges of two
    # neighbouring source rows so enlarged edges stay smooth.
    # Returns (bitmap, x, y): the warped eye and its top-left position,
    # again relative to the face centre.
    w = src.w
    h = src.h
    get = src.fb.pixel
    spans = []
    for y in range(h):
        row = []
        x = 0
        while x < w:
            if get(x, y):
                a = x
                while x < w and get(x, y):
                    x += 1
                row.append((a, x))
            else:
                x += 1
        spans.append(row)
    b = (sb - st) / max(1, bot - top)
    a = st - b * top

    def s_at(y):
        return a + b * y

    def fwd(y):                         # source row -> output row
        return a * y + b * y * y / 2

    def inv(Y):                         # output row -> source row
        if b == 0:
            return Y / a
        d = a * a + 2 * b * Y
        return (-a + (d if d > 0 else 0) ** 0.5) / b

    y_top = fwd(ey)
    y_bot = fwd(ey + h)
    xs = (ex * s_at(ey), ex * s_at(ey + h),
          (ex + w) * s_at(ey), (ex + w) * s_at(ey + h))
    X0 = int(min(xs)) - 1
    X1 = int(max(xs)) + 2
    Y0 = int(y_top) - 1
    Y1 = int(y_bot) + 2
    ow = X1 - X0
    oh = Y1 - Y0
    obuf = bytearray(((ow + 7) // 8) * oh)
    fb = _framebuf.FrameBuffer(obuf, ow, oh, _framebuf.MONO_HLSB)
    for oy in range(oh):
        y = inv(Y0 + oy + 0.5)          # source position of this row centre
        r = y - ey - 0.5                # between source rows r0 and r0 + 1
        r0 = int(r) if r >= 0 else int(r) - 1
        f = r - r0
        ra = spans[r0] if 0 <= r0 < h else ()
        rb = spans[r0 + 1] if 0 <= r0 + 1 < h else ()
        if len(ra) == len(rb) and ra:
            row = [(sa[0] + (sb_[0] - sa[0]) * f, sa[1] + (sb_[1] - sa[1]) * f)
                   for sa, sb_ in zip(ra, rb)]
        else:
            row = rb if f >= 0.5 else ra
        if not row:
            continue
        sy = s_at(y)
        for p, q in row:
            # output pixel is on when its centre lies inside the span
            xa = int((ex + p) * sy - X0 + 0.5)
            xb = int((ex + q) * sy - X0 + 0.5)
            if xb > xa:
                fb.hline(xa, oy, xb - xa, 1)
    return _Bitmap(obuf, ow, oh), X0, Y0


# =============================================================================
class Sequence:
    """A named list of timed steps. Each step calls func(face)."""

    def __init__(self, face, name):
        self.face = face
        self.name = name
        self._steps = []      # [ms, func, done]
        self._start = None

    def step(self, ms_timing, _lambda):
        self._steps.append([ms_timing, _lambda, False])
        self._steps.sort(key=lambda s: s[0])
        return self

    def start(self):
        self.reset()
        self._start = _ticks()

    def reset(self):
        self._start = None
        for s in self._steps:
            s[2] = False

    @property
    def done(self):
        if self._start is None:
            return True
        for s in self._steps:
            if not s[2]:
                return False
        return True

    def update(self):
        if self._start is None:
            return
        elapsed = _since(self._start)
        for s in self._steps:
            if not s[2] and elapsed >= s[0]:
                s[2] = True
                s[1](self.face)


class Sequences(list):
    """List of Sequence objects, driven by RoboFace.update()."""

    def __init__(self, face):
        super().__init__()
        self.face = face

    def add(self, name):
        seq = Sequence(self.face, name)
        self.append(seq)
        return seq

    @property
    def done(self):
        for s in self:
            if not s.done:
                return False
        return True

    def update(self):
        for s in self:
            s.update()


# =============================================================================
class RoboFace:
    def __init__(self, fb, width, height, eye, eye_w, eye_h, name, 
                 right_eye=None, mirror=True,
                 frame_rate=20, on_show=None, bgcolor=0, fgcolor=1):
        _check_len(eye, eye_w, eye_h, "eye")
        if right_eye is not None:
            _check_len(right_eye, eye_w, eye_h, "right_eye")
        self.fb = fb
        self.name = name
        self.width = width
        self.height = height
        self.on_show = on_show
        self.bgcolor = bgcolor
        self.fgcolor = fgcolor
        self._period = 1000 // max(1, frame_rate)
        self._last = _ticks() - self._period
        self._frame_rate = frame_rate

        left = _Bitmap(bytearray(eye), eye_w, eye_h)
        if right_eye is not None:
            right = _Bitmap(bytearray(right_eye), eye_w, eye_h)
        elif mirror:
            right = _mirrored(left)
        else:
            right = left
        self._src = {"eye": (left, right)}
        # the eye bitmaps exactly as given, for to_json() (no extra copies)
        self._eye_args = (left.buf, right.buf if right_eye is not None
                          else None, bool(mirror))
        self._cache = {}
        self._order = []
        self._vb = {}

        # colors: a palette maps bitmap bits 0/1 to bgcolor/fgcolor
        self._pal = None
        if fgcolor != 1 or bgcolor != 0:
            self._pal = _framebuf.FrameBuffer(bytearray(4), 2, 1,
                                              _framebuf.RGB565)
            self._pal.pixel(0, 0, bgcolor)
            self._pal.pixel(1, 0, fgcolor)

        # geometry
        self._w = [eye_w, eye_w]
        self._h = [eye_h, eye_h]
        self._space = _SPACING
        self._mouth = height >= eye_h + _MOUTH_GAP + _MOUTH_H + 4
        self._mouth_w = max(6, (eye_w * _MOUTH_W_PCT) // 100)
        self._mouth_off = _MOUTH_GAP

        # eyelid openness per eye, 0..100
        self._op = [100, 100]
        self._op_target = [100, 100]
        self._reopen = [False, False]

        # position (offsets from centre) and direction
        self._ox = 0
        self._oy = 0
        self._tox = 0
        self._toy = 0
        self._position = DEFAULT
        self._maxx = 0
        self._maxy = 0

        # moods
        self._mood = DEFAULT
        self._shown = DEFAULT
        self._lid = {"top": 0, "angry": 0, "sad": 0, "happy": 0}
        self._curious = False
        self._love_dir = 0
        self._love_idx = 0
        self._love_t = 0
        self._surp_t = 0
        self._ang_z = 0             # ANGRY zoom stage, 0..4
        self._ang_t = 0
        self._angc = {}             # cache of warped eyes
        self._vein_t = None         # when the anger vein appeared

        # animations
        self._hflick = [False, 2]
        self._vflick = [False, 2]
        self._flip = False
        self._confuse_until = None
        self._laugh_until = None
        self._cry_t = None
        self._talk = False
        self._sleeping = False
        self._sleep_t = 0
        self._feed = None           # feed() state while eating

        # automatic behaviours
        self._ab = [False, 1, 4, 0]     # active, interval, variation, next
        self._idle = [False, 1, 4, 0]

        self.sequences = Sequences(self)

        # pre-compute the squashed frames used for blinking
        heights = []
        op = 100
        while op > 0:
            op = self._close_step(op)
            heights.append(op)
        while op < 100:
            op = self._open_step(op)
            heights.append(op)
        for side in (0, 1):
            for op in heights:
                h = (eye_h * op) // 100
                if 2 < h < eye_h:
                    self._get("eye", side, eye_w, h)

    # --------------------------------------------------------------------- copy
    # read-only data that copies share instead of duplicating: the eye
    # bitmaps, the resized-bitmap caches and the colour palette
    _SHARED = ("_src", "_cache", "_order", "_vb", "_angc", "_pal")

    def copy(self, fb=None, on_show=None, width=None, height=None):
        """Return a new, independent RoboFace with the same settings and
        current look (mood, eye sizes, spacing, mouth, position, automatic
        blink / idle settings...).

        The copy has its own state: changing its mood, blinking it, moving
        it, etc. does not affect the original, and the other way round.
        To save RAM it shares the eye bitmaps and resize caches with the
        original (they are never modified, only added to).
        The copy starts with no sequences.

        fb        : display for the copy (default: the same display)
        on_show   : show callback for the copy. Default: the original's
                    callback, or, when fb is given, one that calls fb.show()
        width,
        height    : drawing area of the copy (default: same as original)
        """
        new = object.__new__(type(self))      # keeps subclasses (add-ons)
        for k, v in self.__dict__.items():
            if k in RoboFace._SHARED:
                pass                      # shared, see _SHARED
            elif isinstance(v, list):
                v = list(v)               # own copy of state lists
            elif isinstance(v, dict):
                v = dict(v)               # own copy of state dicts
            setattr(new, k, v)
        new.sequences = Sequences(new)
        if self._feed is not None:        # a copy doesn't carry on eating
            new._feed = None
            new._ab[0] = self._feed["ab"]
            new._idle[0] = self._feed["idle"]
        if fb is not None:
            new.fb = fb
            if on_show is None and hasattr(fb, "show"):
                on_show = lambda face: fb.show()
        if on_show is not None:
            new.on_show = on_show
        if width is not None:
            new.width = width
        if height is not None:
            new.height = height
            new._mouth = height >= max(self._h) + _MOUTH_GAP + _MOUTH_H + 4
        return new

    def __copy__(self):
        # lets copy.copy(face) work when micropython-lib's copy module is
        # installed (it is not built in to MicroPython)
        return self.copy()

    # --------------------------------------------------------------------- json
    _JSON_VERSION = 1

    def to_json(self):
        """Return the face as a JSON string: the eye bitmaps, all settings,
        the mood, the position and the current state of the eyes.

        Not included (they can't be stored as JSON): the display and the
        on_show callback (pass them to from_json), sequences (they contain
        functions), and timers. One-shot animations in progress (blink,
        wink, confuse, laugh, cry) are not saved; the face is saved as it
        looks without them.

        Save it to a file with:
            with open("face.json", "w") as f:
                f.write(face.to_json())
        """
        import json
        from binascii import b2a_base64

        def b64(buf):
            return b2a_base64(buf).decode().strip()

        left, right, mirror = self._eye_args
        lid = self._lid
        d = {
            "roboface_json": RoboFace._JSON_VERSION,
            "class": type(self).__name__,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "frame_rate": self._frame_rate,
            "bgcolor": self.bgcolor,
            "fgcolor": self.fgcolor,
            "eye": {"w": self._src["eye"][0].w, "h": self._src["eye"][0].h,
                    "left": b64(left),
                    "right": b64(right) if right is not None else None,
                    "mirror": mirror},
            "settings": {
                "eyes_width": list(self._w),
                "eyes_height": list(self._h),
                "eyes_spacing": self._space,
                "mouth": self._mouth,
                "mouth_width": self._mouth_w,
                "mouth_offset": self._mouth_off,
                "curious": self._curious,
                "auto_blinker": list(self._ab[:3]),
                "idle_mode": list(self._idle[:3]),
                "horiz_flicker": list(self._hflick),
                "vert_flicker": list(self._vflick),
                "talk": self._talk,
            },
            "state": {
                "mood": self._mood,
                "position": self._position,
                "offset": [self._ox, self._oy],
                "target": [self._tox, self._toy],
                "sleeping": self._sleeping,
                # open/closed eyes (0..100); a blink in progress is saved
                # as open, a close() as closed
                "eyes_open": [0 if (t == 0 and not r) else 100
                              for t, r in zip(self._op_target, self._reopen)],
                "lids": [lid["top"], lid["angry"], lid["sad"], lid["happy"]],
                "angry_zoom": self._ang_z,
            },
        }
        return json.dumps(d)

    @classmethod
    def from_json(cls, text, fb, on_show=None):
        """Make a face from a to_json() string, drawing on display fb.

            with open("face.json") as f:
                face = RoboFace.from_json(f.read(), lcd, on_show=face_show)

        Use the same class that saved it (for an add-on face, for example
        CyclopsFace.from_json(...)); a different class raises ValueError.
        """
        import json
        from binascii import a2b_base64

        d = json.loads(text)
        if d.get("roboface_json") != RoboFace._JSON_VERSION:
            raise ValueError("not a RoboFace JSON (version %r)"
                             % (d.get("roboface_json"),))
        if d.get("class") != cls.__name__:
            raise ValueError("JSON was saved from %s; load it with %s.from_json"
                             % (d.get("class"), d.get("class")))
        e = d["eye"]
        right = a2b_base64(e["right"]) if e["right"] is not None else None
        face = cls(fb, d["width"], d["height"], a2b_base64(e["left"]),
                   e["w"], e["h"], name=d.get("name"), right_eye=right, mirror=e["mirror"],
                   frame_rate=d["frame_rate"], on_show=on_show,
                   bgcolor=d["bgcolor"], fgcolor=d["fgcolor"])
        s = d["settings"]
        face._w = list(s["eyes_width"])
        face._h = list(s["eyes_height"])
        face._space = s["eyes_spacing"]
        face._mouth = s["mouth"]
        face._mouth_w = s["mouth_width"]
        face._mouth_off = s["mouth_offset"]
        face._curious = s["curious"]
        face._hflick = list(s["horiz_flicker"])
        face._vflick = list(s["vert_flicker"])
        face._talk = s["talk"]
        st = d["state"]
        face.mood = st["mood"]                 # also prepares LOVE hearts
        if st["mood"] == LOVE:                 # loaded as settled hearts
            face._love_idx = 3
            face._love_dir = 0
        face._position = st["position"]
        face._ox, face._oy = st["offset"]
        face._tox, face._toy = st["target"]
        face._sleeping = st["sleeping"]
        face._op = list(st["eyes_open"])
        face._op_target = list(st["eyes_open"])
        face._reopen = [False, False]
        face._lid["top"], face._lid["angry"], face._lid["sad"], \
            face._lid["happy"] = st["lids"]
        face._ang_z = st["angry_zoom"]
        now = _ticks()
        face._sleep_t = now
        face._ang_t = now
        a = s["auto_blinker"]
        face.set_auto_blinker(a[0], a[1], a[2])
        a = s["idle_mode"]
        face.set_idle_mode(a[0], a[1], a[2])
        return face

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _close_step(op):
        return op // 2 if op > 6 else 0

    @staticmethod
    def _open_step(op):
        op = op + (101 - op) // 2
        return 100 if 100 - op < 8 else op

    def _get(self, kind, side, w, h):
        src = self._src[kind][side]
        if w == src.w and h == src.h:
            return src
        key = (kind, id(src), w, h)
        bmp = self._cache.get(key)
        if bmp is None:
            bmp = _scaled(src, w, h)
            self._cache[key] = bmp
            self._order.append(key)
            if len(self._order) > _CACHE_MAX:
                del self._cache[self._order.pop(0)]
        else:
            self._order.remove(key)
            self._order.append(key)
        return bmp

    def _sides(self, left, right):
        if left is None and right is None:
            return (0, 1)
        out = []
        if left:
            out.append(0)
        if right:
            out.append(1)
        return out

    def _due(self, until):
        return until is not None and _time.ticks_diff(until, _ticks()) > 0

    # --------------------------------------------------------------- main loop
    def update(self):
        self.sequences.update()
        now = _ticks()
        if _time.ticks_diff(now, self._last) < self._period:
            return
        self._last = now
        self._tick(now)
        self._draw(now)
        if self.on_show is not None:
            self.on_show(self)

    # ------------------------------------------------------- automatic modes
    def set_auto_blinker(self, active, interval=None, variation=None):
        self._ab[0] = bool(active)
        if interval is not None:
            self._ab[1] = interval
        if variation is not None:
            self._ab[2] = variation
        self._ab[3] = _ticks()

    def set_idle_mode(self, active, interval=None, variation=None):
        self._idle[0] = bool(active)
        if interval is not None:
            self._idle[1] = interval
        if variation is not None:
            self._idle[2] = variation
        self._idle[3] = _ticks()

    def _schedule(self, cfg, now):
        ms = int(cfg[1] * 1000) + _rand(int(cfg[2] * 1000))
        cfg[3] = _time.ticks_add(now, ms)

    # ---------------------------------------------------------------- geometry
    def eyes_width(self, leftEye, rightEye):
        self._w = [max(1, int(leftEye)), max(1, int(rightEye))]

    def eyes_height(self, leftEye, rightEye):
        self._h = [max(1, int(leftEye)), max(1, int(rightEye))]

    def eyes_spacing(self, space):
        self._space = int(space)

    @property
    def mouth(self):
        return self._mouth

    @mouth.setter
    def mouth(self, enabled):
        self._mouth = bool(enabled)

    def set_mouth(self, value):
        self.mouth = value

    def mouth_width(self, px):
        self._mouth_w = max(2, int(px))

    def mouth_offset(self, px):
        self._mouth_off = int(px)

    # -------------------------------------------------------------------- mood
    @property
    def mood(self):
        return self._mood

    @mood.setter
    def mood(self, mood):
        if mood not in (DEFAULT, TIRED, ANGRY, HAPPY, FROZEN, SCARY, CURIOUS,
                        LOVE, SAD, SURPRISED):
            raise ValueError("unknown mood %r" % (mood,))
        now = _ticks()
        self._mood = mood
        if mood == LOVE:
            if self._shown != LOVE:
                if "heart" not in self._src:
                    hrt = _heart(self._src["eye"][0].w, self._src["eye"][0].h)
                    self._src["heart"] = (hrt, hrt)
                self._shown = LOVE
                self._love_idx = 0
            self._love_dir = 1
            self._love_t = now
        elif self._shown == LOVE:
            self._love_dir = -1          # play transition in reverse first
            self._love_t = now
        else:
            self._show(mood, now)

    def set_mood(self, mood):
        self.mood = mood

    def _show(self, mood, now):
        if mood == SURPRISED and self._shown != SURPRISED:
            self._surp_t = now
        self._shown = mood

    # ---------------------------------------------------------------- position
    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, direction):
        if direction not in _POS:
            raise ValueError("unknown position %r" % (direction,))
        self._position = direction
        dx, dy = _POS[direction]
        self._layout()
        self._tox = dx * self._maxx
        self._toy = dy * self._maxy
        if self._idle[0]:
            self._schedule(self._idle, _ticks())

    def set_position(self, direction):
        self.position = direction

    @property
    def curious(self):
        return self._curious

    @curious.setter
    def curious(self, enable):
        self._curious = bool(enable)

    def set_curious(self, enable):
        self.curious = enable

    # -------------------------------------------------------------- animations
    def blink(self, left=None, right=None):
        if self._sleeping:
            return
        for s in self._sides(left, right):
            self._op_target[s] = 0
            self._reopen[s] = True

    def wink(self, left=None, right=None):
        self._ab[0] = False
        self._idle[0] = False
        if left is None and right is None:
            left = True
        self.blink(left, right)

    def close(self, left=None, right=None):
        for s in self._sides(left, right):
            self._op_target[s] = 0
            self._reopen[s] = False

    def open(self, left=None, right=None):
        for s in self._sides(left, right):
            self._op_target[s] = 100
            self._reopen[s] = False

    def confuse(self):
        self._confuse_until = _time.ticks_add(_ticks(), 500)

    def laugh(self):
        self._laugh_until = _time.ticks_add(_ticks(), 500)

    def horiz_flicker(self, enable, amplitude=None):
        self._hflick[0] = bool(enable)
        if amplitude is not None:
            self._hflick[1] = int(amplitude)

    def vert_flicker(self, enable, amplitude=None):
        self._vflick[0] = bool(enable)
        if amplitude is not None:
            self._vflick[1] = int(amplitude)

    def sleep(self):
        self._sleeping = True
        self._sleep_t = _ticks()
        self.close()

    def wake(self):
        self._sleeping = False
        self.open()

    def cry(self):
        self._cry_t = _ticks()

    def talk(self, enable):
        self._talk = bool(enable)

    def feed(self, side="right"):
        """Feed the face. side is "left" or "right": the face shrinks,
        moves to that side and opens its mouth (EAT), stars pour straight
        down into it, then the mouth closes, HAPPY appears and the face
        goes back to the centre. Calling feed() again while it is eating
        pours more stars."""
        if side not in ("left", "right"):
            raise ValueError("side must be 'left' or 'right'")
        now = _ticks()
        f = self._feed
        if f is None or f["phase"] in ("close", "happy"):
            if f is None:                 # pause automatic blink / idle
                ab, idle = self._ab[0], self._idle[0]
            else:
                ab, idle = f["ab"], f["idle"]
            self._feed = f = {"dir": 1 if side == "right" else -1,
                              "phase": "open", "t": now, "queue": 0,
                              "stars": [], "next": now + 250, "gulp": 0,
                              "ab": ab, "idle": idle, "mouth": None}
            self._ab[0] = False
            self._idle[0] = False
            # eat from a neutral face: wake up, drop the current mood
            if self._sleeping:
                self.wake()
            if self._mood != DEFAULT:
                self.set_mood(DEFAULT)
        f["queue"] += _FEED_STARS

    # --------------------------------------------------------- per-frame logic
    def _moff(self):
        # mouth offset actually used: a raised mouth (negative offset) sits
        # in the space between the eyes, so it falls back to 1 px under the
        # eyes when there is no such space (eyes too close together)
        o = self._mouth_off
        if o < 1:
            l, r = self._src["eye"]
            gap = (self._space + l.rpad * self._w[0] // l.w +
                   r.lpad * self._w[1] // r.w)    # visible gap between eyes
            if self._mouth_w + 2 > gap:
                o = 1
        return o

    def _layout(self):
        w0, w1 = self._w
        tw = w0 + self._space + w1
        eh = max(self._h)
        fh = eh + (self._moff() + _MOUTH_H if self._mouth else 0)
        self._maxx = max(0, (self.width - tw) // 2)
        self._maxy = max(0, (self.height - fh) // 2)
        return tw, eh, fh

    def _tick(self, now):
        awake = not self._sleeping
        if awake and self._ab[0] and _time.ticks_diff(now, self._ab[3]) >= 0:
            self.blink()
            self._schedule(self._ab, now)
        if awake and self._idle[0] and \
                _time.ticks_diff(now, self._idle[3]) >= 0:
            self._layout()
            self._tox = _rand(2 * self._maxx) - self._maxx
            self._toy = _rand(2 * self._maxy) - self._maxy
            self._schedule(self._idle, now)

        for s in (0, 1):
            if self._op_target[s] == 0:
                self._op[s] = self._close_step(self._op[s])
                if self._op[s] == 0 and self._reopen[s]:
                    self._op_target[s] = 100
                    self._reopen[s] = False
            elif self._op[s] < 100:
                self._op[s] = self._open_step(self._op[s])

        self._ox = _ease(self._ox, self._tox)
        self._oy = _ease(self._oy, self._toy)

        m = self._shown
        targets = {"top": 45 if m == TIRED else (30 if m == SCARY else 0),
                   "sad": 45 if m == SAD else 0,
                   "happy": 100 if m == HAPPY else 0}
        for k in targets:
            self._lid[k] = _ease(self._lid[k], targets[k])

        # ANGRY zoom: one stage every _ANG_STAGE_MS toward fully zoomed (in
        # ANGRY) or not zoomed (any other mood); the angry lids follow it
        zt = 4 if m == ANGRY else 0
        if self._ang_z != zt and \
                _time.ticks_diff(now, self._ang_t) >= _ANG_STAGE_MS:
            self._ang_z += 1 if zt > self._ang_z else -1
            self._ang_t = now
        self._lid["angry"] = _ANG_LID * _ANG_STEPS[self._ang_z] // 100
        if m == ANGRY and self._ang_z == 4:
            if self._vein_t is None:
                self._vein_t = now
        else:
            self._vein_t = None

        if self._love_dir and _time.ticks_diff(now, self._love_t) >= \
                _LOVE_FRAME_MS:
            self._love_t = now
            self._love_idx += self._love_dir
            if self._love_idx >= 3:
                self._love_idx = 3
                self._love_dir = 0
            elif self._love_idx < 0:
                self._love_idx = 0
                self._love_dir = 0
                self._show(self._mood, now)
        self._flip = not self._flip
        if self._feed is not None:
            self._feed_tick(now)

    def _feed_tick(self, now):
        f = self._feed
        el = _time.ticks_diff(now, f["t"])
        ph = f["phase"]
        if ph == "open" and el >= 120:
            f["phase"] = ph = "eat"
        if ph in ("open", "eat"):
            if f["queue"] and _time.ticks_diff(now, f["next"]) >= 0:
                f["stars"].append(now)
                f["queue"] -= 1
                f["next"] = _time.ticks_add(now, _FEED_SPAWN_MS)
            keep = []
            for t0 in f["stars"]:
                if _time.ticks_diff(now, t0) >= _FEED_FALL_MS:
                    f["gulp"] = now               # swallowed
                else:
                    keep.append(t0)
            f["stars"] = keep
            if ph == "eat" and not f["queue"] and not keep:
                f["phase"] = "close"
                f["t"] = now
        elif ph == "close" and el >= 240:
            f["phase"] = "happy"
            f["t"] = now
            self.set_mood(HAPPY)
        elif ph == "happy" and el >= 700:
            self._feed = None
            self.set_position(DEFAULT)
            self._ab[0] = f["ab"]
            self._idle[0] = f["idle"]
            if f["ab"]:
                self._schedule(self._ab, now)
            if f["idle"]:
                self._schedule(self._idle, now)

    def _eat_e(self):
        # 0..100: how far into the shrunk EAT look the face is
        f = self._feed
        if f is None:
            return 0
        el = _time.ticks_diff(_ticks(), f["t"])
        ph = f["phase"]
        if ph == "open":
            return (35, 70, 100)[min(2, el // 40)]
        if ph == "eat":
            return 100
        if ph == "close":
            return (100, 66, 33, 0)[min(3, el // 60)]
        return 0

    def _draw(self, now):
        f = self._feed
        if f is None:
            self._draw_face(now)
            return
        e = self._eat_e()
        if e:
            # shrink EVERYTHING by the same factor (eyes, gap between them,
            # mouth width and position), so the face keeps its shape, and
            # move it to the fed side
            sc = 100 - (100 - _EAT_SCALE) * e // 100
            w, h, sp = self._w, self._h, self._space
            mw, mo = self._mouth_w, self._mouth_off
            self._w = [max(2, v * sc // 100) for v in w]
            self._h = [max(2, v * sc // 100) for v in h]
            self._space = sp * sc // 100
            self._mouth_w = max(4, mw * sc // 100)
            self._mouth_off = mo * sc // 100
            try:
                self._feed_place()
                self._draw_face(now)
            finally:
                self._w, self._h, self._space = w, h, sp
                self._mouth_w, self._mouth_off = mw, mo
        else:
            self._feed_place()                   # full size, at the side
            self._draw_face(now)
        if f["phase"] in ("open", "eat"):
            self._draw_feed_stars(now)

    def _feed_place(self):
        # keep the face at the fed side and never off the screen while it
        # changes size
        self._layout()
        if self._feed["phase"] != "happy" or \
                _time.ticks_diff(_ticks(), self._feed["t"]) < 700:
            # stop 1 px short of the screen edge
            self._tox = self._feed["dir"] * max(0, self._maxx - 1)
            self._toy = 0
        lim = max(0, self._maxx - 1)
        self._ox = max(-lim, min(lim, self._ox))
        self._oy = max(-self._maxy, min(self._maxy, self._oy))

    def _draw_feed_stars(self, now):
        # straight down from the top of the screen into the mouth, speeding
        # up like falling and getting smaller; stars pass behind anything
        # already drawn (they only light dark pixels)
        f = self._feed
        m = f["mouth"]
        if m is None:
            return
        mx, my = m
        fb = self.fb
        fg = self.fgcolor
        bg = self.bgcolor
        sy = 3
        for t0 in f["stars"]:
            p = _time.ticks_diff(now, t0) * 256 // _FEED_FALL_MS
            y = sy + (my - sy) * p * p // 65536
            star = _FEED_STAR[min(2, (y - sy) * 3 // max(1, my - sy))]
            n = len(star)
            x0 = mx - n // 2
            y0 = y - n // 2
            for j, row in enumerate(star):
                for i, c in enumerate(row):
                    if c == "#" and fb.pixel(x0 + i, y0 + j) == bg:
                        fb.pixel(x0 + i, y0 + j, fg)

    def _draw_face(self, now):
        fb = self.fb
        fg = self.fgcolor
        bg = self.bgcolor
        fb.fill(bg)
        tw, eh, fh = self._layout()
        m = self._shown

        # offsets: position + flickers
        x = self._ox
        y = self._oy
        hamp = 0
        vamp = 0
        if self._hflick[0]:
            hamp = self._hflick[1]
        if m == FROZEN:
            hamp = max(hamp, 2)
        if self._due(self._confuse_until):
            hamp = 20
        if self._vflick[0]:
            vamp = self._vflick[1]
        if m == SCARY:
            vamp = max(vamp, 2)
        laughing = self._due(self._laugh_until)
        if laughing:
            vamp = 5
        if hamp:
            x += hamp // 2 if self._flip else -(hamp // 2)
        if vamp:
            y += vamp // 2 if self._flip else -(vamp // 2)

        bx = (self.width - tw) // 2 + x
        by = (self.height - fh) // 2 + y
        cy = by + eh // 2
        centers = ((bx + self._w[0] // 2, cy),
                   (bx + self._w[0] + self._space + self._w[1] // 2, cy))

        # how each eye is drawn (source kind + size multipliers in %)
        kind = "eye"
        mw = mh = 100
        if m == LOVE:
            i = self._love_idx
            if i == 1:
                mh = 50
            elif i == 2:
                kind = "heart"
                mw = mh = 60
            elif i == 3:
                kind = "heart"
        boost = 100
        if m == SURPRISED:
            boost = 128 if _since(self._surp_t) < 150 else 120
        look = 0
        if self._maxx:
            if self._ox <= -self._maxx // 3:
                look = -1
            elif self._ox >= self._maxx // 3:
                look = 1
        curious = self._curious or m == CURIOUS

        # ANGRY lean-in zoom around the face centre (FX, FY): a row y pixels
        # from the centre is scaled by za + zb * y and moved to zf(y)
        zoom = self._ang_z and kind == "eye"
        if zoom:
            FX = bx + tw // 2
            FY = by + fh // 2
            ztop = by - FY
            zbot = by + fh - FY
            k = _ANG_STEPS[self._ang_z] / 100
            zst = 1 + (_ANG_TOP - 1) * k
            zsb = 1 + (_ANG_BOT - 1) * k
            zb = (zsb - zst) / max(1, zbot - ztop)
            za = zst - zb * ztop
            # (PX, PY): where the zoomed face is drawn, nudged so it stays on
            # screen when idle mode / position has moved it near an edge
            hw = tw * zst / 2
            up = -(za * ztop + zb * ztop * ztop / 2)
            dn = za * zbot + zb * zbot * zbot / 2
            PX = FX
            PY = FY
            if self.width >= 2 * hw:
                PX = int(min(max(FX, hw), self.width - hw))
            if self.height >= up + dn:
                PY = int(min(max(FY, up), self.height - dn))

        boxes = []
        eye_bottom = cy
        for s in (0, 1):
            cx = centers[s][0]
            sc = boost
            if curious and look and ((look < 0) == (s == 0)):
                sc = sc * 120 // 100
            dw = max(1, self._w[s] * mw * sc // 10000)
            full_h = max(1, self._h[s] * mh * sc // 10000)
            dh = full_h * self._op[s] // 100
            # where the bottom of this (fully open, un-morphed) eye is drawn;
            # the mouth sits under it and does not jump while blinking
            ow = max(1, self._w[s] * sc // 100)
            oh = max(1, self._h[s] * sc // 100)
            if m == LOVE and self._love_idx >= 2:
                b = self._visible_bottom("heart", s, ow, oh, False)
            else:
                b = self._visible_bottom("eye", s, ow, oh, False)
                hp = self._lid["happy"]
                if hp:
                    hb = self._visible_bottom("eye", s, ow, oh, True)
                    b -= (b - hb) * hp // 100
            eye_bottom = max(eye_bottom, cy - oh // 2 + b)
            if dh <= 2:
                src = self._src[kind][s]
                span = max(2, dw * src.span // src.w)
                lx = cx
                ly = cy
                if zoom:
                    zs = za + zb * (cy - FY)
                    lx = PX + int((cx - FX) * zs + 0.5)
                    ly = PY + int(za * (cy - FY) + zb * (cy - FY) ** 2 / 2
                                  + 0.5)
                    span = int(span * zs + 0.5)
                fb.fill_rect(lx - span // 2, ly - 1, span, 2, fg)
                boxes.append((lx, ly, dw, 0))
                continue
            bmp = self._get(kind, s, dw, dh)
            x0 = cx - dw // 2
            y0 = cy - dh // 2
            if zoom:
                bmp, wx, wy = self._zoomed(s, bmp, x0 - FX, y0 - FY,
                                           ztop, zbot, zst, zsb)
                if self._pal is None:
                    fb.blit(bmp.fb, PX + wx, PY + wy, bg)
                else:
                    fb.blit(bmp.fb, PX + wx, PY + wy, bg, self._pal)
                boxes.append((PX + wx + bmp.w // 2, PY + wy + bmp.h // 2,
                              bmp.w, bmp.h))
                continue
            if self._pal is None:
                fb.blit(bmp.fb, x0, y0, bg)
            else:
                fb.blit(bmp.fb, x0, y0, bg, self._pal)
            self._lids(s, x0, y0, dw, dh)
            boxes.append((cx, cy, dw, dh))

        if self._mouth:
            mx = bx + tw // 2
            top = eye_bottom + 1 + self._moff()
            mw_ = None
            if zoom:
                t = top - FY
                mw_ = int(self._mouth_w * (za + zb * t) + 0.5)
                top = PY + int(za * t + zb * t * t / 2 + 0.5)
                mx = PX
            self._draw_mouth(mx, top, m, look, laughing, now, mw_)
            if laughing and not self._sleeping:
                cb = eye_bottom
                if zoom:                  # follow the ANGRY zoom
                    t = cb - FY
                    cb = PY + int(za * t + zb * t * t / 2 + 0.5)
                self._draw_cheeks(boxes, cb)

        if self._vein_t is not None:
            self._draw_vein(self.width - 9, 8, now)

        if self._sleeping:
            self._draw_zzz(bx + tw + 2, by, now)

        if self._cry_t is not None:
            e = _since(self._cry_t)
            if e >= 600:
                self._cry_t = None
            else:
                cx, ecy, dw, dh = boxes[-1]
                tx = cx + dw // 4
                ty = ecy + dh // 2 + 3 + (e * 16) // 600
                fb.ellipse(tx, ty, 2, 2, fg, True)
                fb.poly(0, 0, _array("h", [tx - 2, ty, tx + 2, ty,
                                           tx, ty - 5]), fg, True)

    def _zoomed(self, s, bmp, ex, ey, ztop, zbot, zst, zsb):
        # eye bitmap with the current eyelids applied, then lean-in warped;
        # cached, so holding ANGRY costs no extra work
        lid = self._lid
        key = (s, id(bmp), ex, ey, ztop, zbot, self._ang_z,
               lid["top"], lid["angry"], lid["sad"], lid["happy"])
        hit = self._angc.get(key)
        if hit is None:
            tmp = _Bitmap(bytearray(bmp.buf), bmp.w, bmp.h)
            self._lids(s, 0, 0, bmp.w, bmp.h, tmp.fb, 0, lid)
            hit = _keystone(tmp, ex, ey, ztop, zbot, zst, zsb)
            if len(self._angc) >= 24:
                self._angc = {}
            self._angc[key] = hit
        return hit

    def _draw_vein(self, cx, cy, now):
        # anger vein: pops in (small, big, normal), then throbs
        e = _time.ticks_diff(now, self._vein_t)
        i = e // 40
        if i < len(_VEIN_POP):
            sc = _VEIN_POP[i]
        else:
            sc = 125 if ((e - 40 * len(_VEIN_POP)) // _VEIN_THROB_MS) % 2 \
                else 100
        def r(v):                            # round half away from zero,
            m = (abs(v) * sc + 50) // 100    # so all 4 pieces stay mirrored
            return m if v >= 0 else -m
        segs = []
        for qx, qy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
            pts = [(cx + r(qx * x), cy + r(qy * y)) for x, y in _VEIN]
            for i in range(len(pts) - 1):
                segs.append((pts[i], pts[i + 1]))
        fb = self.fb
        bg = self.bgcolor
        fg = self.fgcolor
        for (x0, y0), (x1, y1) in segs:      # 1 px halo keeps it readable
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx or dy:
                        fb.line(x0 + dx, y0 + dy, x1 + dx, y1 + dy, bg)
        for (x0, y0), (x1, y1) in segs:
            fb.line(x0, y0, x1, y1, fg)

    def _visible_bottom(self, kind, s, w, h, happy):
        # last drawn row (0..h-1) of a fully open eye of size w x h, either
        # as drawn or with the HAPPY mask applied; cached per size
        key = (kind, s, w, h, happy)
        b = self._vb.get(key)
        if b is not None:
            return b
        bmp = self._get(kind, s, w, h)
        if not happy:
            b = h - 1 - bmp.bottom
        else:
            tmp = _Bitmap(bytearray(bmp.buf), w, h)
            self._lids(s, 0, 0, w, h, tmp.fb, 0,
                       {"top": 0, "angry": 0, "sad": 0, "happy": 100})
            b = 0
            for y in range(h - 1, -1, -1):
                for x in range(w):
                    if tmp.fb.pixel(x, y):
                        b = y
                        break
                if b:
                    break
        if len(self._vb) > 32:
            self._vb = {}
        self._vb[key] = b
        return b

    def _lids(self, s, x0, y0, w, h, fb=None, bg=None, lid=None):
        if fb is None:
            fb = self.fb
            bg = self.bgcolor
            lid = self._lid
        x1 = x0 + w
        top = y0 - 1
        if lid["top"]:
            fb.fill_rect(x0 - 1, top, w + 2, h * lid["top"] // 100 + 1, bg)
        if lid["angry"]:
            a = top + h * lid["angry"] // 100
            if s == 0:
                pts = [x0 - 1, top, x1, top, x1, a]
            else:
                pts = [x0 - 1, top, x1, top, x0 - 1, a]
            fb.poly(0, 0, _array("h", pts), bg, True)
        if lid["sad"]:
            a = top + h * lid["sad"] // 100
            if s == 0:
                fb.poly(0, 0, _array("h", [x0 - 1, top, x1, top,
                                           x0 - 1, a]), bg, True)
            else:
                fb.poly(0, 0, _array("h", [x0 - 1, top, x1, top,
                                           x1, a]), bg, True)
        if lid["happy"]:
            ry = (h * 8 // 10) * lid["happy"] // 100
            if ry > 0:
                fb.ellipse(x0 + w // 2, y0 + h + h // 5,
                           (w * 6) // 10 + 1, ry, bg, True)
                if lid["happy"] >= 50:
                    fb.fill_rect(x0 - 1, y0 + h + h // 5, w + 2, h, bg)

    # ------------------------------------------------------------------- mouth
    def _curve(self, cx, cy, w, depth, thick=_MOUTH_T):
        # parabola: depth > 0 smiles, depth < 0 frowns
        fb = self.fb
        fg = self.fgcolor
        n = max(4, w // 3)
        half = w // 2
        prev = None
        for i in range(n + 1):
            t = 2 * i - n                      # -n .. n
            px = cx - half + (w * i) // n
            py = cy - depth // 2 + (depth * (n * n - t * t)) // (n * n)
            if prev:
                for k in range(thick):
                    fb.line(prev[0], prev[1] + k, px, py + k, fg)
            prev = (px, py)

    def _ring(self, x, y, rx, ry, t):
        # ellipse outline t px thick
        fb = self.fb
        fb.ellipse(x, y, rx, ry, self.fgcolor, True)
        ix = rx - t
        iy = ry - t
        if ix >= 2 and iy >= 2:
            fb.ellipse(x, y, ix, iy, self.bgcolor, True)
        elif ix >= 0 and iy >= 0:
            # a filled ellipse this small is a '+' shape and would leave
            # stray pixels in the hole, so clear a small rectangle instead
            fb.fill_rect(x - ix, y - iy, 2 * ix + 1, 2 * iy + 1, self.bgcolor)

    def _draw_eat_mouth(self, mx, top, now):
        # EAT: an oval a little bigger than the (shrunk) normal mouth; it
        # opens, gulps as each star goes in, then narrows and closes
        f = self._feed
        rx_full = self._mouth_w // 2 + 2
        ry_full = max(3, rx_full * 7 // 10)
        el = _time.ticks_diff(now, f["t"])
        ph = f["phase"]
        if ph == "open":
            k = kx = (35, 70, 100)[min(2, el // 40)]
        elif ph == "eat":
            gulping = f["gulp"] and _time.ticks_diff(now, f["gulp"]) < 70
            k = kx = 90 if gulping else 100
        else:
            k = (60, 30, 10, 0)[min(3, el // 60)]
            kx = (90, 70, 50, 30)[min(3, el // 60)]
        rx = max(3, rx_full * kx // 100)
        ry = ry_full * k // 100
        cy = top + max(ry, 1) + 1
        f["mouth"] = (mx, cy)
        fb = self.fb
        if ry <= 0:
            fb.fill_rect(mx - rx, cy - 1, 2 * rx + 1, 2, self.fgcolor)
            return
        fb.ellipse(mx, cy, rx, ry, self.fgcolor, True)
        if rx > 2 and ry > 2:
            fb.ellipse(mx, cy, rx - 2, ry - 2, self.bgcolor, True)

    def _draw_mouth(self, mx, top, m, look, laughing, now, w=None):
        # 'top' is the row where the top of the mouth goes; each shape is
        # shifted down by its own height above its centre so every mouth
        # starts at the same row. 'w' overrides the width (ANGRY zoom).
        if self._feed is not None and self._feed["phase"] != "happy":
            self._draw_eat_mouth(mx, top, now)
            return
        fb = self.fb
        fg = self.fgcolor
        T = _MOUTH_T
        if w is None:
            w = self._mouth_w
        if self._sleeping:
            r = 4 if (_since(self._sleep_t) // 700) % 2 else 3
            self._ring(mx, top + r, r, r, 2)
            return
        if laughing:
            fb.ellipse(mx, top, w // 2, 7, fg, True, 0b1100)
            return
        if self._talk and (now // 150) % 2:
            fb.ellipse(mx, top + 4, max(3, w // 4), 4, fg, True)
            return
        if m == LOVE or m == HAPPY:
            self._curve(mx, top + 3, w, 6)
        elif m == TIRED:
            fb.fill_rect(mx - w // 4, top, w // 2, T, fg)
        elif m == ANGRY:
            self._curve(mx, top + 2, (w * 6) // 10, -4)
        elif m == SAD:
            self._curve(mx, top + 2, (w * 6) // 10, -5)
        elif m == FROZEN:
            my = top + 1
            n = max(2, (w * 8 // 10) // 3)
            x0 = mx - (n * 3) // 2
            pts = []
            for i in range(n + 1):
                pts.append((x0 + i * 3, my + (2 if i % 2 else -1)))
            for i in range(n):
                for k in range(T - 1):
                    fb.line(pts[i][0], pts[i][1] + k, pts[i + 1][0],
                            pts[i + 1][1] + k, fg)
        elif m == SCARY:
            my = top + 1
            ww = (w * 8) // 10
            x0 = mx - ww // 2
            for i in range(ww):
                for k in range(T):
                    fb.pixel(x0 + i, my + k + _WAVE[i % 8] // 2, fg)
        elif m == CURIOUS:
            self._ring(mx + 4 * look, top + 3, 4, 3, 2)
        elif m == SURPRISED:
            r = 5 if (_since(self._surp_t) // 300) % 2 else 4
            self._ring(mx, top + r, r, r, 2)
        else:
            self._curve(mx, top + 2, (w * 6) // 10, 4)

    def _draw_cheeks(self, boxes, eye_bottom):
        # little round cheeks just under the outer edge of each eye
        # (laughing), placed from the eyes so they don't move with the mouth
        r = _CHEEK_R
        y = eye_bottom + 2 + r
        xs = (boxes[0][0] - boxes[0][2] // 4,
              boxes[1][0] + boxes[1][2] // 4)
        for x in xs:
            self.fb.ellipse(x, y, r, r, self.fgcolor, True)

    def _draw_zzz(self, x, y, now):
        x = min(x, self.width - 14)
        y = max(y, 1)
        k = (_since(self._sleep_t) // 400) % 4
        fg = self.fgcolor
        for i, (dx, dy, s) in enumerate(((0, 12, 3), (4, 6, 4), (9, 0, 5))):
            if i < k:
                zx = x + dx
                zy = y + dy
                self.fb.hline(zx, zy, s + 1, fg)
                self.fb.line(zx + s, zy, zx, zy + s, fg)
                self.fb.hline(zx, zy + s, s + 1, fg)



class HalfMoonFace(RoboFace):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        left, right = self._src["eye"]
        self._eyes_normal = (left, right)
        self._eyes_sad = (right, left)      # each eye uses its mirror image

    def _show(self, mood, now):
        # called by RoboFace whenever the displayed mood changes
        super()._show(mood, now)
        self._src["eye"] = self._eyes_sad if mood == SAD else self._eyes_normal

    def _tick(self, now):
        # called by RoboFace once per frame; keep the SAD eyelid closed off
        super()._tick(now)
        



_CYC_MOUTH_W = 24           # default mouth width for a cyclops face (px)

class CyclopsFace(RoboFace):
    def __init__(self, fb, width, height, eye, eye_w, eye_h, **kw):
        kw.pop("right_eye", None)
        kw["mirror"] = False                 # both copies identical
        super().__init__(fb, width, height, eye, eye_w, eye_h, **kw)
        self.mouth_width(min(_CYC_MOUTH_W, eye_w))
        # LOVE: the built-in heart fills the eye's full width, which is
        # squashed on a wide eye. Use well-proportioned hearts instead: one
        # centred heart, or on a long thin eye (a visor) a row of hearts
        hw = min(eye_w, eye_h * 10 // 9)
        n = max(1, (eye_w + 3) // (hw + 3)) if eye_w >= 3 * eye_h else 1
        small = _heart(hw, eye_h)
        buf = bytearray(((eye_w + 7) // 8) * eye_h)
        canvas = _framebuf.FrameBuffer(buf, eye_w, eye_h, _framebuf.MONO_HLSB)
        gap = (eye_w - n * hw) // (n + 1) if n > 1 else 0
        x = (eye_w - n * hw - (n - 1) * gap) // 2
        for i in range(n):
            canvas.blit(small.fb, x + i * (hw + gap), 0)
        hrt = _Bitmap(buf, eye_w, eye_h)
        self._src["heart"] = (hrt, hrt)

    # both "eyes" always the same size, drawn in the same place
    def eyes_width(self, leftEye, rightEye=None):
        super().eyes_width(leftEye, leftEye)

    def eyes_height(self, leftEye, rightEye=None):
        super().eyes_height(leftEye, leftEye)

    def eyes_spacing(self, space):
        pass                                 # no spacing with one eye

    def _layout(self):
        self._space = -self._w[0]            # second eye on top of the first
        out = super()._layout()
        # one big eye: leave room for CURIOUS / SURPRISED to enlarge it by
        # 20% when the face has moved toward an edge
        tw, eh, fh = out
        self._maxx = max(0, (self.width - tw * 6 // 5) // 2)
        return out

    def _sides(self, left, right):
        return (0, 1)                        # blink/wink/open/close: the eye

    def _lids(self, s, x0, y0, w, h, fb=None, bg=None, lid=None):
        # same eyelids as RoboFace, but cut symmetrically on one eye, and
        # identical for both copies (so drawing them on top of each other
        # gives the same result)
        if fb is None:
            fb = self.fb
            bg = self.bgcolor
            lid = self._lid
        x1 = x0 + w
        mid = x0 + w // 2
        top = y0 - 1
        if lid["top"]:
            fb.fill_rect(x0 - 1, top, w + 2, h * lid["top"] // 100 + 1, bg)
        if lid["angry"]:                     # V-notch: brows meet mid-eye
            a = top + h * lid["angry"] // 100
            fb.poly(0, 0, _array("h", [x0 - 1, top, x1, top, mid, a]), bg,
                    True)
        if lid["sad"]:                       # both outer corners droop
            a = top + h * lid["sad"] // 100
            fb.poly(0, 0, _array("h", [x0 - 1, top, mid, top, x0 - 1, a]),
                    bg, True)
            fb.poly(0, 0, _array("h", [mid, top, x1, top, x1, a]), bg, True)
        if lid["happy"]:
            ry = (h * 8 // 10) * lid["happy"] // 100
            if ry > 0:
                fb.ellipse(mid, y0 + h + h // 5, (w * 6) // 10 + 1, ry, bg,
                           True)
                if lid["happy"] >= 50:
                    fb.fill_rect(x0 - 1, y0 + h + h // 5, w + 2, h, bg)

    def _draw_cheeks(self, boxes, eye_bottom):
        # either side of the mouth, under the eye's outer edges
        r = _CHEEK_R
        cx, cy, dw, dh = boxes[0]
        o = max(dw // 2, self._mouth_w // 2 + r + 2)
        y = eye_bottom + 2 + r
        for x in (cx - o, cx + o):
            self.fb.ellipse(x, y, r, r, self.fgcolor, True)

        self._lid["sad"] = 0