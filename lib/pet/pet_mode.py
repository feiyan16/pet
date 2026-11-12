# pet_mode.py - the pet's everyday life, after a pet has been chosen
#
#   pet_mode = PetMode(face)     # face: a RoboFace (from pet.json)
#   pet_mode.wake()              # groggy wake-up animation
#   pet_mode.run()               # the main loop: never returns
#
# In run() the face idles (blinks, looks around, now and then picks a mood by
# itself). RIGHT opens the menu: Pat, Feed, Play, Visit, Message, Demo, Back.
# While idle the radio is on, so friends can visit or send messages. After
# IDLE_SLEEP_MS with no touch the pet falls asleep and the board deep-sleeps
# until a pad is touched.
#
import time  # ticks_ms, sleep_ms
import random  # pat goals, idle moods, spots on the screen
import machine  # deepsleep, lightsleep, reset
import esp32  # wake_on_gpio: wake from deep sleep when a pad is touched

from peripherals import oled, OLED_W, OLED_H, left_btn, right_btn
from roboface.roboface import *  # RoboFace, moods (HAPPY...), positions (N, E...), ON / OFF
from roboface.roboface import _FEED_STARS  # "_" names aren't included by import *
from ui.menu import Menu
from games import jump, shooter
from bitmaps import characters  # mini sprites and pet names / indexes
from utils import scroll, back, Choice, btn_handler, wait_release
from connect import radio_on, radio_off, drain
from pet import visit, message
from connect import ASK  # the "can I visit you?" message kind
from logger import log, log_exception, DEBUG, INFO, WARN

# --- pet mode settings -------------------------------------------------------
IDLE_SLEEP_MS = 45000  # no touch for this long in pet mode = get sleepy, then deep sleep
PAT_TOO_FAST_MS = 250  # two pats closer together than this count as "too fast"...
PAT_TOO_FAST_TAPS = 5  # ...and this many too-fast pats in a row make the pet angry
PAT_LOVE_MIN, PAT_LOVE_MAX = (5, 30)  # pats needed for LOVE: picked at random in this range
PAT_SPOTS = (N, NE, E, SE, S, SW, W, NW)  # where a pat can move the face (never the same twice)
LOVE_SHOW_MS = 2000  # how long the heart eyes stay
LOVE_BACK_MS = 600  # hearts turning back into happy eyes, before the laugh
LOVE_LAUGH_MS = 3000  # how long it laughs after enough pats
LAUGH_STEP_MS = 300  # how often the laugh is re-started (it lasts 500 ms)
LAUGH_BOUNCE_MS = 150  # time between up and down while laughing (lower = faster)
EXIT_BLANK_MS = 300  # blank screen when leaving pat mode, before the pet returns
# idle moods: while the face is idle in run(), the pet picks a mood by itself now and then
IDLE_MOODS = (HAPPY, CURIOUS, TIRED, LOVE)  # CURIOUS also looks to one side
IDLE_MOOD_GAP_MS = (20000, 40000)  # random wait between moods (min, max)
IDLE_MOOD_SHOW_MS = 3000  # how long a mood stays before going back to DEFAULT


# --- small helpers for steps that do more than one thing --------------------
def reset(f):
    """Back to a neutral, centred face with nothing running (mood, flicker, talking...)."""
    # back to a neutral face before each step
    f.set_mood(DEFAULT)
    f.set_curious(False)
    f.set_position(DEFAULT)
    f.horiz_flicker(False)
    f.vert_flicker(False)
    f.talk(False)
    f.wake()  # also opens the eyes


def curious(f):
    """CURIOUS mood, looking left."""
    f.set_mood(CURIOUS)
    f.set_position(W)  # CURIOUS shows when looking to a side


def curious_other_side(f):
    """CURIOUS mood, looking right."""
    f.set_mood(CURIOUS)
    f.set_position(E)


def happy_laugh(f):
    """HAPPY mood with a laugh (one laugh lasts 500 ms)."""
    f.set_mood(HAPPY)
    f.laugh()


def sad_cry(f):
    """SAD mood with tears."""
    f.set_mood(SAD)
    f.cry()


# --- the cycle: (milliseconds to show, name, what to do) --------------------
# Demo: every mood and animation, one after the other, with a RESET in between.
# Each step: (how long it shows in ms, name for the log, function that does it).
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
    """Turn a list of steps into a sequence on the face (pet.sequences) that plays
    them one after another. Returns the sequence; call .start() on it."""
    seq = pet.sequences.add("all expressions")  # a named, empty sequence
    t = 0  # when the next step starts (ms from the start)
    for ms, name, action in steps:

        # name=name, action=action: freeze this step's values now; otherwise
        # every step would see the loop's LAST values when it finally runs
        def step(pet, name=name, action=action):
            action(pet)

        seq.step(t, step)  # run step() at time t
        t += ms  # the next one starts after this one's time
    return seq


# (Not used: an example of a sequence that tours all 8 positions, kept for reference.)
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
    """Everything the pet does after it's been chosen. Menu items are the methods
    pat, feed, play, visit, message, demo."""

    def __init__(self, pet):
        self.pet = pet  # the face (a RoboFace)
        self.name = pet.name  # e.g. "bean": picks the mini sprite and the index sent over the radio

    # ------------------------------------------------------------------ helpers
    def _hold(self, ms, stop_on_touch=False):
        """Keep the face animating for ms milliseconds.
        With stop_on_touch=True, stop early and return True if a pad is touched."""
        end = time.ticks_add(time.ticks_ms(), ms)  # when to stop
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            self.pet.update()  # draws a new frame when it's time
            if stop_on_touch and (left_btn.value() or right_btn.value()):
                return True
        return False

    def _still(self):
        """Stop the automatic blinking/looking around, centre the face (for scripted scenes)."""
        self.pet.set_auto_blinker(OFF)  # no blinking by itself
        self.pet.set_idle_mode(OFF)  # no looking around by itself
        self.pet.set_position(DEFAULT)

    def _lively(self):
        """Back to the normal automatic blinking and looking around."""
        self.pet.set_auto_blinker(ON, 3, 2)  # blink every 3 s + a random 0-2 s
        self.pet.set_idle_mode(ON, 2, 2)  # look somewhere new every 2 s + a random 0-2 s

    # --------------------------------------------------------------------- wake
    def wake(self):
        """Groggy wake-up: asleep -> eyes open -> heavy, slow blinks (TIRED) -> HAPPY -> normal."""
        log(INFO, "waking up")
        f = self.pet  # short name
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
        goal = random.randint(PAT_LOVE_MIN, PAT_LOVE_MAX)  # a different number of pats each time
        log(DEBUG, "pat goal:", goal)
        pats = 0  # pats so far
        too_fast = 0  # too-fast gaps in a row
        last_pat = None  # when the last pat ended (None = no pat yet)
        spot = DEFAULT  # where the last pat moved the face

        tap = Choice(None)  # set by the pad callbacks: "leave" or "pat"

        def on_left():
            tap.value = "leave"

        def on_right():  # called when RIGHT is let go
            tap.value = "pat"

        wait_release()  # the press that chose "Pat" isn't a pat
        l_prsd, r_prsd = False, False  # pad states for btn_handler
        while True:
            f.update()  # draw the face (and run its animations)
            was_r = r_prsd  # was RIGHT down before this reading?
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
                spot = random.choice([d for d in PAT_SPOTS if d != spot])  # any spot except the last one
                f.set_mood(HAPPY)
                f.set_position(spot)
            if tap.value != "pat":
                continue  # RIGHT still held (or nothing happened): keep waiting
            tap.value = None  # RIGHT just let go: one pat done
            f.set_mood(DEFAULT)  # back to the middle, normal eyes
            f.set_position(DEFAULT)

            now = time.ticks_ms()
            if last_pat is not None and time.ticks_diff(now, last_pat) < PAT_TOO_FAST_MS:
                too_fast += 1  # another short gap
            else:
                too_fast = 0  # a calm pat resets the count
            last_pat = now  # for the next gap
            if too_fast >= PAT_TOO_FAST_TAPS - 1:  # N fast taps = N - 1 short gaps
                log(INFO, "pat: too fast, pet got angry")
                f.confuse()  # "what are you doing?"
                self._hold(700)
                f.set_mood(ANGRY)  # "stop that!"
                self._hold(1800)
                break  # patting ends

            pats += 1  # a good pat
            if pats >= goal:
                log(INFO, "pat: LOVE after", pats, "pats")
                f.set_mood(LOVE)  # heart eyes, in the middle...
                self._hold(LOVE_SHOW_MS)
                f.set_mood(HAPPY)  # ...turn back into happy eyes...
                self._hold(LOVE_BACK_MS)
                # ...then laugh and bounce.
                # laugh() moves the face up/down once per frame; at 50 fps that is
                # just a blur, so slow the frames down while it laughs
                normal_period = f._period  # ms between frames now (remembered to restore it)
                f._period = LAUGH_BOUNCE_MS  # one face frame every LAUGH_BOUNCE_MS
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
        """ "Feed": LEFT / RIGHT drop food from that side (feed() in roboface),
        until the pet's stomach is full. The stomach size is random each time."""
        direction = Choice(None)  # "left" or "right", set by a tap

        def go_left():
            direction.value = "left"

        def go_right():
            direction.value = "right"

        pet = self.pet.copy()  # a copy, so the feeding animation doesn't change the real face
        stomach_size = random.randrange(30, 103, 6)  # 30, 36, ... 102
        l_prsd, r_prsd = False, False
        while stomach_size > 0:
            l, r = left_btn.value(), right_btn.value()  # read each button once per pass

            _, l_prsd, r_prsd = btn_handler(l, r, l_prsd, r_prsd, l_action=go_left, r_action=go_right)

            if direction.value is not None:
                stomach_size -= _FEED_STARS  # each feed fills it a bit
                pet.feed(direction.value)
                direction.value = None  # one feed per tap

            pet.update()

    # ---------------------------------------------------------------------- play
    def play(self):
        """ "Play": choose a game (Jump / Shoot / Back) and play it."""
        game = Choice(None)  # the chosen game's run function

        def chosen(_, index, __):
            game.value = [jump.run, shooter.run, back][index]

        game_menu = Menu(oled, OLED_W, OLED_H, ["Jump", "Shoot", "Back"], on_select=chosen)
        scroll(game_menu)
        if game.value is not None:
            game.value(self)  # the game gets this PetMode (it uses .name / .mini())

    def visit(self):
        """ "Visit": search for a friend and ask to visit them (pet/visit.py)."""
        visit.visit(self)

    def message(self):
        """ "Message": pick a message and send it to a friend (pet/message.py)."""
        message.send(self)

    def demo(self):
        """ "Demo": play every expression once (ALL_EXPRESSIONS), on a copy of the face."""
        pet = self.pet.copy()  # a copy: the demo can't leave the real face in a strange state
        all_expressions = build_cycle(pet, ALL_EXPRESSIONS)
        all_expressions.start()  # start the sequence's clock
        while not pet.sequences.done:  # until every step has run
            pet.update()
        del pet  # free the copy's memory
        oled.fill(0)

    def mini(self):
        """This pet's mini sprite: (bytearray, w, h), used by the games."""
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
        f.set_mood(TIRED)  # heavy eyelids
        if self._hold(2000, stop_on_touch=True):
            return self._cancel_sleep()
        f.sleep()  # eyes close, Zzz
        if self._hold(3000, stop_on_touch=True):
            return self._cancel_sleep()
        self._deep_sleep()

    def _cancel_sleep(self):
        """A pad was touched while falling asleep: wake up again."""
        log(INFO, "sleep cancelled by a touch")
        self.pet.wake()
        reset(self.pet)
        self._lively()
        wait_release()  # the touch that woke it isn't a menu tap

    def _deep_sleep(self):
        """Deep sleep until either pad is touched. The screen isn't switched off here,
        so it keeps showing the sleeping face. Waking restarts the board: app.py runs
        again and the pet does its wake-up animation."""
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
            machine.lightsleep(200)  # sleep 200 ms with low power, then look again
        machine.reset()  # restart through app.py, as deep sleep would

    # ------------------------------------------------------------- idle moods
    def _next_mood_at(self):
        """A time IDLE_MOOD_GAP_MS from now (random), for the next idle mood."""
        return time.ticks_add(time.ticks_ms(), random.randint(*IDLE_MOOD_GAP_MS))

    def _idle_mood(self):
        """Pick one of IDLE_MOODS by itself. Returns when it should go back to DEFAULT."""
        mood = random.choice(IDLE_MOODS)  # one of HAPPY, CURIOUS, TIRED, LOVE
        log(DEBUG, "idle mood:", mood)
        self.pet.set_mood(mood)
        if mood == CURIOUS:  # CURIOUS shows when looking to a side
            self.pet.set_idle_mode(OFF)  # keep looking to that side (no wandering eyes)
            self.pet.set_position(random.choice((W, E)))
        return time.ticks_add(time.ticks_ms(), IDLE_MOOD_SHOW_MS)

    def _end_idle_mood(self):
        """Idle mood over: neutral face, normal blinking and looking around."""
        reset(self.pet)
        self._lively()

    # --------------------------------------------------------------------- run
    def run(self):
        """The pet's main loop: the face idles, RIGHT opens the menu, friends can
        visit / send messages, and after IDLE_SLEEP_MS with no touch it goes to sleep.
        Never returns."""
        action = Choice(None)  # the menu item's method, set when one is chosen

        def chosen(_, index, __):
            log(INFO, "menu choice:", index)
            action.value = [self.pat, self.feed, self.play, self.visit, self.message, self.demo, back][index]

        pet_menu = Menu(
            oled, OLED_W, OLED_H, ["Pat", "Feed", "Play", "Visit", "Message", "Demo", "Back"], on_select=chosen
        )
        name_idx = characters.NAMES.index(self.name)  # our pet's number, sent to friends who search

        self._lively()  # blink and look around by itself
        radio_on()  # the radio is on only while the face is idle here, so friends can visit

        l_prsd, r_prsd = False, False

        display = Choice(False)  # True = RIGHT was tapped: open the menu

        def display_menu():
            display.value = True

        last_touch = time.ticks_ms()  # for the 45 s sleep timer
        next_mood = self._next_mood_at()  # when the pet picks its next mood by itself
        mood_until = None  # while an idle mood shows: when it goes back to DEFAULT
        while True:
            self.pet.update()  # draw the face (blinks, looking around, mood)

            l, r = left_btn.value(), right_btn.value()
            if l or r:
                last_touch = time.ticks_ms()  # any touch counts as an interaction

            _, l_prsd, r_prsd = btn_handler(l, r, l_prsd, r_prsd, r_action=display_menu)

            # ---- idle moods
            now = time.ticks_ms()
            if mood_until is None and time.ticks_diff(now, next_mood) >= 0:
                mood_until = self._idle_mood()  # time for a mood
            elif mood_until is not None and time.ticks_diff(now, mood_until) >= 0:
                self._end_idle_mood()
                mood_until = None
                next_mood = self._next_mood_at()

            # ---- a friend asking to visit or sending a message (and answering friends who search)
            request = visit.listen(name_idx)  # never waits: None if nobody wants anything
            if request is not None:
                if mood_until is not None:
                    self._end_idle_mood()
                    mood_until = None
                kind, mac, value = request  # ASK (value = their pet) or MSG (value = message number)
                try:
                    if kind == ASK:
                        visit.host(self, mac, value)
                    else:
                        message.receive(mac, value)
                except Exception as err:
                    log_exception(err, "visit / message failed")
                self._lively()  # back to normal idling
                l_prsd, r_prsd = False, False
                last_touch = time.ticks_ms()
                next_mood = self._next_mood_at()

            if display.value:
                display.value = False  # handle this request once
                action.value = None  # forget the last choice
                if mood_until is not None:
                    self._end_idle_mood()
                    mood_until = None
                radio_off()  # busy: not visitable while the menu or an activity is up
                scroll(pet_menu)

                wait_release()  # let go of the pads first
                l_prsd, r_prsd = False, False  # the release isn't a new tap

                if action.value is not None:
                    try:
                        action.value()  # run the chosen menu item (returns when it's done)
                    except Exception as err:
                        log_exception(err, "menu action failed")
                radio_on()  # idle again: back to being visitable
                drain()  # messages left over from a game or visit
                last_touch = time.ticks_ms()  # the menu and its action were interactions
                next_mood = self._next_mood_at()

            if time.ticks_diff(time.ticks_ms(), last_touch) >= IDLE_SLEEP_MS:
                if mood_until is not None:
                    self._end_idle_mood()
                    mood_until = None
                radio_off()  # not visitable while falling asleep
                self.go_to_sleep()  # only returns if a pad was touched in time
                radio_on()
                l_prsd, r_prsd = False, False
                last_touch = time.ticks_ms()
                next_mood = self._next_mood_at()
