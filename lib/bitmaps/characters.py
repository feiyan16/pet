# characters.py - the pets' small sprites, for the games
#
# One sprite per character (max 18 x 9 px), framebuf.MONO_HLSB, 1 = lit pixel,
# 0 = background (black). Each has a bytearray plus _W and _H; the comment
# beside each row shows it ('#' = lit).
#
# To draw one:
#   import framebuf
#   from bitmaps import characters
#   buf, w, h = characters.CHARACTERS['bean']
#   sprite = framebuf.FrameBuffer(buf, w, h, framebuf.MONO_HLSB)
#   oled.blit(sprite, x, y, 0)     # 0 = background pixels are transparent
#
# Make each FrameBuffer once (for example when a character is chosen),
# not every frame.

# --- characters -------------------------------------------------------------

BEAN = bytearray(
    b"\x70\x03\x80"  # .###..........###.
    b"\xf8\x07\xc0"  # #####........#####
    b"\xf8\x07\xc0"  # #####........#####
    b"\xf0\x03\xc0"  # ####..........####
    b"\xf0\x03\xc0"  # ####..........####
    b"\xfa\x17\xc0"  # #####.#....#.#####
    b"\xf9\xe7\xc0"  # #####..####..#####
    b"\x70\x03\x80"  # .###..........###.
)
BEAN_W = 18
BEAN_H = 8

OVAL = bytearray(
    b"\x30\x03\x00"  # ..##..........##..
    b"\x78\x07\x80"  # .####........####.
    b"\xfc\x0f\xc0"  # ######......######
    b"\xfc\x0f\xc0"  # ######......######
    b"\xfc\x0f\xc0"  # ######......######
    b"\xfd\x2f\xc0"  # ######.#..#.######
    b"\x78\xc7\x80"  # .####...##...####.
    b"\x30\x03\x00"  # ..##..........##..
)
OVAL_W = 18
OVAL_H = 8

OBLONG = bytearray(
    b"\x70\x03\x80"  # .###..........###.
    b"\xf8\x07\xc0"  # #####........#####
    b"\xf8\x07\xc0"  # #####........#####
    b"\xf8\x07\xc0"  # #####........#####
    b"\xf8\x07\xc0"  # #####........#####
    b"\xf8\x07\xc0"  # #####........#####
    b"\xfa\x17\xc0"  # #####.#....#.#####
    b"\xf9\xe7\xc0"  # #####..####..#####
    b"\x70\x03\x80"  # .###..........###.
)
OBLONG_W = 18
OBLONG_H = 9

HALFMOON = bytearray(
    b"\xc0\x00\xc0"  # ##..............##
    b"\xf0\x03\xc0"  # ####..........####
    b"\xfc\x0f\xc0"  # ######......######
    b"\xfe\x1f\xc0"  # #######....#######
    b"\x7c\x0f\x80"  # .#####......#####.
    b"\x39\x27\x00"  # ..###..#..#..###..
    b"\x00\xc0\x00"  # ........##........
)
HALFMOON_W = 18
HALFMOON_H = 7

VISOR = bytearray(
    b"\x7f\xff\x80"  # .################.
    b"\xff\xff\xc0"  # ##################
    b"\xff\xff\xc0"  # ##################
    b"\x7f\xff\x80"  # .################.
    b"\x00\x00\x00"  # ..................
    b"\x02\x10\x00"  # ......#....#......
    b"\x01\xe0\x00"  # .......####.......
)
VISOR_W = 18
VISOR_H = 7

POINTED = bytearray(
    b"\x38\x07\x00"  # ..###........###..
    b"\x7c\x0f\x80"  # .#####......#####.
    b"\xfe\x1f\xc0"  # #######....#######
    b"\x7c\x0f\x80"  # .#####......#####.
    b"\x38\x07\x00"  # ..###........###..
    b"\x02\x10\x00"  # ......#....#......
    b"\x01\xe0\x00"  # .......####.......
)
POINTED_W = 18
POINTED_H = 7

SCRIBBLE = bytearray(
    b"\x78\x07\x80"  # .####........####.
    b"\x94\x0a\x40"  # #..#.#......#.#..#
    b"\xba\x17\x80"  # #.###.#....#.####.
    b"\x7a\x15\xc0"  # .####.#....#.#.###
    b"\xa4\x0e\x80"  # #.#..#......###.#.
    b"\x58\x02\x80"  # .#.##.........#.#.
    b"\x02\x10\x00"  # ......#....#......
    b"\x01\xe0\x00"  # .......####.......
)
SCRIBBLE_W = 18
SCRIBBLE_H = 8


# all characters, in menu order: name -> (bytearray, width, height)
CHARACTERS = {
    "bean": (BEAN, BEAN_W, BEAN_H),
    "oval": (OVAL, OVAL_W, OVAL_H),
    "oblong": (OBLONG, OBLONG_W, OBLONG_H),
    "halfmoon": (HALFMOON, HALFMOON_W, HALFMOON_H),
    "visor": (VISOR, VISOR_W, VISOR_H),
    "pointed": (POINTED, POINTED_W, POINTED_H),
    "scribble": (SCRIBBLE, SCRIBBLE_W, SCRIBBLE_H),
}
# ORDER MATTERS: 2-player games send the index into NAMES, so only ever ADD names
# at the end. A keychain with an older list shows an unknown index as 'oblong'.
NAMES = ("bean", "oval", "oblong", "halfmoon", "visor", "pointed", "scribble")
