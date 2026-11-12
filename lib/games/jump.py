# jump.py - the 1-player jump game ("Play" -> jump): your pet's mini sprite
# runs along the ground and jumps over obstacles, like the offline dino game.
#
#   from games import jump
#   jump.run(pet_mode)        # pet_mode.mini() gives the sprite
#
# RIGHT = jump (hold for a higher jump, let go early for a short hop),
# LEFT = leave the game. The score goes up the longer you survive; the best
# score is saved in dino_hi.txt.

import time  # ticks_ms, sleep_ms
import random  # obstacle types and gaps
import framebuf  # FrameBuffer: wraps sprite bytes so they can be blit()-ed

from utils import scroll, Choice, countdown
from ui.popup import Popup
from peripherals import *  # oled, OLED_W, OLED_H, left_btn, right_btn
from logger import log, INFO
from bitmaps import sky  # the starry background tile


class Result:
    """How a game ended (what play() returns first)."""

    CRASH = 0  # hit an obstacle
    EXIT = -1  # left with the LEFT pad


def placeholder(w, h):
    """An outlined box, used until you insert real sprites. Returns (bytearray, w, h)."""
    rb = (w + 7) // 8  # bytes per row (8 pixels per byte, rounded up)
    buf = bytearray(rb * h)
    for y in range(h):
        for x in range(w):
            if y == 0 or y == h - 1 or x == 0 or x == w - 1:  # border pixels only
                buf[y * rb + x // 8] |= 0x80 >> (x % 8)  # set pixel (x, y): leftmost pixel = highest bit
    return (buf, w, h)


# Each obstacle type: (bytes, w, h, lift). lift = how far above the ground it floats (0 = on it).
OBSTACLES = [
    placeholder(6, 12) + (0,),  # small cactus
    placeholder(12, 10) + (0,),  # wide, low cactus
    placeholder(7, 16) + (0,),  # tall cactus
]
obstacle_fbs = [framebuf.FrameBuffer(o[0], o[1], o[2], framebuf.MONO_HLSB) for o in OBSTACLES]  # same order


# ---------------------------------------------------------------- background tile
# The 64 x 40 sky tile is in bitmaps/sky.py, repeated across the sky and scrolled slowly.
STAR_Y = 10  # sky band rows 10-49
STAR_PARALLAX = 0.25  # stars move at 1/4 of the ground speed (feels far away)
star_fb = framebuf.FrameBuffer(sky.SKY, sky.SKY_W, sky.SKY_H, framebuf.MONO_HLSB)


GROUND_Y = 56  # the ground line; feet stand on the row above it
DINO_X = 10  # the dino's left edge (it never moves sideways)
GRAVITY = 0.6  # added to the fall speed every frame
JUMP_SPEED = -6.5  # upward speed at take-off (more negative = higher)
SHORT_HOP_SPEED = -3.0  # releasing the pad early cuts the rise to this
START_SPEED = 3.0  # ground scroll, pixels per frame
MAX_SPEED = 7.0  # the ground never scrolls faster than this
SPEED_STEP = 0.5  # added every SPEED_EVERY frames
SPEED_EVERY = 300  # frames (about 10 s at 30 frames per second)
GAP_MIN, GAP_MAX = (28, 50)  # space between obstacles, in frames of travel (a full jump lasts 21)
HIT_MARGIN = 2  # shrinks the dino's hitbox so near misses feel fair
RUN_FRAME_TICKS = 4  # frames between run-animation steps
FRAME_MS = 33  # about 30 frames per second
HISCORE_FILE = "dino_hi.txt"  # the high score, kept between games and restarts


def load_hiscore():
    """The saved high score, or 0 if there isn't one yet."""
    try:
        with open(HISCORE_FILE) as f:
            return int(f.read())  # the file holds just the number, as text
    except (OSError, ValueError):  # no file yet, or it's damaged
        return 0


def save_hiscore(value):
    """Save a new high score (silently skipped if the flash can't be written)."""
    try:
        with open(HISCORE_FILE, "w") as f:  # "w" = replace the file
            f.write(str(value))
    except OSError:
        pass  # e.g. flash full: the game carries on without saving


class Dino:
    """The player: the pet's mini sprite, with its jump physics."""

    def __init__(self, character, w, h):
        # run animation frames (just one for now) and the frame shown in the air
        self.run_fbs = [framebuf.FrameBuffer(character, w, h, framebuf.MONO_HLSB)]
        self.jump_fb = self.run_fbs[0]
        self.w, self.h = w, h  # sprite size
        self.stand_y = GROUND_Y - h  # top row when standing on the ground
        self.y = self.stand_y  # top row (a float while jumping)
        self.vy = 0.0  # vertical speed: negative = going up

    def on_ground(self):
        """True when standing (not in the air)."""
        return self.y >= self.stand_y  # y grows downwards, so "at or below" = on the ground

    def update(self, jump_tapped, jump_held):
        """Move one frame. jump_tapped: RIGHT was just touched; jump_held: still touched."""
        if jump_tapped and self.on_ground():
            self.vy = JUMP_SPEED  # take off
        if not jump_held and self.vy < SHORT_HOP_SPEED:
            self.vy = SHORT_HOP_SPEED  # let go early = shorter jump
        self.vy += GRAVITY  # gravity pulls down every frame
        self.y += self.vy  # move by the current speed
        if self.y >= self.stand_y:  # landed
            self.y = self.stand_y
            self.vy = 0.0


def overlap(ax, ay, aw, ah, bx, by, bw, bh):
    """True if two rectangles overlap (x, y = top-left corner)."""
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def play(dino, hiscore=0):
    """Plays until the dino crashes or the left pad is touched.
    Returns (Result.CRASH or Result.EXIT, score)."""
    obstacles = []  # each: [x, type index]
    speed = START_SPEED
    next_gap = 0  # 0 = spawn the first obstacle straight away
    since_spawn = 0.0  # pixels scrolled since the last obstacle appeared
    score = 0  # counts frames; shown as score // 5
    star_offset = 0.0  # how far the sky has scrolled (px, wraps every tile)
    ground_offset = 0  # how far the ground specks have scrolled (px, wraps every 16)
    right_was = True  # the tap that started the game must not jump
    left_was = True

    while True:
        start = time.ticks_ms()  # for the steady frame rate

        # ---- 1. input (act on the moment a pad is touched, not while it's held)
        right = right_btn.value() == 1
        left = left_btn.value() == 1
        jump_tapped = right and not right_was  # touched now, not touched last frame
        if left and not left_was:
            log(INFO, "jump: exit, score", score // 5)
            return Result.EXIT, score // 5
        right_was, left_was = right, left  # remember for the next frame

        # ---- 2. dino
        dino.update(jump_tapped, right)

        # ---- 3. obstacles: move, drop the ones gone off the left, spawn new ones
        for o in obstacles:
            o[0] -= speed  # everything slides left
        while obstacles and obstacles[0][0] < -OBSTACLES[obstacles[0][1]][1]:  # fully off screen
            obstacles.pop(0)
        # spacing is measured as distance travelled since the last spawn, NOT from the last
        # obstacle on screen: that one may already be gone, which would spawn the next too soon
        since_spawn += speed
        if since_spawn >= next_gap:
            obstacles.append([OLED_W, random.randint(0, len(OBSTACLES) - 1)])  # new one at the right edge
            since_spawn = 0
            next_gap = random.randint(int(speed * GAP_MIN), int(speed * GAP_MAX))  # wider gaps when faster

        # ---- 4. crash?
        dx, dy = DINO_X + HIT_MARGIN, int(dino.y) + HIT_MARGIN  # the dino's (shrunk) hitbox
        dw, dh = dino.w - 2 * HIT_MARGIN, dino.h - 2 * HIT_MARGIN
        for o in obstacles:
            _, ow, oh, lift = OBSTACLES[o[1]]
            oy = GROUND_Y - oh - lift  # obstacle's top row
            if overlap(dx, dy, dw, dh, int(o[0]), oy, ow, oh):
                draw(dino, obstacles, score, star_offset, ground_offset, hiscore)  # show the crash
                log(INFO, "jump: crash, score", score // 5)
                return Result.CRASH, score // 5

        # ---- 5. score, speed, scrolling offsets
        score += 1
        speed = min(START_SPEED + (score // SPEED_EVERY) * SPEED_STEP, MAX_SPEED)  # speeds up in steps
        star_offset = (star_offset + speed * STAR_PARALLAX) % sky.SKY_W
        ground_offset = (ground_offset + int(speed)) % 16

        # ---- 6. draw, then hold a steady frame rate
        draw(dino, obstacles, score, star_offset, ground_offset, hiscore)
        wait = FRAME_MS - time.ticks_diff(time.ticks_ms(), start)  # time left in this frame
        if wait > 0:
            time.sleep_ms(wait)


def draw(dino, obstacles, score, star_offset, ground_offset, hiscore=0):
    """Draw one game frame and show it."""
    oled.fill(0)
    # background: the star tile, repeated across the screen (3 copies cover 128 px)
    x = -int(star_offset)
    while x < OLED_W:
        oled.blit(star_fb, x, STAR_Y)
        x += sky.SKY_W
    # ground line + specks that scroll with it
    oled.hline(0, GROUND_Y, OLED_W, 1)
    for gx in range(-ground_offset, OLED_W, 16):  # a speck every 16 px
        oled.pixel(gx + 8, GROUND_Y + 3, 1)
    # obstacles
    for o in obstacles:
        _, ow, oh, lift = OBSTACLES[o[1]]
        oled.blit(obstacle_fbs[o[1]], int(o[0]), GROUND_Y - oh - lift, 0)  # 0 = dark pixels see-through
    # dino: jump frame in the air, run animation on the ground
    if dino.on_ground():
        fb = dino.run_fbs[(score // RUN_FRAME_TICKS) % len(dino.run_fbs)]
    else:
        fb = dino.jump_fb
    oled.blit(fb, DINO_X, int(dino.y), 0)
    # high score top-left (goes up with the score once it's beaten), current score top-right
    oled.text("HI " + str(max(hiscore, score // 5)), 0, 0)
    s = str(score // 5)
    oled.text(s, OLED_W - len(s) * 8, 0)  # right-aligned
    oled.show()


def run(pet):
    """The whole game: "Jump" popup, countdown, play, score popup, play again..."""
    play_game = Choice(False)  # set by the popups: True = play (again)

    def chosen(_, index, __):
        if index > 0:  # option 0 is "exit"; 1 is "start" / "play again"
            play_game.value = True

    ready_popup = Popup(oled, "Jump", ["exit", "start"], on_select=chosen)
    scroll(ready_popup)  # times out after 5 s = exit

    hiscore = load_hiscore()
    while play_game.value:
        countdown()  # READY, then 3 stars disappearing one by one
        result, score = play(Dino(*pet.mini()), hiscore)  # a fresh dino from the pet's mini sprite
        new_hi = score > hiscore
        if new_hi:
            log(INFO, "jump: new high score", score)
            hiscore = score
            save_hiscore(hiscore)
        if result == Result.EXIT:
            break  # left with the LEFT pad: no score popup
        time.sleep_ms(500)  # a moment to see the crash

        play_game.value = False  # stays False unless "play again" is chosen
        text = f"NEW HI! {score}" if new_hi else f"SCORE: {score}\nHI: {hiscore}"
        play_again_popup = Popup(oled, text, ["exit", "play again"], on_select=chosen)
        scroll(play_again_popup)

    oled.fill(0)  # leave a blank screen for pet mode
    oled.show()
