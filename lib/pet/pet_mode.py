import time
import random
import machine
import esp32

from peripherals import oled, OLED_W, OLED_H, left_btn, right_btn
from roboface.roboface import *
from roboface.roboface import _FEED_STARS
from ui.menu import Menu
from games import jump, shooter
from bitmaps import characters
from utils import scroll, back, Choice, btn_handler, wait_release
from connect import radio_off
from logger import log, log_exception, DEBUG, INFO, WARN

# --- pet mode settings -------------------------------------------------------
IDLE_SLEEP_MS = 45000  # no touch for this long in pet mode = get sleepy, then deep sleep
PAT_TOO_FAST_MS = 250  # two pats closer together than this count as "too fast"...
PAT_TOO_FAST_TAPS = 5  # ...and this many too-fast pats in a row make the pet angry
PAT_LOVE_MIN, PAT_LOVE_MAX = 5, 30  # pats needed for LOVE: picked at random in this range
PAT_SPOTS = (N, NE, E, SE, S, SW, W, NW)  # where a pat can move the face (never the same twice)
LOVE_SHOW_MS = 2000  # how long the heart eyes stay
LOVE_BACK_MS = 600  # hearts turning back into happy eyes, before the laugh
LOVE_LAUGH_MS = 3000  # how long it laughs after enough pats
LAUGH_STEP_MS = 300  # how often the laugh is re-started (it lasts 500 ms)
LAUGH_BOUNCE_MS = 150  # time between up and down while laughing (lower = faster)
EXIT_BLANK_MS = 300  # blank screen when leaving pat mode, before the pet returns


# --- small helpers for steps that do more than one thing --------------------
def reset(f):
    # back to a neutral face before each step
    f.set_mood(DEFAULT)
    f.set_curious(False)
    f.set_position(DEFAULT)
    f.horiz_flicker(False)
    f.vert_flicker(False)
    f.talk(False)
    f.wake()  # also opens the eyes


def curious(f):
    f.set_mood(CURIOUS)
    f.set_position(W)  # CURIOUS shows when looking to a side


def curious_other_side(f):
    f.set_mood(CURIOUS)
    f.set_position(E)


def happy_laugh(f):
    f.set_mood(HAPPY)
    f.laugh()


def sad_cry(f):
    f.set_mood(SAD)
    f.cry()


# --- the cycle: (milliseconds to show, name, what to do) --------------------
ALL_EXPRESSIONS = (
    # moods
    (1500, "TIRED", lambda f: f.set_mood(TIRED)),
    (200, "RESET", reset),
    (2000, "ANGRY", lambda f: f.set_mood(ANGRY)),
    (200, "RESET", reset),
    (1500, "HAPPY", lambda f: f.set_mood(HAPPY)),
    (200, "RESET", reset),
    (1500, "FROZEN", lambda f: f.set_mood(FROZEN)),
    (200, "RESET", reset),
    (1500, "SCARY", lambda f: f.set_mood(SCARY)),
    (200, "RESET", reset),
    (500, "CURIOUS (left)", curious),
    (500, "CURIOUS (right)", curious_other_side),
    (200, "RESET", reset),
    (2000, "LOVE", lambda f: f.set_mood(LOVE)),
    (200, "RESET", reset),
    (1500, "SAD", lambda f: f.set_mood(SAD)),
    (200, "RESET", reset),
    (1500, "SURPRISED", lambda f: f.set_mood(SURPRISED)),
    (200, "RESET", reset),
    # animations
    (500, "blink", lambda f: f.blink()),
    (200, "RESET", reset),
    (500, "blink left only", lambda f: f.blink(left=True)),
    (500, "wink right", lambda f: f.wink(right=True)),
    (200, "RESET", reset),
    (200, "close", lambda f: f.close()),
    (200, "RESET", reset),
    (200, "open", lambda f: f.open()),
    (200, "RESET", reset),
    (500, "confuse", lambda f: f.confuse()),
    (200, "RESET", reset),
    (500, "laugh (HAPPY)", happy_laugh),
    (200, "RESET", reset),
    (1000, "cry (SAD)", sad_cry),
    (200, "RESET", reset),
    (1500, "talk", lambda f: f.talk(True)),
    (200, "RESET", reset),
    (1000, "horizontal flicker", lambda f: f.horiz_flicker(True, 4)),
    (200, "RESET", reset),
    (1000, "vertical flicker", lambda f: f.vert_flicker(True, 4)),
    (200, "RESET", reset),
    (2000, "sleep", lambda f: f.sleep()),
    (200, "RESET", reset),
    (500, "wake", lambda f: f.wake()),
    (200, "RESET", reset),
)


def build_cycle(pet, steps):
    seq = pet.sequences.add("all expressions")
    t = 0
    for ms, name, action in steps:

        def step(pet, name=name, action=action):
            action(pet)

        seq.step(t, step)
        t += ms
    return seq


# # visit all 8 positions, 400 ms each, then back to the centre. Built once
# # and restarted each cycle (building it inside the step would add a new
# # sequence every cycle and slowly use up memory).
# tour = face.sequences.add("positions")
# for i, p in enumerate((N, NE, E, SE, S, SW, W, NW, DEFAULT)):
#     tour.step(i * 400, (lambda q: lambda f: f.set_position(q))(p))
#
#
# def positions(f):
#     tour.start()


class PetMode:
    def __init__(self, pet):
        self.pet = pet
        self.name = pet.name

    # ------------------------------------------------------------------ helpers
    def _hold(self, ms, stop_on_touch=False):
        """Keep the face animating for ms milliseconds.
        With stop_on_touch=True, stop early and return True if a pad is touched."""
        end = time.ticks_add(time.ticks_ms(), ms)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            self.pet.update()
            if stop_on_touch and (left_btn.value() or right_btn.value()):
                return True
        return False

    def _still(self):
        """Stop the automatic blinking/looking around, centre the face (for scripted scenes)."""
        self.pet.set_auto_blinker(OFF)
        self.pet.set_idle_mode(OFF)
        self.pet.set_position(DEFAULT)

    def _lively(self):
        """Back to the normal automatic blinking and looking around."""
        self.pet.set_auto_blinker(ON, 3, 2)
        self.pet.set_idle_mode(ON, 2, 2)

    # --------------------------------------------------------------------- wake
    def wake(self):
        """Groggy wake-up: asleep -> eyes open -> heavy, slow blinks (TIRED) -> HAPPY -> normal."""
        log(INFO, "waking up")
        f = self.pet
        self._still()
        f.sleep()  # start asleep: eyes closed, Zzz
        self._hold(800)
        f.wake()  # eyes open...
        f.set_mood(TIRED)  # ...but the eyelids are still heavy
        self._hold(700)
        for gap in (800, 500, 1000):  # slow, uneven blinks: still waking up
            f.blink()
            self._hold(gap)
        f.set_mood(HAPPY)  # awake and glad to see you
        self._hold(1500)
        reset(f)
        self._lively()

    # ---------------------------------------------------------------------- pat
    def pat(self):
        """RIGHT pad = pat, LEFT pad = stop patting.
        While RIGHT is held: HAPPY eyes, and the face moves to a new spot.
        When RIGHT is let go: back to the middle with DEFAULT eyes (that is one pat).
        After enough pats: heart eyes, then HAPPY eyes, then a bouncing laugh.
        Patting too fast: confused, then ANGRY, and patting stops."""
        f = self.pet
        self._still()
        goal = random.randint(PAT_LOVE_MIN, PAT_LOVE_MAX)
        log(DEBUG, "pat goal:", goal)
        pats = 0
        too_fast = 0  # too-fast gaps in a row
        last_pat = None
        spot = DEFAULT  # where the last pat moved the face

        tap = Choice(None)

        def on_left():
            tap.value = "leave"

        def on_right():  # called when RIGHT is let go
            tap.value = "pat"

        wait_release()  # the press that chose "Pat" isn't a pat
        l_prsd, r_prsd = False, False
        while True:
            f.update()
            was_r = r_prsd
            both, l_prsd, r_prsd = btn_handler(
                left_btn.value(), right_btn.value(), l_prsd, r_prsd, l_action=on_left, r_action=on_right
            )
            if tap.value == "leave":
                log(INFO, "pat: stopped after", pats, "pats")
                break
            if both:  # both pads together isn't a pat:
                f.set_mood(DEFAULT)  # don't leave the face stuck off-centre
                f.set_position(DEFAULT)
                continue

            if r_prsd and not was_r:  # RIGHT just touched: happy, somewhere new
                spot = random.choice([d for d in PAT_SPOTS if d != spot])
                f.set_mood(HAPPY)
                f.set_position(spot)
            if tap.value != "pat":
                continue
            tap.value = None  # RIGHT just let go: one pat done
            f.set_mood(DEFAULT)  # back to the middle, normal eyes
            f.set_position(DEFAULT)

            now = time.ticks_ms()
            if last_pat is not None and time.ticks_diff(now, last_pat) < PAT_TOO_FAST_MS:
                too_fast += 1
            else:
                too_fast = 0
            last_pat = now
            if too_fast >= PAT_TOO_FAST_TAPS - 1:  # N fast taps = N - 1 short gaps
                log(INFO, "pat: too fast, pet got angry")
                f.confuse()  # "what are you doing?"
                self._hold(700)
                f.set_mood(ANGRY)  # "stop that!"
                self._hold(1800)
                break  # patting ends

            pats += 1
            if pats >= goal:
                log(INFO, "pat: LOVE after", pats, "pats")
                f.set_mood(LOVE)  # heart eyes, in the middle...
                self._hold(LOVE_SHOW_MS)
                f.set_mood(HAPPY)  # ...turn back into happy eyes...
                self._hold(LOVE_BACK_MS)
                # ...then laugh and bounce.
                # laugh() moves the face up/down once per frame; at 50 fps that is
                # just a blur, so slow the frames down while it laughs
                normal_period = f._period
                f._period = LAUGH_BOUNCE_MS
                try:
                    for _ in range(LOVE_LAUGH_MS // LAUGH_STEP_MS):
                        f.laugh()  # re-started so it keeps laughing
                        self._hold(LAUGH_STEP_MS)
                    self._hold(500)  # let the last laugh finish, still slow
                finally:
                    f._period = normal_period  # back to the normal frame rate
                self._hold(400)
                break  # patting ends after the laugh

        reset(f)
        oled.fill(0)  # leaving pat mode: blank for a moment...
        oled.show()
        time.sleep_ms(EXIT_BLANK_MS)
        self._lively()  # ...then the pet is back (next update draws it)
        wait_release()

    # ---------------------------------------------------------------------- feed
    def feed(self):
        direction = Choice(None)

        def go_left():
            direction.value = "left"

        def go_right():
            direction.value = "right"

        pet = self.pet.copy()
        stomach_size = random.randrange(30, 103, 6)
        l_prsd, r_prsd = False, False
        while stomach_size > 0:
            l, r = left_btn.value(), right_btn.value()  # read each button once per pass

            _, l_prsd, r_prsd = btn_handler(l, r, l_prsd, r_prsd, l_action=go_left, r_action=go_right)

            if direction.value is not None:
                stomach_size -= _FEED_STARS
                pet.feed(direction.value)
                direction.value = None  # one feed per tap

            pet.update()

    # ---------------------------------------------------------------------- play
    def play(self):
        game = Choice(None)

        def chosen(_, index, __):
            game.value = [jump.run, shooter.run, back][index]

        game_menu = Menu(oled, OLED_W, OLED_H, ["Jump", "Shoot", "Back"], on_select=chosen)
        scroll(game_menu)
        if game.value is not None:
            game.value(self)

    def demo(self):
        pet = self.pet.copy()
        all_expressions = build_cycle(pet, ALL_EXPRESSIONS)
        all_expressions.start()
        while not pet.sequences.done:
            pet.update()
        del pet
        oled.fill(0)

    def mini(self):
        return characters.CHARACTERS[self.name]

    # ---------------------------------------------------------------- sleeping
    def go_to_sleep(self):
        """Drowsy blinks -> TIRED -> asleep (Zzz) -> deep sleep until a pad is touched.
        Touching a pad before deep sleep starts cancels it and the pet wakes up again.
        """
        log(INFO, "45 s idle: getting sleepy")
        f = self.pet
        self._still()
        for gap in (1000, 800, 600):  # blinks coming closer together: getting sleepy
            f.blink()
            if self._hold(gap, stop_on_touch=True):
                return self._cancel_sleep()
        f.set_mood(TIRED)
        if self._hold(2000, stop_on_touch=True):
            return self._cancel_sleep()
        f.sleep()  # eyes close, Zzz
        if self._hold(3000, stop_on_touch=True):
            return self._cancel_sleep()
        self._deep_sleep()

    def _cancel_sleep(self):
        log(INFO, "sleep cancelled by a touch")
        self.pet.wake()
        reset(self.pet)
        self._lively()
        wait_release()  # the touch that woke it isn't a menu tap

    def _deep_sleep(self):
        """Screen off, then deep sleep until either pad is touched. Waking restarts the
        board: main.py runs again and the pet does its wake-up animation."""
        radio_off()  # in case a 2-player game left it on
        wait_release()  # a pad held now would wake it at once
        if hasattr(esp32, "wake_on_gpio"):  # MicroPython versions with GPIO deep-sleep wake
            level = getattr(esp32, "WAKEUP_ANY_HIGH", True)  # TTP223: HIGH while touched
            esp32.wake_on_gpio((left_btn, right_btn), level)
            log(WARN, "deep sleep until a pad is touched")
            machine.deepsleep()  # does not return
        # older firmware without wake_on_gpio: light sleep, checking the pads 5 times a second
        log(WARN, "no esp32.wake_on_gpio in this firmware: light-sleep fallback")
        while not (left_btn.value() or right_btn.value()):
            machine.lightsleep(200)
        machine.reset()  # restart through main.py, as deep sleep would

    # --------------------------------------------------------------------- run
    def run(self):
        action = Choice(None)

        def chosen(_, index, __):
            log(INFO, "menu choice:", index)
            action.value = [self.pat, self.feed, self.play, self.demo, back][index]

        pet_menu = Menu(oled, OLED_W, OLED_H, ["Pat", "Feed", "Play", "Demo", "Back"], on_select=chosen)

        self._lively()

        l_prsd, r_prsd = False, False

        display = Choice(False)

        def display_menu():
            display.value = True

        last_touch = time.ticks_ms()  # for the 45 s sleep timer
        while True:
            self.pet.update()

            l, r = left_btn.value(), right_btn.value()
            if l or r:
                last_touch = time.ticks_ms()  # any touch counts as an interaction

            _, l_prsd, r_prsd = btn_handler(l, r, l_prsd, r_prsd, r_action=display_menu)

            if display.value:
                display.value = False  # handle this request once
                action.value = None  # forget the last choice
                scroll(pet_menu)

                wait_release()  # let go of the pads first
                l_prsd, r_prsd = False, False  # the release isn't a new tap

                if action.value is not None:
                    try:
                        action.value()
                    except Exception as err:
                        log_exception(err, "menu action failed")
                last_touch = time.ticks_ms()  # the menu and its action were interactions

            if time.ticks_diff(time.ticks_ms(), last_touch) >= IDLE_SLEEP_MS:
                self.go_to_sleep()  # only returns if a pad was touched in time
                l_prsd, r_prsd = False, False
                last_touch = time.ticks_ms()
