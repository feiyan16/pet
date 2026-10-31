# menu.py - pre-rendered carousel menu for MicroPython FrameBuffer displays
#
# Layout (one frame per option):
#   +------------------------+   1 px border (rounded left corners,
#   | ^ PREVIOUS OPTION      |   cut right corners)
#   |########################|   middle bar: highlighted current option
#   |#(icon) CURRENT OPTION #|   with the star icon (bitmaps/stars.py),
#   |########################|   or a sprite of your own per option (icons=)
#   |########################|
#   | v NEXT OPTION          |
#   +------------------------+
# The top bar is blank on the first option and the bottom bar is blank on
# the last option.
#
# The constructor renders every frame up front from your list of options.
# display() shows the current frame, next()/prev() move through the list
# (wrapping around at the ends), select() flashes the middle bar inverted.
#
# Works with any display that subclasses framebuf.FrameBuffer (e.g. ssd1306).
# Uses the built-in 8x8 FrameBuffer font and the star from bitmaps/stars.py.
#
# Licensed under the GNU General Public License v3.0 (same as RoboFace).

import framebuf as _framebuf
import time as _time

from bitmaps import stars as _stars

_FONT = 8  # built-in FrameBuffer font is 8 x 8 px

# The star icon is the shared star from bitmaps/stars.py (11 x 10 px)
_ICON = _framebuf.FrameBuffer(_stars.STAR, _stars.STAR_W, _stars.STAR_H, _framebuf.MONO_HLSB)
# palette for drawing the icon dark on the highlighted (lit) bar:
# background (0) -> 1, which the key skips; star (1) -> 0 = dark pixel
_ICON_DARK = _framebuf.FrameBuffer(bytearray(1), 2, 1, _framebuf.MONO_HLSB)
_ICON_DARK.pixel(0, 0, 1)
_ICON_DARK.pixel(1, 0, 0)


class Menu:
    def __init__(
        self, fb, width, height, options, on_select=None, on_show=None, fgcolor=1, bgcolor=0, flash_ms=150, icons=None
    ):
        """
        fb        : FrameBuffer to draw into (your display object)
        width,
        height    : screen size in pixels (designed for 128x64)
        options   : list of option labels; one frame is rendered per option
        on_select : called as on_select(menu, index, label) after select()
        on_show   : called as on_show(menu) to push a frame to the screen.
                    If omitted, fb.show() is called when fb has one.
        fgcolor,
        bgcolor   : colours for colour displays (defaults suit mono OLEDs)
        flash_ms  : how long select() shows the inverted bar
        icons     : optional list, one per option: (bytearray, w, h) MONO_HLSB
                    sprite to show instead of the star, or None for the star
        """
        if not options:
            raise ValueError("options must contain at least one item")
        if height < 48 or width < 64:
            raise ValueError("menu needs a screen of at least 64x48 px " "(designed for 128x64)")
        self.fb = fb
        self.width = width
        self.height = height
        self.on_select = on_select
        self.on_show = on_show
        self.fgcolor = fgcolor
        self.bgcolor = bgcolor
        self.flash_ms = flash_ms
        self.options = [str(o) for o in options]
        self.icons = icons
        self._index = 0

        # palette maps the 1-bit frames to fgcolor/bgcolor on colour screens
        self._pal = None
        if fgcolor != 1 or bgcolor != 0:
            self._pal = _framebuf.FrameBuffer(bytearray(4), 2, 1, _framebuf.RGB565)
            self._pal.pixel(0, 0, bgcolor)
            self._pal.pixel(1, 0, fgcolor)

        self._layout()
        # one 1-bit frame per option (128x64 = 1024 bytes each)
        self.frames = [self._render(i, False) for i in range(len(self.options))]

    # ------------------------------------------------------------ geometry
    def _layout(self):
        w = self.width
        h = self.height
        self._x0 = 1  # border box
        self._y0 = 1
        self._x1 = w - 2
        self._y1 = h - 2
        inner = self._y1 - self._y0 - 1  # rows inside the border
        self._bar = max(_FONT, inner * 27 // 100)  # top/bottom bar height
        self._mid = inner - 2 * self._bar  # highlighted bar height
        self._top_y = self._y0 + 1
        self._mid_y = self._top_y + self._bar
        self._bot_y = self._mid_y + self._mid
        self._icon_x = self._x0 + 4
        self._text_x = self._icon_x + _stars.STAR_W + 4
        self._side_x = self._x0 + 13  # text start in top/bottom bars

    # ----------------------------------------------------------- rendering
    def _render(self, i, flash):
        w = self.width
        h = self.height
        buf = bytearray(((w + 7) // 8) * h)
        fb = _framebuf.FrameBuffer(buf, w, h, _framebuf.MONO_HLSB)
        self._border(fb)
        n = len(self.options)
        # top bar: previous option (blank on the first option)
        if i > 0:
            ty = self._top_y + (self._bar - _FONT) // 2
            self._caret(fb, self._x0 + 4, ty + 2, True, 1)
            fb.text(self._fit(self.options[i - 1], self._side_x), self._side_x, ty, 1)
        # bottom bar: next option (blank on the last option)
        if i < n - 1:
            ty = self._bot_y + (self._bar - _FONT) // 2
            self._caret(fb, self._x0 + 4, ty + 3, False, 1)
            fb.text(self._fit(self.options[i + 1], self._side_x), self._side_x, ty, 1)
        # middle bar: current option, highlighted (inverted when flashing)
        bar, ink = (0, 1) if flash else (1, 0)
        fb.fill_rect(self._x0 + 1, self._mid_y, self._x1 - self._x0 - 1, self._mid, bar)
        icon = self.icons[i] if self.icons else None
        if icon is None:
            icon_fb, icon_w, icon_h = _ICON, _stars.STAR_W, _stars.STAR_H
        else:
            icon_w, icon_h = icon[1], icon[2]
            icon_fb = _framebuf.FrameBuffer(icon[0], icon_w, icon_h, _framebuf.MONO_HLSB)
        iy = self._mid_y + (self._mid - icon_h) // 2
        if ink:
            fb.blit(icon_fb, self._icon_x, iy, 0)  # lit icon, dark kept
        else:
            fb.blit(icon_fb, self._icon_x, iy, 1, _ICON_DARK)  # dark icon on the lit bar
        text_x = self._icon_x + icon_w + 4
        ty = self._mid_y + (self._mid - _FONT) // 2
        fb.text(self._fit(self.options[i], text_x), text_x, ty, ink)
        return buf

    def _fit(self, label, x):
        room = max(1, (self._x1 - x) // _FONT)
        if len(label) > room:
            label = label[: room - 1] + "~"
        return label

    def _caret(self, fb, x, y, up, c):
        # 5 x 3 chevron: '^' when up, 'v' when down
        for k in range(3):
            row = y + (2 - k if up else k)
            fb.pixel(x + k, row, c)
            fb.pixel(x + 4 - k, row, c)

    def _border(self, fb):
        x0, y0, x1, y1 = self._x0, self._y0, self._x1, self._y1
        fb.hline(x0 + 2, y0, x1 - x0 - 4, 1)  # top
        fb.hline(x0 + 2, y1, x1 - x0 - 4, 1)  # bottom
        fb.vline(x0, y0 + 2, y1 - y0 - 3, 1)  # left
        fb.vline(x1, y0 + 3, y1 - y0 - 5, 1)  # right
        fb.pixel(x0 + 1, y0 + 1, 1)  # rounded left corners
        fb.pixel(x0 + 1, y1 - 1, 1)
        fb.line(x1 - 2, y0, x1, y0 + 2, 1)  # cut right corners
        fb.line(x1 - 2, y1, x1, y1 - 2, 1)

    # ------------------------------------------------------------- showing
    def _show(self, buf):
        frame = _framebuf.FrameBuffer(buf, self.width, self.height, _framebuf.MONO_HLSB)
        if self._pal is None:
            self.fb.blit(frame, 0, 0)
        else:
            self.fb.blit(frame, 0, 0, -1, self._pal)
        if self.on_show is not None:
            self.on_show(self)
        elif hasattr(self.fb, "show"):
            self.fb.show()

    def display(self):
        """Show the frame of the current option (the first one at start)."""
        self._show(self.frames[self._index])

    def next(self):
        """Show the next option. After the last one, wraps to the first.
        Returns the new index."""
        self._index += 1
        if self._index >= len(self.options):  # bounds check
            self._index = 0
        self.display()
        return self._index

    def prev(self):
        """Show the previous option. Before the first one, wraps to the last.
        Returns the new index."""
        self._index -= 1
        if self._index < 0:  # bounds check
            self._index = len(self.options) - 1
        self.display()
        return self._index

    def select(self):
        """Flash the middle bar inverted for flash_ms, show the normal frame
        again, then call on_select(menu, index, label). Returns the label.
        Blocks for flash_ms."""
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
            raise IndexError("menu index out of range")
        self._index = i

    @property
    def current(self):
        """Label of the current option."""
        return self.options[self._index]

    def __len__(self):
        return len(self.options)
