# mama.py - 妈 (simplified "ma") and a tall "!" for the "妈妈!" loading screen
#
# oled.text() only draws English letters, numbers and symbols, so Chinese
# characters are drawn as bitmaps. Both drawn from Noto Sans CJK SC Medium
# (SIL Open Font License), then turned into 1-bit pixels.
# framebuf.MONO_HLSB, 1 = lit pixel; the comment beside each row shows it ('#' = lit).
#
#   from bitmaps import mama
#   from ui import loading_screen
#   hello = loading_screen.Pictures(((mama.MA, mama.MA_W, mama.MA_H),) * 2 + ((mama.BANG, mama.BANG_W, mama.BANG_H),))


# 20 x 20: 妈
MA = bytearray(
    b"\x18\x00\x00"  # ...##...............
    b"\x18\xff\x80"  # ...##...#########...
    b"\x18\xff\x80"  # ...##...#########...
    b"\x18\x01\x80"  # ...##..........##...
    b"\x7f\x61\x80"  # .#######.##....##...
    b"\x7f\x61\x80"  # .#######.##....##...
    b"\x33\x61\x80"  # ..##..##.##....##...
    b"\x33\x61\x80"  # ..##..##.##....##...
    b"\x32\x61\x80"  # ..##..#..##....##...
    b"\x36\x7f\xe0"  # ..##.##..##########.
    b"\x66\x7f\xe0"  # .##..##..##########.
    b"\x66\x00\x60"  # .##..##..........##.
    b"\x3e\x00\x60"  # ..#####..........##.
    b"\x1c\xfe\x60"  # ...###..#######..##.
    b"\x0c\xff\xc0"  # ....##..##########..
    b"\x1e\x00\xc0"  # ...####.........##..
    b"\x3f\x00\xc0"  # ..######........##..
    b"\x72\x00\xc0"  # .###..#.........##..
    b"\x60\x07\xc0"  # .##..........#####..
    b"\x00\x07\x00"  # .............###....
)
MA_W = 20
MA_H = 20

# 4 x 20: "!" as tall as 妈
BANG = bytearray(
    b"\x00"  # ....
    b"\x70"  # .###
    b"\x70"  # .###
    b"\x70"  # .###
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x60"  # .##.
    b"\x00"  # ....
    b"\x00"  # ....
    b"\x60"  # .##.
    b"\xf0"  # ####
    b"\xf0"  # ####
    b"\x60"  # .##.
)
BANG_W = 4
BANG_H = 20
