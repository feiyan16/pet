# choose_pet.py - the first-start screen where you pick your pet
#
#   ChoosePet().run()
#
# LEFT = previous pet, RIGHT = next pet, both pads = choose this one.
# The chosen face is saved to pet.json (so it comes back after a restart)
# and the pet does a short demo.

from utils import btn_handler
from peripherals import *  # oled, OLED_W, OLED_H, left_btn, right_btn
from pet.pet_mode import PetMode
from roboface.roboface import *  # RoboFace and its subclasses, moods, positions
from bitmaps.eyes import default, basic, bean, halfmoon, visor, pointed, scribble, sparkle
from logger import log, log_exception, INFO

# Every pet you can choose, in picker order: (name, eye bitmap module, face class).
# The name is saved in pet.json and matches bitmaps/characters.NAMES (mini sprites).
# Other modules (e.g. pet/visit.py) also look pets up in this list by name.
eye_options = [
    ("oblong", default, RoboFace),
    ("oval", basic, RoboFace),
    ("bean", bean, RoboFace),
    ("halfmoon", halfmoon, HalfMoonFace),  # its own class: the eyes change shape with some moods
    ("visor", visor, CyclopsFace),  # its own class: one wide eye
    ("pointed", pointed, RoboFace),
    ("scribble", scribble, RoboFace),  # left and right eyes drawn differently
    ("sparkle", sparkle, RoboFace),
]


def face_show(face):
    """The face's on_show callback while choosing: draw "<" and ">" on the sides
    (LEFT / RIGHT change pet), then send the picture to the screen."""
    oled.text(">", OLED_W - 8, int(OLED_H / 2))  # right edge, middle row
    oled.text("<", 0, int(OLED_H / 2))  # left edge, middle row
    oled.show()


class ChoosePet:
    def __init__(self):
        self.idx = 0  # which entry of eye_options is showing
        self.pet = None  # the face being shown (made by set_pet)

    def set_pet(self):
        """Build the face for eye_options[self.idx] (replacing the one before)."""
        robo_eyes = eye_options[self.idx][1]  # the eye bitmap module
        robo_cls = eye_options[self.idx][2]  # the face class
        self.pet = robo_cls(
            oled,  # draw on the screen
            OLED_W,
            OLED_H,  # face area = the whole screen
            robo_eyes.EYE,  # the eye bitmap bytes
            robo_eyes.EYE_W,
            robo_eyes.EYE_H,  # its size
            name=eye_options[self.idx][0],
            frame_rate=50,  # redraw up to 50 times a second
            on_show=face_show,  # adds the < > arrows each time it's drawn
            right_eye=getattr(robo_eyes, "RIGHT_EYE", None),  # only scribble has one; others mirror
        )

    def prev(self):
        """LEFT tap: the previous pet (wraps from the first to the last)."""
        self.idx -= 1
        if self.idx < 0:
            self.idx = len(eye_options) - 1

        self.set_pet()
        self.pet.update()  # draw it now

    def next_(self):
        """RIGHT tap: the next pet (wraps from the last to the first).
        (Called next_ because next is a Python built-in.)"""
        self.idx += 1
        if self.idx >= len(eye_options):
            self.idx = 0

        self.set_pet()
        self.pet.update()  # draw it now

    def selected(self):
        """Both pads: save this pet to pet.json, then show the pet mode demo."""
        log(INFO, "pet chosen:", self.pet.name)
        try:
            with open("pet.json", "w") as f:  # "w" = replace the file
                f.write(self.pet.to_json())  # the face's settings as text
        except OSError as err:
            log_exception(err, "saving pet.json")  # e.g. flash full: logged, demo still plays

        self.pet.on_show = lambda p: oled.show()  # no more < > arrows
        pet_mode = PetMode(self.pet)
        pet_mode.demo()

    def run(self):
        """The picker loop: returns once a pet has been chosen (and its demo played)."""
        self.set_pet()  # start with the first pet

        l_prsd, r_prsd = False, False  # pad states for btn_handler
        while True:
            l, r = left_btn.value(), right_btn.value()

            selected, l_prsd, r_prsd = btn_handler(l, r, l_prsd, r_prsd, l_action=self.prev, r_action=self.next_)

            if selected:
                self.selected()
                break

            self.pet.update()  # keep the face animating (blinks, looking around)
