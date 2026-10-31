# main.py - start-up: Wi-Fi driver first, then either the pet picker (first start)
#           or the saved pet (every other start, including waking from deep sleep)
import gc
import connect  # Wi-Fi driver first, while system memory is free

gc.collect()

import errno
import json
import machine
import time
from peripherals import oled, OLED_H
from utils import centred, wait_release
from ui import loading_screen  # small: its frames are read from flash only while it plays

gc.collect()
from logger import log, log_exception, INFO, WARN, log_boot

cause = log_boot()


HELLO_TEXT = "hello!"
TYPED_HELLO = loading_screen.typed("hello!", 350)  # typed during the star intro, 1 letter / 350 ms,
HELLO_PAUSE_MS = 3000  # then the whole word stays up, stars twinkling, before loading
# Same width (trailing space) so the centred text doesn't shift as the dots change
LOADING_TEXT = ("loading. ", "loading..")

# Shown before the pet picker. Max 16 characters per line (8 px per character).
INSTRUCTIONS = ("Choose your pet", "L + R: select")
INSTRUCTIONS_MS = 3000


def show_instructions(lines=INSTRUCTIONS, ms=INSTRUCTIONS_MS):
    """Centred lines, 12 px apart, in the middle of the screen, for ms milliseconds."""
    y = (OLED_H - (len(lines) * 12 - 4)) // 2
    for line in lines:
        centred(line, y)
        y += 12
    oled.show()
    time.sleep_ms(ms)
    oled.fill(0)
    oled.show()


def read_pet_json():
    """The text of pet.json, or None if there is no pet yet."""
    try:
        with open("pet.json") as f:
            return f.read()
    except OSError as err:
        if err.args[0] == errno.ENOENT:  # no file yet: first start
            log(INFO, "no pet.json: first start")
            return None
        raise


def face_from_json(text):
    """The saved face, or None if pet.json is damaged or outdated."""
    from roboface.roboface import RoboFace, HalfMoonFace, CyclopsFace  # the .mpy: no compiling

    classes = {"RoboFace": RoboFace, "HalfMoonFace": HalfMoonFace, "CyclopsFace": CyclopsFace}
    try:
        cls = classes[json.loads(text)["class"]]
        return cls.from_json(text, oled, on_show=lambda face: oled.show())
    except (ValueError, KeyError) as err:  # damaged or outdated file: choose again
        log(WARN, "pet.json unusable:", repr(err))
        return None


# -----------------------------------------------------------------------------
# LOADING SCREEN FOR THE FIRST START (no pet yet)
#
# HOW IT WORKS
#   loading_screen.run(first_start_loading, ...) plays the "hello!" star intro, a
#   pause, then the "loading.." text while first_start_loading runs.
#   first_start_loading is a GENERATOR: each `yield` hands control back to the
#   loading screen so the stars and dots can move on. During an import itself
#   the screen holds still (MicroPython can only do one thing at a time), so put
#   a `yield` after EVERY slow step.
#
# WHAT TO PUT HERE
#   Everything ChoosePet needs that isn't loaded yet: roboface, the menus, the
#   games, pet_mode and choose_pet itself. MicroPython remembers every import
#   (in sys.modules), so after run() finishes, the `from pet.choose_pet import ...`
#   line below is instant.
#
# TO ADD ANOTHER SLOW STEP (another import, reading a file...):
#   write it on its own line in here, then `gc.collect()` and `yield` after it.
#
# ORDER: biggest first (roboface), while memory is still in one piece.
# Never move `import connect` in here: the Wi-Fi driver must start before all of
# this, at the very top of main.py.
# -----------------------------------------------------------------------------
def first_start_loading():
    import roboface.roboface  # the face library (.mpy)

    gc.collect()
    yield
    import ui.menu

    gc.collect()
    yield
    import ui.popup

    gc.collect()
    yield
    import games.jump

    gc.collect()
    yield
    import games.shooter

    gc.collect()
    yield
    import pet.pet_mode

    gc.collect()
    yield
    import pet.choose_pet

    gc.collect()
    yield


# -----------------------------------------------------------------------------
# LOADING FOR EVERY OTHER START (a pet exists)
# The same idea, but instead of the stars, the pet is shown ASLEEP while pet
# mode loads. Each `yield` lets its Zzz animate. Then it wakes up for real.
# -----------------------------------------------------------------------------
def pet_mode_loading():
    import ui.menu

    gc.collect()
    yield
    import ui.popup

    gc.collect()
    yield
    import games.jump

    gc.collect()
    yield
    import games.shooter

    gc.collect()
    yield
    import pet.pet_mode

    gc.collect()
    yield


pet_text = read_pet_json()
pet_face = face_from_json(pet_text) if pet_text is not None else None
gc.collect()


if pet_face is None:
    oled.poweron()  # in case deep sleep left it off
    # 1. "hello!" typed over the star intro, a 3 s pause, then load everything behind the stars
    #    (see the notes above). on_error="raise": a failed import shows
    #    "loading failed", prints the traceback, and stops here instead of
    #    carrying on half-loaded.
    try:
        loading_screen.run(
            first_start_loading, text=LOADING_TEXT, intro_text=TYPED_HELLO, pause_ms=HELLO_PAUSE_MS, on_error="raise"
        )
    except Exception as err:
        log_exception(err, "first-start loading")
        raise
    # 2. Everything is loaded now: this import is instant.
    from pet.choose_pet import ChoosePet

    gc.collect()
    # 3. How to choose, for 5s; then choose the pet, then restart into pet mode.
    show_instructions()
    wait_release()  # a finger still down isn't a choice
    ChoosePet().run()
    log(INFO, "pet chosen, restarting into pet mode")
    oled.poweroff()
    machine.deepsleep(3000)  # restart into pet mode after 3 s
else:
    if cause == machine.PWRON_RESET:
        loading_screen.run(text=HELLO_TEXT, intro_text=TYPED_HELLO, pause_ms=HELLO_PAUSE_MS)

    log(INFO, "pet loaded:", pet_face.name)
    pet_face.sleep()  # shown asleep while pet mode loads
    for _ in pet_mode_loading():
        pet_face.update()  # the Zzz moves on between imports
    from pet.pet_mode import PetMode  # already loaded: instant

    gc.collect()
    app = PetMode(pet_face)
    try:
        app.wake()  # groggy wake-up animation
        app.run()
    except Exception as err:
        log_exception(err, "pet mode crashed")
        raise
