import time
import framebuf
import random

from peripherals import oled, OLED_W, OLED_H, left_btn, right_btn
from connect import e, parse, pack, safe_send, radio_off, wait_for, POS, FIRE, LIVES, PING, QUIT, REMATCH, drain
from connect import Lobby, my_id, short_id
from bitmaps import characters, stars, bullets
from utils import scroll, Choice, countdown, wait_release
from ui.menu import Menu
from ui.popup import Popup
from ui import loading_screen
from logger import log, INFO, WARN

# Bot difficulty. Pick one with DIFFICULTY below.
#   speed:     pixels per frame the bot moves
#   think_ms:  how often it picks a new target (slower = more reaction delay)
#   aim_error: random +/- pixels added to its target (bigger = sloppier aim)
#   fire_range: fires only when its centre is within this many pixels of yours
#   cooldown:  minimum ms between its shots
#   fire_chance: % chance it actually shoots when lined up (a skipped shot still waits a cooldown)
#   dodge:     % chance it notices one of your bullets and moves away
BOT_LEVELS = {
    "easy": {
        "speed": 1,
        "think_ms": 450,
        "aim_error": 12,
        "fire_range": 3,
        "cooldown": 1000,
        "fire_chance": 50,
        "dodge": 10,
    },
    "normal": {
        "speed": 1,
        "think_ms": 250,
        "aim_error": 6,
        "fire_range": 5,
        "cooldown": 700,
        "fire_chance": 75,
        "dodge": 30,
    },
    "hard": {
        "speed": 2,
        "think_ms": 150,
        "aim_error": 3,
        "fire_range": 7,
        "cooldown": 450,
        "fire_chance": 100,
        "dodge": 60,
    },
}

PING_EVERY_MS = 1000  # heartbeat: tell the opponent "still here" once a second
LINK_TIMEOUT_MS = 3000  # nothing heard for this long = the other keychain is gone
IDLE_MS = 10000
FRAME_MS = 33  # about 30 frames per second
PLAYER_SPEED = 2  # move 2 pixels per movement
BULLET_SPEED = 3  # moves 3 pixels per movement
FIRE_COOLDOWN_MS = 300
INVINCIBLE_MS = 650
CHORD_MS = 60

# Maximum character dimensions
MAX_W = 18
MAX_H = 9

# y = 0-4     lives: one star each (opponent left, you right)
# y = 5-6     2-pixel gap
# y = 7-15    opponent (flipped, top-aligned; 9 rows for the tallest sprite)
# y = 16-54   arena
# y = 55-63   you (bottom-aligned)

LIFE_Y = 0
LIFE_GAP = 2  # space between the life stars (px)
OPP_Y = LIFE_Y + stars.STAR_5_H + 2  # 2-pixel gap under the stars -> 7

life_fb = framebuf.FrameBuffer(stars.STAR_5, stars.STAR_5_W, stars.STAR_5_H, framebuf.MONO_HLSB)
bullet_fb = framebuf.FrameBuffer(bullets.BULLET, bullets.BULLET_W, bullets.BULLET_H, framebuf.MONO_HLSB)

press_start = None
chord_locked = False


class Result:
    LOST = 0
    WIN = 1
    IDLE = 2
    OPP_IDLE = 3
    LOST_LINK = 4


class Player:
    def __init__(self, data, w, h, speed=PLAYER_SPEED, bullet_speed=BULLET_SPEED, is_opp=False):
        self.character = Player.load_character(data, w, h, is_opp)
        self.width = w
        self.height = h
        # starting center x-value,
        # since it starts in the middle of the screen,
        # it's just the center of the screen
        self.x = OLED_W // 2
        # starting y position
        self.y = OPP_Y if is_opp else OLED_H - h
        self.lives = 3
        self.is_opp = is_opp
        self.bullets = []
        self.bullet_speed = bullet_speed
        self.speed = speed
        self.last_sent_x = None  # last position sent; None = nothing sent yet
        self.last_shot = time.ticks_add(time.ticks_ms(), -FIRE_COOLDOWN_MS)  # pretend the last shot was long ago
        self.hit_time = None

    def left_edge(self):
        # x position
        # current center x-value - left half of the width length
        return self.x - self.width // 2

    def clamp(self):
        half = self.width // 2
        self.x = max(half, min(OLED_W - (self.width - half), self.x))

    def hit_by(self, bx, by):  # bullet box is now 1 x 3
        return (
            self.left_edge() <= bx + bullets.BULLET_W - 1
            and bx <= self.left_edge() + self.width - 1
            and self.y <= by + bullets.BULLET_H - 1
            and by <= self.y + self.height - 1
        )

    def draw(self):
        oled.blit(self.character, self.left_edge(), self.y, 0)

    @staticmethod
    def load_character(data, w, h, is_opp):
        assert w <= MAX_W and h <= MAX_H
        row_bytes = (w + 7) // 8
        assert len(data) == row_bytes * h, "byte count doesn't match w x h"
        out = bytearray(len(data))
        for r in range(h):
            src = (h - 1 - r) if is_opp else r  # flip = read rows bottom-up
            for b in range(row_bytes):
                out[r * row_bytes + b] = data[src * row_bytes + b]
        return framebuf.FrameBuffer(out, w, h, framebuf.MONO_HLSB)


class Bot(Player):
    def __init__(self, data, w, h, speed, think_ms, aim_error, fire_range, cooldown, fire_chance, dodge):
        super().__init__(data, w, h, speed=speed, is_opp=True)
        self.think_ms = think_ms
        self.aim_error = aim_error
        self.fire_range = fire_range
        self.cooldown = cooldown
        self.fire_chance = fire_chance
        self.dodge = dodge
        self.last_shot = time.ticks_ms()  # the bot waits one cooldown before its first shot


class Frame:
    def __init__(self, player, opponent):
        self.player = player
        self.opponent = opponent
        self.last_input = None
        self.start = None
        self.direction = None
        self.shots_fired = None
        self.result = None

    def draw_lives(self):
        for i in range(self.player.lives):
            oled.blit(life_fb, OLED_W - stars.STAR_5_W - i * (stars.STAR_5_W + LIFE_GAP), LIFE_Y, 0)
        for i in range(self.opponent.lives):
            oled.blit(life_fb, i * (stars.STAR_5_W + LIFE_GAP), LIFE_Y, 0)

    def draw_bullets(self):
        for b in self.player.bullets:
            oled.blit(bullet_fb, b[0], b[1], 0)
        for b in self.opponent.bullets:
            oled.blit(bullet_fb, b[0], b[1], 0)

    def move_bullets(self):
        for b in self.player.bullets:
            b[1] -= self.player.bullet_speed
        for b in self.opponent.bullets:
            b[1] += self.opponent.bullet_speed

    def update_player_pos(self):
        self.player.x += self.direction * self.player.speed
        self.player.clamp()
        self.send_pos()

    def update_player_bullets(self):
        if self.shots_fired and time.ticks_diff(self.start, self.player.last_shot) >= FIRE_COOLDOWN_MS:
            # bullet is positioned directly above character and in the center
            self.player.bullets.append([self.player.x, self.player.y - bullets.BULLET_H])
            self.send_shots_fired()
            self.player.last_shot = self.start

    def restart_clocks(self):
        """Called right before play starts (after the countdown)."""
        self.last_input = time.ticks_ms()  # idle timer starts now

    def check_idle(self):
        pass

    def update_opponent(self):
        pass

    def send_pos(self):
        pass

    def send_shots_fired(self):
        pass

    def send_lives_left(self):
        pass

    def rematch(self, result):
        do_rematch = Choice(False)

        def chosen(_, index, __):
            if index > 0:
                do_rematch.value = True

        rematch_popup = Popup(oled, result, ["exit", "rematch"], on_select=chosen)
        rematch_popup.display()
        scroll(rematch_popup)

        return do_rematch.value

    def filter_bullets(self):
        pass

    def filter_opponent_bullets(self):
        keep = []
        for b in self.opponent.bullets:
            if self.player.hit_by(b[0], b[1]):
                if self.player.hit_time is None:  # you aren't blinking: you lose a life
                    self.player.lives -= 1
                    self.player.hit_time = self.start
                    self.send_lives_left()
            elif b[1] < OLED_H:
                keep.append(b)  # still on screen
        self.opponent.bullets[:] = keep

    def reset_hit_time(self):
        # resetting hit time once 650ms has passed
        if self.player.hit_time is not None and time.ticks_diff(self.start, self.player.hit_time) >= INVINCIBLE_MS:
            self.player.hit_time = None

    def reset_opponent_hit_time(self):
        if self.opponent.hit_time is not None and time.ticks_diff(self.start, self.opponent.hit_time) >= INVINCIBLE_MS:
            self.opponent.hit_time = None

    def draw(self):
        blink = (time.ticks_ms() // 100) % 2 == 1  # True on the "hidden" 100 ms slots
        oled.fill(0)
        self.draw_lives()
        if self.opponent.hit_time is None or not blink:
            self.opponent.draw()
        if self.player.hit_time is None or not blink:
            self.player.draw()
        self.draw_bullets()
        oled.show()

    def reset(self):
        """Put both players back to the start of a match (used for rematches)."""
        for p in (self.player, self.opponent):
            p.x = OLED_W // 2  # back to the centre
            p.lives = 3
            p.bullets[:] = []  # clear bullets in place
            p.hit_time = None  # not blinking
            p.last_sent_x = None  # forces a fresh POS on the first frame


class TwoPlayerFrame(Frame):
    def __init__(self, player, opponent, opp_mac):
        super().__init__(player, opponent)
        self.opp_mac = opp_mac
        now = time.ticks_ms()
        self.last_input = now
        self.last_heard_from_opp = now
        self.last_ping = now

    def restart_clocks(self):
        super().restart_clocks()
        now = time.ticks_ms()
        self.last_heard_from_opp = now  # the countdown must not count as silence
        self.last_ping = time.ticks_add(now, -PING_EVERY_MS)  # send a PING on the first frame

    def check_idle(self):
        if time.ticks_diff(self.start, self.last_input) >= IDLE_MS:
            for _ in range(3):
                safe_send(self.opp_mac, pack(QUIT))
            self.result = Result.IDLE
            log(INFO, "match ended: idle")

    def update_opponent(self):
        now = time.ticks_ms()
        while e.any():
            mac, msg = e.recv(0)
            kind, value = parse(msg)
            if kind is None or mac != self.opp_mac:
                continue
            self.last_heard_from_opp = now
            if kind == POS:
                self.opponent.x = OLED_W - value
            elif kind == FIRE:
                self.opponent.bullets.append([OLED_W - value, self.opponent.y + self.opponent.height])
            elif kind == LIVES:
                if value < self.opponent.lives:  # they really lost one (not a repeat)
                    self.opponent.hit_time = now  # blink them on your screen
                self.opponent.lives = value
            elif kind == QUIT:
                self.result = Result.OPP_IDLE
                log(INFO, "match ended: opponent idle")
        # heartbeat: once a second, so the opponent hears us even while we stand still
        if time.ticks_diff(self.start, self.last_ping) >= PING_EVERY_MS:
            safe_send(self.opp_mac, pack(PING))
            self.last_ping = self.start
        # watchdog: nothing at all from them for 3 s = battery died or out of range
        if time.ticks_diff(time.ticks_ms(), self.last_heard_from_opp) > LINK_TIMEOUT_MS:
            self.result = Result.LOST_LINK
            log(WARN, "match ended: lost link")

    def send_pos(self):
        # tell opponent's esp32 position has been changed
        if self.player.x != self.player.last_sent_x:
            safe_send(self.opp_mac, pack(POS, self.player.x))
            self.player.last_sent_x = self.player.x

    def send_shots_fired(self):
        # tell the opponent where you fired from
        safe_send(self.opp_mac, pack(FIRE, self.player.x))

    def send_lives_left(self):
        # send your number of lives left to opponent 3 times
        for _ in range(3):
            safe_send(self.opp_mac, pack(LIVES, self.player.lives))

    def rematch(self, result):
        do_rematch = super().rematch(result)
        if do_rematch:
            # Tell opponent you want a rematch 3 times to be safe
            for _ in range(3):
                safe_send(self.opp_mac, pack(REMATCH, 1))
            # Wait for opponent's choice
            kind, value = wait_for(self.opp_mac, (REMATCH, QUIT), 15000)
            if kind != REMATCH or value != 1:  # they chose exit, left, or never answered
                return False
        else:
            # Tell opponent you do not want a rematch 3 times to be safe
            for _ in range(3):
                safe_send(self.opp_mac, pack(REMATCH, 0))
            return False
        return True

    def filter_bullets(self):
        """
        keeps only your bullets that should still be on screen and throws away the rest.
        Keep bullets that is still under your opponent and did not hit them
        if it hit them, don't make it pass through them
        """
        self.player.bullets[:] = [
            b for b in self.player.bullets if b[1] >= OPP_Y and not self.opponent.hit_by(b[0], b[1])
        ]

    def reset(self):
        super().reset()
        now = time.ticks_ms()
        self.last_input = now  # restart the idle clock
        self.last_heard_from_opp = now
        self.last_ping = now
        drain()


class SinglePlayerFrame(Frame):
    def __init__(self, player, opponent):
        super().__init__(player, opponent)
        now = time.ticks_ms()
        self.last_input = now
        self.opponent_target = opponent.x  # head nowhere yet
        self.opponent_last_think = time.ticks_add(now, -opponent.think_ms)  # think at once

    def check_idle(self):
        if time.ticks_diff(self.start, self.last_input) >= IDLE_MS:
            self.result = Result.IDLE

    def update_opponent(self):
        # ---- 3. the bot thinks (only every think_ms, which gives it a reaction delay)
        if time.ticks_diff(self.start, self.opponent_last_think) >= self.opponent.think_ms:
            self.opponent_last_think = self.start
            self.opponent_target = self.player.x + random.randint(-self.opponent.aim_error, self.opponent.aim_error)
            # look for one of your bullets heading at it: in its columns, within 30 px below it
            for b in self.player.bullets:
                close_x = self.opponent.left_edge() - 2 <= b[0] <= self.opponent.left_edge() + self.opponent.width + 1
                close_y = 0 <= b[1] - (self.opponent.y + self.opponent.height) <= 30
                if close_x and close_y and random.randint(1, 100) <= self.opponent.dodge:
                    # step away from the bullet, toward the side with more room
                    away = self.opponent.width if b[0] <= self.opponent.x else -self.opponent.width
                    if (
                        self.opponent.x + away > OLED_W - self.opponent.width // 2
                        or self.opponent.x + away < self.opponent.width // 2
                    ):
                        away = -away
                    self.opponent_target = self.opponent.x + away
                    break
        # ---- 4. the bot moves toward its target and fires when lined up
        if self.opponent.x < self.opponent_target:
            self.opponent.x += min(self.opponent.speed, self.opponent_target - self.opponent.x)
        elif self.opponent.x > self.opponent_target:
            self.opponent.x -= min(self.opponent.speed, self.opponent.x - self.opponent_target)
        self.opponent.clamp()
        lined_up = abs(self.opponent.x - self.player.x) <= self.opponent.fire_range
        if lined_up and time.ticks_diff(self.start, self.opponent.last_shot) >= self.opponent.cooldown:
            if random.randint(1, 100) <= self.opponent.fire_chance:
                self.opponent.bullets.append(
                    [self.opponent.x, self.opponent.y + self.opponent.height]
                )  # the bot's centre, just below it
            self.opponent.last_shot = self.start  # shot or hesitated: wait a cooldown

    def filter_bullets(self):
        """
        keeps only your bullets that should still be on screen and throws away the rest.
        Keep bullets that is still under your opponent and did not hit them
        if it hit them, don't make it pass through them
        """
        keep = []
        for b in self.player.bullets:
            if self.opponent.hit_by(b[0], b[1]):
                if self.opponent.hit_time is None:  # the bot isn't blinking: it loses a life
                    self.opponent.lives -= 1
                    self.opponent.hit_time = self.start
            elif b[1] >= OPP_Y:
                keep.append(b)  # still in the arena
        self.player.bullets[:] = keep

    def reset(self):
        super().reset()
        now = time.ticks_ms()
        self.last_input = now  # restart the idle clock
        self.opponent_target = self.opponent.x
        self.opponent_last_think = time.ticks_add(now, -self.opponent.think_ms)
        self.opponent.last_shot = now


def read_controls():
    """Returns (move, fire). move is -1 left, 0 none, +1 right. fire is True once per two-pad press."""
    global press_start, chord_locked

    left_pressed = left_btn.value() == 1
    right_pressed = right_btn.value() == 1
    now = time.ticks_ms()

    # nothing pressed: reset everything
    if not left_pressed and not right_pressed:
        press_start = None
        chord_locked = False
        return 0, False

    # a new press just started
    if press_start is None:
        press_start = now

    # fire ONCE, not every frame
    if left_pressed and right_pressed:
        if not chord_locked:
            chord_locked = True
            return 0, True
        # Don't do anything, both pressed, but chord is locked, must wait before you can fire again
        return 0, False

    # one finger lifted after firing: don't move
    if chord_locked:
        return 0, False

    # still waiting to see if it's a chord
    if time.ticks_diff(now, press_start) < CHORD_MS:
        return 0, False

    return (-1 if left_pressed else 1), False  # a single pad: move


def play_match(frame):
    global press_start, chord_locked

    press_start, chord_locked = None, False  # forget the last match's press

    while left_btn.value() or right_btn.value():  # wait for fingers off the pads
        time.sleep_ms(10)

    countdown()  # READY, then 3 stars disappearing one by one
    frame.restart_clocks()  # idle (and link) timers start now

    while True:
        frame.start = time.ticks_ms()
        # ---- 1. your input
        frame.direction, frame.shots_fired = read_controls()
        if left_btn.value() or right_btn.value():
            frame.last_input = frame.start

        # ---- 2. check if screen is idle
        frame.check_idle()
        if frame.result is not None:
            return frame.result

        # ---- 3. move and fire (you)
        frame.update_player_pos()  # move + clamp (+ send POS in 2-player)
        frame.update_player_bullets()  # fire if allowed (+ send FIRE in 2-player)

        # ---- 4. opponent: radio messages (2-player) or the bot (1-player)
        frame.update_opponent()
        if frame.result is not None:  # opponent quit, or the link dropped
            return frame.result

        # ---- 5. move bullets and check hits
        frame.move_bullets()
        frame.filter_bullets()  # your bullets
        frame.filter_opponent_bullets()  # their bullets vs you (lives, LIVES message)
        frame.reset_hit_time()
        frame.reset_opponent_hit_time()

        # ---- 6. draw
        frame.draw()

        # ---- 7. game over?
        if frame.player.lives == 0:
            return Result.LOST
        if frame.opponent.lives == 0:
            return Result.WIN

        # ---- 8. hold a steady frame rate
        wait = FRAME_MS - time.ticks_diff(time.ticks_ms(), frame.start)
        if wait > 0:
            time.sleep_ms(wait)


def run_single_player(pet):
    difficulty = Choice(None)

    def select_difficulty(_, __, label):
        difficulty.value = BOT_LEVELS.get(label)

    difficulty_menu = Menu(oled, OLED_W, OLED_H, ["easy", "normal", "hard", "back"], on_select=select_difficulty)
    difficulty_menu.display()
    scroll(difficulty_menu)

    if difficulty.value is not None:
        player = Player(*characters.CHARACTERS[pet.name])
        opponent = Bot(*characters.CHARACTERS["oblong"], **difficulty.value)

        return SinglePlayerFrame(player, opponent)

    return None


# While searching: your ID (so others can pick you), ". .. ..." and how to cancel
def search_text():
    me = "you: " + my_id()
    return tuple(((me, 16), (dots, 28), ("L: cancel", 40)) for dots in (".  ", ".. ", "..."))


# While waiting for the person you picked to pick you too
WAIT_TEXT = tuple(((w, 24), ("L: cancel", 40)) for w in ("waiting  ", "waiting. ", "waiting.."))
CONNECTED_TEXT = "connected"
CONNECTED_MS = 1000  # "connected" stays up this long before the game
AGAIN, BACK = "search again", "back"


def pick_from_list(lobby):
    """Menu of the keychains found: their pet's mini sprite + short ID, then
    "search again" and "back". R = next, L = prev, L + R = select.
    Returns a mac, AGAIN, or None (back, or the menu timed out)."""
    found = lobby.entries()
    labels = [short_id(mac) for mac, _ in found] + [AGAIN, BACK]
    icons = [characters.CHARACTERS[name] for _, name in found] + [None, None]
    chosen = Choice(None)

    def on_select(_, index, __):
        if index < len(found):
            chosen.value = found[index][0]
        elif index == len(found):
            chosen.value = AGAIN

    menu = Menu(oled, OLED_W, OLED_H, labels, on_select=on_select, icons=icons)
    scroll(menu, on_idle=lobby.poll)  # keep answering others while the list is up
    return chosen.value


def run_two_players(pet):
    player = Player(*characters.CHARACTERS[pet.name])
    lobby = Lobby(pet.name)
    cancel = lambda: left_btn.value() == 1

    while True:
        # 1. search: stars + your ID + ". .. ..."
        anim = loading_screen.Animation(text=search_text())
        try:
            found = lobby.search(cancel, anim.tick)
        finally:
            anim.close()
        if not found:
            break
        # 2. pick someone from the list
        choice = pick_from_list(lobby)
        wait_release()  # the L + R that selected isn't a cancel
        if choice is AGAIN:  # (not ==: choice may be a MAC, i.e. bytes)
            lobby.found.clear()
            continue
        if choice is None:
            break
        # 3. wait until they picked us too
        anim = loading_screen.Animation(text=WAIT_TEXT, intro=False)
        try:
            paired = lobby.wait_for(choice, cancel, anim.tick)
            if paired:
                anim.set_text(CONNECTED_TEXT)
                anim.hold(CONNECTED_MS)
        finally:
            anim.close()
        oled.fill(0)
        oled.show()
        if not paired:
            break
        opp_pet_name = characters.NAMES[lobby.found[choice]]
        opponent = Player(*characters.CHARACTERS[opp_pet_name], is_opp=True)
        drain()  # leftover HELLOs aren't game messages
        return TwoPlayerFrame(player, opponent, choice)

    log(INFO, "2-player: no opponent")
    oled.fill(0)
    oled.show()
    radio_off()
    return None


def run(pet):
    mode = Choice(None)

    def chosen(_, index, __):
        try:
            mode.value = [run_single_player, run_two_players][index]
        except IndexError:
            return

    player_menu = Menu(oled, OLED_W, OLED_H, ["1 Player", "2 Player", "Back"], on_select=chosen)
    player_menu.display()
    scroll(player_menu)

    if mode.value is not None:
        frame = mode.value(pet)

        if frame is None:  # "back" chosen, or no opponent found
            return

        while True:
            result = play_match(frame)
            log(INFO, "match result:", result)
            if result in (Result.IDLE, Result.OPP_IDLE, Result.LOST_LINK):
                break
            time.sleep_ms(1000)  # let the final hit sink in
            if not frame.rematch("YOU WIN" if result == Result.WIN else "YOU LOSE"):
                break

            frame.reset()  # fresh match, same opponent

        time.sleep_ms(1000)

    oled.fill(0)
    oled.show()
    radio_off()
