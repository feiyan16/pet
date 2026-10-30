# stars.py - the one star design used everywhere, in 3 sizes
#
# framebuf.MONO_HLSB, 1 = lit pixel. Each size is a bytearray plus _W and _H;
# the comment beside each row shows it ('#' = lit).
#
# Draw one (make the FrameBuffer once, not every frame):
#   import framebuf
#   from bitmaps import stars
#   star_fb = framebuf.FrameBuffer(stars.STAR, stars.STAR_W, stars.STAR_H, framebuf.MONO_HLSB)
#   oled.blit(star_fb, x, y, 0)       # 0 = background pixels are transparent
#
# Not used by roboface: its feed() keeps its own 7, 5 and 3 px stars, the same
# design, so roboface.mpy doesn't need recompiling. (So no 7 px star here.)


# 11 x 10: countdown, menu icon
STAR = bytearray(
    b'\x04\x00'  # .....#.....
    b'\x0e\x00'  # ....###....
    b'\x0e\x00'  # ....###....
    b'\xff\xe0'  # ###########
    b'\x7f\xc0'  # .#########.
    b'\x3f\x80'  # ..#######..
    b'\x1f\x00'  # ...#####...
    b'\x3b\x80'  # ..###.###..
    b'\x31\x80'  # ..##...##..
    b'\x40\x40'  # .#.......#.
)
STAR_W = 11
STAR_H = 10

# 5 x 5: shooter lives
STAR_5 = bytearray(
    b'\x20'  # ..#..
    b'\x20'  # ..#..
    b'\xf8'  # #####
    b'\x70'  # .###.
    b'\x50'  # .#.#.
)
STAR_5_W = 5
STAR_5_H = 5

# 3 x 3: the plus-shaped stars in the jump sky tile (sky.py)
STAR_3 = bytearray(
    b'\x40'  # .#.
    b'\xe0'  # ###
    b'\x40'  # .#.
)
STAR_3_W = 3
STAR_3_H = 3
