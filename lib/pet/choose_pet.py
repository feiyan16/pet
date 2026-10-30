from utils import btn_handler
from peripherals import *
from pet.pet_mode import PetMode
from roboface.roboface import *
from bitmaps.eyes import default, basic, bean, halfmoon, visor, pointed, scribble
from logger import log, log_exception, INFO


eye_options = [
    ("oblong", default, RoboFace),
    ("oval", basic, RoboFace),
    ("bean", bean, RoboFace),
    ("halfmoon", halfmoon, HalfMoonFace),
    ("visor", visor, CyclopsFace),
    ("pointed", pointed, RoboFace),
    ("scribble", scribble, RoboFace),    # left and right eyes drawn differently
]


def face_show(face):
    oled.text(">", OLED_W - 8, int(OLED_H / 2))
    oled.text("<", 0, int(OLED_H / 2))
    oled.show()


class ChoosePet:
    def __init__(self):
        self.idx = 0
        self.pet = None

    def set_pet(self):
        robo_eyes = eye_options[self.idx][1]
        robo_cls = eye_options[self.idx][2]
        self.pet = robo_cls(
            oled, OLED_W, OLED_H, robo_eyes.EYE, robo_eyes.EYE_W, robo_eyes.EYE_H,
            name=eye_options[self.idx][0], frame_rate=50, on_show=face_show,
            right_eye=getattr(robo_eyes, "RIGHT_EYE", None)   # only scribble has one; others mirror
        )

    def prev(self):
        self.idx -= 1
        if self.idx < 0:
            self.idx = len(eye_options) - 1

        self.set_pet()
        self.pet.update()

    def next_(self):
        self.idx += 1
        if self.idx >= len(eye_options):
            self.idx = 0

        self.set_pet()
        self.pet.update()

    def selected(self):
        log(INFO, "pet chosen:", self.pet.name)
        try:
            with open("pet.json", "w") as f:
                f.write(self.pet.to_json())
        except OSError as err:
            log_exception(err, "saving pet.json")

        self.pet.on_show = lambda p: oled.show()
        pet_mode = PetMode(self.pet)
        pet_mode.demo()

    def run(self):
        self.set_pet()

        l_prsd, r_prsd = False, False
        while True:
            l, r = left_btn.value(), right_btn.value()

            selected, l_prsd, r_prsd = btn_handler(l, r, l_prsd, r_prsd, l_action=self.prev, r_action=self.next_)

            if selected:
                self.selected()
                break

            self.pet.update()
