# mail.py - the envelope for sending messages (pet/message.py)
#
# framebuf.MONO_HLSB, 1 = lit pixel; the comment beside each row shows it ('#' = lit).
# Its trail uses the 5 x 5 and 3 x 3 stars from stars.py.
#
#   import framebuf
#   from bitmaps import mail
#   env_fb = framebuf.FrameBuffer(mail.ENVELOPE, mail.ENVELOPE_W, mail.ENVELOPE_H, framebuf.MONO_HLSB)
#   oled.blit(env_fb, x, y, 0)       # 0 = background pixels are transparent


# 27 x 18: envelope with a star seal on the flap
ENVELOPE = bytearray(
    b"\xff\xff\xff\xe0"  # ###########################
    b"\xc0\x00\x00\x60"  # ##.......................##
    b"\xa0\x00\x00\xa0"  # #.#.....................#.#
    b"\x90\x00\x01\x20"  # #..#...................#..#
    b"\x88\x00\x02\x20"  # #...#.................#...#
    b"\x84\x00\x04\x20"  # #....#...............#....#
    b"\x82\x00\x08\x20"  # #.....#.............#.....#
    b"\x81\x04\x10\x20"  # #......#.....#.....#......#
    b"\x80\x04\x00\x20"  # #............#............#
    b"\x80\x0e\x00\x20"  # #...........###...........#
    b"\x80\x7f\xc0\x20"  # #........#########........#
    b"\x80\x3f\x80\x20"  # #.........#######.........#
    b"\x80\x1f\x00\x20"  # #..........#####..........#
    b"\x80\x1b\x00\x20"  # #..........##.##..........#
    b"\x80\x31\x80\x20"  # #.........##...##.........#
    b"\x80\x20\x80\x20"  # #.........#.....#.........#
    b"\x80\x00\x00\x20"  # #.........................#
    b"\xff\xff\xff\xe0"  # ###########################
)
ENVELOPE_W = 27
ENVELOPE_H = 18
