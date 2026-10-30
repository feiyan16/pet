# popup.py - pre-rendered popup window for MicroPython FrameBuffer displays
#
# Same 1 px border style as menu.py (rounded left corners, cut right
# corners). The popup is a window centred on the screen, drawn over whatever
# is already there:
#
#     +----------------------------+
#     |      Quit the game?        |   text, word-wrapped (up to 3 lines)
#     |                            |
#     |   [ Yes ]      No          |   options: the current one highlighted
#     +----------------------------+
#
# If the options don't fit side by side, one option is shown at a time as
# "<  option  >".
#
# The constructor renders one frame per option. display() shows the current
# one, next()/prev() move between options (wrapping around at the ends) and
# select() flashes the highlighted option, like menu.py.
#
# Works with any display that subclasses framebuf.FrameBuffer (e.g. ssd1306).
# Uses the built-in 8x8 FrameBuffer font. No other dependencies.
#
# Licensed under the GNU General Public License v3.0 (same as RoboFace).

import framebuf as _framebuf
import time as _time

_FONT = 8        # built-in FrameBuffer font is 8 x 8 px
_LINE = 10       # text line height (px)
_MAX_LINES = 3   # most text lines the window shows
_BTN_H = 11      # option button height (px)
_BTN_PAD = 3     # space left/right of an option's text inside its button


class Popup:
    def __init__(self, fb, text, options, width=128, height=64,
                 win_w=None, on_select=None, on_show=None,
                 fgcolor=1, bgcolor=0, flash_ms=150):
        """
        fb        : FrameBuffer of the display to draw on
        text      : message shown at the top of the window (word-wrapped)
        options   : list of option labels, e.g. ["Yes", "No"]
        width,
        height    : screen size in pixels (the window is centred in it)
        win_w     : window width in px (default: screen width - 8)
        on_select : called as on_select(popup, index, label) after select()
        on_show   : called as on_show(popup) after drawing; if omitted,
                    fb.show() is called when the display has one
        fgcolor,
        bgcolor   : colours for colour displays (defaults suit mono OLEDs)
        flash_ms  : how long select() shows the inverted option
        """
        if not options:
            raise ValueError("options must contain at least one item")
        self.fb = fb
        self.width = width
        self.height = height
        self.on_select = on_select
        self.on_show = on_show
        self.fgcolor = fgcolor
        self.bgcolor = bgcolor
        self.flash_ms = flash_ms
        self.text = str(text)
        self.options = [str(o) for o in options]
        self._index = 0

        self._pal = None
        if fgcolor != 1 or bgcolor != 0:
            self._pal = _framebuf.FrameBuffer(bytearray(4), 2, 1,
                                              _framebuf.RGB565)
            self._pal.pixel(0, 0, bgcolor)
            self._pal.pixel(1, 0, fgcolor)

        self._layout(win_w)
        # one 1-bit frame per option, window-sized (not full screen)
        self.frames = [self._render(i, False) for i in range(len(self.options))]

    # ------------------------------------------------------------ geometry
    def _layout(self, win_w):
        ww = min(self.width, win_w or self.width - 8)
        cols = max(1, (ww - 8) // _FONT)                 # text chars per line
        self._lines = self._wrap(self.text, cols)
        # options side by side if they fit, otherwise one at a time
        widths = [len(o) * _FONT + 2 * _BTN_PAD for o in self.options]
        gap = 4
        self._row = sum(widths) + gap * (len(widths) - 1) <= ww - 8
        self._btn_w = widths
        self._gap = gap
        wh = 4 + len(self._lines) * _LINE + 3 + _BTN_H + 4
        wh = min(self.height, wh)
        self.win_w = ww
        self.win_h = wh
        self.win_x = (self.width - ww) // 2
        self.win_y = (self.height - wh) // 2
        self._btn_y = wh - 4 - _BTN_H

    @staticmethod
    def _wrap(text, cols):
        lines = []
        for para in text.split("\n"):
            line = ""
            for word in para.split(" "):
                while len(word) > cols:                  # very long word
                    if line:
                        lines.append(line)
                        line = ""
                    lines.append(word[:cols])
                    word = word[cols:]
                if not line:
                    line = word
                elif len(line) + 1 + len(word) <= cols:
                    line += " " + word
                else:
                    lines.append(line)
                    line = word
            lines.append(line)
        if len(lines) > _MAX_LINES:
            lines = lines[:_MAX_LINES]
            last = lines[-1]
            lines[-1] = (last[:cols - 1] if len(last) >= cols else last) + "~"
        return lines

    # ----------------------------------------------------------- rendering
    def _render(self, i, flash):
        w = self.win_w
        h = self.win_h
        buf = bytearray(((w + 7) // 8) * h)
        fb = _framebuf.FrameBuffer(buf, w, h, _framebuf.MONO_HLSB)
        self._border(fb, w, h)
        # text, each line centred
        y = 4
        for line in self._lines:
            fb.text(line, (w - len(line) * _FONT) // 2, y, 1)
            y += _LINE
        by = self._btn_y
        ty = by + (_BTN_H - _FONT) // 2 + 1
        if self._row:
            total = sum(self._btn_w) + self._gap * (len(self._btn_w) - 1)
            x = (w - total) // 2
            for k, label in enumerate(self.options):
                bw = self._btn_w[k]
                self._button(fb, x, by, bw, label, ty, k == i, flash)
                x += bw + self._gap
        else:
            label = self._fit(self.options[i], w - 8 - 2 * 10)
            bw = len(label) * _FONT + 2 * _BTN_PAD
            x = (w - bw) // 2
            self._button(fb, x, by, bw, label, ty, True, flash)
            self._arrow(fb, 5, by + _BTN_H // 2, -1)        # "<"
            self._arrow(fb, w - 6, by + _BTN_H // 2, 1)     # ">"
        return buf

    def _button(self, fb, x, y, bw, label, ty, current, flash):
        if current:
            bar, ink = (0, 1) if flash else (1, 0)
            fb.fill_rect(x, y, bw, _BTN_H, bar)
        else:
            ink = 1
        fb.text(label, x + _BTN_PAD, ty, ink)

    @staticmethod
    def _arrow(fb, x, y, d):
        # small 3 x 5 chevron pointing left (d = -1) or right (d = 1)
        for k in range(3):
            fb.pixel(x + d * k, y - 2 + k, 1)
            fb.pixel(x + d * k, y + 2 - k, 1)

    @staticmethod
    def _fit(label, room_px):
        room = max(1, room_px // _FONT)
        return label if len(label) <= room else label[:room - 1] + "~"

    @staticmethod
    def _border(fb, w, h):
        # same style as menu.py: rounded left corners, cut right corners
        x0, y0, x1, y1 = 0, 0, w - 1, h - 1
        fb.hline(x0 + 2, y0, x1 - x0 - 4, 1)            # top
        fb.hline(x0 + 2, y1, x1 - x0 - 4, 1)            # bottom
        fb.vline(x0, y0 + 2, y1 - y0 - 3, 1)            # left
        fb.vline(x1, y0 + 3, y1 - y0 - 5, 1)            # right
        fb.pixel(x0 + 1, y0 + 1, 1)                     # rounded left corners
        fb.pixel(x0 + 1, y1 - 1, 1)
        fb.line(x1 - 2, y0, x1, y0 + 2, 1)              # cut right corners
        fb.line(x1 - 2, y1, x1, y1 - 2, 1)

    # ------------------------------------------------------------- showing
    def _show(self, buf):
        x = self.win_x
        y = self.win_y
        # clear a 1 px margin around the window so it stands out from
        # whatever is underneath, then draw the window (fully opaque)
        self.fb.fill_rect(x - 1, y - 1, self.win_w + 2, self.win_h + 2,
                          self.bgcolor)
        frame = _framebuf.FrameBuffer(buf, self.win_w, self.win_h,
                                      _framebuf.MONO_HLSB)
        if self._pal is None:
            self.fb.blit(frame, x, y)
        else:
            self.fb.blit(frame, x, y, -1, self._pal)
        if self.on_show is not None:
            self.on_show(self)
        elif hasattr(self.fb, "show"):
            self.fb.show()

    def display(self):
        """Draw the popup over the screen, current option highlighted
        (the first option after construction)."""
        self._show(self.frames[self._index])

    def next(self):
        """Highlight the next option and redraw. After the last option it
        wraps to the first. Returns the new index."""
        self._index += 1
        if self._index >= len(self.options):     # bounds check
            self._index = 0
        self.display()
        return self._index

    def prev(self):
        """Highlight the previous option and redraw. Before the first option
        it wraps to the last. Returns the new index."""
        self._index -= 1
        if self._index < 0:                      # bounds check
            self._index = len(self.options) - 1
        self.display()
        return self._index

    def select(self):
        """Flash the highlighted option inverted for flash_ms, show the
        normal frame again, then call on_select(popup, index, label).
        Returns the label. Blocks for flash_ms."""
        self._show(self._render(self._index, True))
        _time.sleep_ms(self.flash_ms)
        self.display()
        label = self.options[self._index]
        if self.on_select is not None:
            self.on_select(self, self._index, label)
        return label

    # ---------------------------------------------------------- properties
    @property
    def index(self):
        """Position of the current option (0 = first)."""
        return self._index

    @index.setter
    def index(self, i):
        if not 0 <= i < len(self.options):
            raise IndexError("popup index out of range")
        self._index = i

    @property
    def current(self):
        """Label of the current option."""
        return self.options[self._index]

    def __len__(self):
        return len(self.options)
