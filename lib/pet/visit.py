# ---- visiting another keychain (used by pet/pet_mode.py)
#
# Visitor (picks "Visit" in the pet menu):
#   search -> list of keychains found -> pick one -> "asking.."
#     no reply in ASK_REPLY_MS          -> popup "<ID> is busy"
#     they chose "no" / didn't answer   -> popup "<ID> declined your visit"
#     they chose "yes"                  -> screen blank until the visit is over
# Host (only while its face is idle in PetMode.run(), the only time its radio is on):
#   popup "<ID> wants to visit you"  yes / no  (no answer in HOST_ANSWER_MS = no)
#     yes -> both pets side by side at half size, a short sequence, then back to normal
#
# Messages (4 bytes, see connect.py): SCAN, HERE, ASK, SEEN, YES, NO, END.

import time
import framebuf

from peripherals import oled, OLED_W, OLED_H, left_btn
from roboface.roboface import *
from connect import e, parse, pack, safe_send, add_peer, radio_on, drain, short_id, my_id, BCAST
from connect import SCAN, HERE, ASK, SEEN, YES, NO, END, MSG
from bitmaps import characters
from ui.popup import Popup
from ui import loading_screen
from utils import scroll, Choice, wait_release
from logger import log, INFO

SCAN_EVERY_MS = 500  # visitor: how often to shout SCAN while searching
SEARCH_MIN_MS = 3000  # search at least this long, so everyone nearby is heard
SEARCH_MAX_MS = 15000  # give up if nobody at all answered by then
MAX_FOUND = 5  # most keychains listed (each menu entry costs ~1 KB of RAM)
ASK_EVERY_MS = 500  # visitor: repeat ASK this often until the host answers
ASK_REPLY_MS = 3000  # no SEEN from the host in this long = "<ID> is busy"
HOST_ANSWER_MS = 5000  # host: no choice in this long = "no"
ANSWER_WAIT_MS = HOST_ANSWER_MS + 3000  # visitor: after SEEN, wait this long for YES / NO
VISIT_MAX_MS = 30000  # visitor: give up waiting for END after this long
REPEAT = 3  # replies (SEEN, YES, NO, END) are sent this many times, in case one is lost

HALF_W, HALF_H = OLED_W // 2, OLED_H // 2  # each pet's area during a visit: 64 x 32
HALF_Y = (OLED_H - HALF_H) // 2  # both halves sit in the middle rows


def _send_n(mac, kind, value=0):
    for _ in range(REPEAT):
        safe_send(mac, pack(kind, value))


# ============================================================== host side
def listen(name_idx):
    """Call often while the face is idle (radio on). Answers SCAN with HERE.
    Returns (kind, mac, value) when someone ASKs to visit (value = their pet index)
    or sends a MSG (value = message index, see pet/message.py), else None. Never blocks."""
    while e.any():
        mac, msg = e.recv(0)
        kind, value = parse(msg)
        if kind == SCAN:
            add_peer(mac)
            safe_send(mac, pack(HERE, name_idx))
        elif kind == ASK or kind == MSG:
            add_peer(mac)
            return kind, mac, value
    return None


def host(pet_mode, mac, their_idx):
    """Someone asked to visit: ask our person, then (if yes) play the visit."""
    who = short_id(mac)
    log(INFO, "visit request from", who)
    _send_n(mac, SEEN)  # "I heard you, asking my person now"
    answer = Choice(False)

    def chosen(_, index, __):
        answer.value = index == 0  # ["yes", "no"]

    popup = Popup(oled, who + " wants to visit you", ["yes", "no"], on_select=chosen)
    scroll(popup, timeout_ms=HOST_ANSWER_MS)  # timed out = answer stays False = "no"
    wait_release()
    if not answer.value or their_idx >= len(characters.NAMES):
        log(INFO, "visit declined")
        _send_n(mac, NO)
        drain()
        return
    log(INFO, "visit accepted")
    _send_n(mac, YES)
    try:
        host_sequence(pet_mode.pet.name, characters.NAMES[their_idx])
    finally:
        _send_n(mac, END)
        drain()  # ASKs / SCANs that came in during the visit
        oled.fill(0)
        oled.show()


# ---- half-size pets
def _half(eye, w, h):
    """The eye bitmap at half size (a pixel is on if any of its 4 source pixels is)."""
    src = framebuf.FrameBuffer(bytearray(eye), w, h, framebuf.MONO_HLSB)
    nw, nh = w // 2, h // 2
    out = bytearray(((nw + 7) // 8) * nh)
    dst = framebuf.FrameBuffer(out, nw, nh, framebuf.MONO_HLSB)
    for y in range(nh):
        for x in range(nw):
            if (
                src.pixel(2 * x, 2 * y)
                or src.pixel(2 * x + 1, 2 * y)
                or src.pixel(2 * x, 2 * y + 1)
                or src.pixel(2 * x + 1, 2 * y + 1)
            ):
                dst.pixel(x, y, 1)
    return out, nw, nh


def _half_face(name, fb):
    """A half-size face of pet `name`, drawn into fb (64 x 32)."""
    from pet.choose_pet import eye_options  # imported here: choose_pet imports pet_mode

    opt = eye_options[0]
    for o in eye_options:
        if o[0] == name:
            opt = o
            break
    pet_name, eyes, cls = opt
    eye, w, h = _half(eyes.EYE, eyes.EYE_W, eyes.EYE_H)
    right = getattr(eyes, "RIGHT_EYE", None)  # only scribble has one
    if right is not None:
        right = _half(right, eyes.EYE_W, eyes.EYE_H)[0]
    return cls(fb, HALF_W, HALF_H, eye, w, h, name=pet_name, right_eye=right, frame_rate=20, on_show=lambda f: None)


class TwoPets:
    """Our pet on the left, the visitor on the right, each 64 x 32."""

    def __init__(self, left_name, right_name):
        self.bufs = [bytearray(HALF_W * HALF_H // 8) for _ in range(2)]
        self.fbs = [framebuf.FrameBuffer(b, HALF_W, HALF_H, framebuf.MONO_VLSB) for b in self.bufs]
        self.faces = [_half_face(left_name, self.fbs[0]), _half_face(right_name, self.fbs[1])]
        for f in self.faces:
            f.set_auto_blinker(ON, 2, 1)

    def hold(self, ms):
        """Animate both faces for ms milliseconds."""
        end = time.ticks_add(time.ticks_ms(), ms)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            for f in self.faces:
                f.update()
            oled.fill(0)
            oled.blit(self.fbs[0], 0, HALF_Y)
            oled.blit(self.fbs[1], HALF_W, HALF_Y)
            oled.show()
            time.sleep_ms(20)

    def both(self, action):
        for f in self.faces:
            action(f)


def host_sequence(my_name, their_name):
    """PLACEHOLDER: what the two pets do together on the host's screen.
    Replace with the real sequence later."""
    pets = TwoPets(my_name, their_name)
    pets.hold(1200)  # both pets arrive
    pets.both(lambda f: f.set_mood(HAPPY))
    pets.hold(1500)
    pets.both(lambda f: f.set_mood(LOVE))
    pets.hold(1500)

    def laugh(f):
        f.set_mood(HAPPY)
        f.laugh()

    pets.both(laugh)
    pets.hold(1500)
    pets.both(lambda f: f.set_mood(DEFAULT))
    pets.hold(800)
    del pets


# ============================================================== visitor side
class Scan:
    """Finds the keychains nearby whose face is idle (they answer SCAN with HERE).
    Same shape as connect.Lobby (poll / entries / found), so the shooter's
    pick_from_list() menu works with it."""

    def __init__(self):
        self.found = {}  # mac -> their pet index
        self.last_scan = None

    def poll(self):
        now = time.ticks_ms()
        if self.last_scan is None or time.ticks_diff(now, self.last_scan) >= SCAN_EVERY_MS:
            safe_send(BCAST, pack(SCAN))
            self.last_scan = now
        while e.any():
            mac, msg = e.recv(0)
            kind, value = parse(msg)
            if kind == HERE and (mac in self.found or len(self.found) < MAX_FOUND):
                self.found[mac] = value

    def search(self, cancel, on_wait):
        """True if at least one keychain answered. LEFT (cancel) stops early."""
        radio_on()
        start = time.ticks_ms()
        while True:
            self.poll()
            on_wait()
            if cancel():
                log(INFO, "visit search cancelled")
                return False
            took = time.ticks_diff(time.ticks_ms(), start)
            if self.entries() and took >= SEARCH_MIN_MS:
                return True
            if took >= SEARCH_MAX_MS:
                log(INFO, "visit: nobody found")
                return False
            time.sleep_ms(20)

    def entries(self):
        """[(mac, pet name), ...], strongest signal (nearest) first."""
        known = [(mac, characters.NAMES[i]) for mac, i in self.found.items() if i < len(characters.NAMES)]
        try:
            table = e.peers_table  # mac -> [rssi, time]
            known.sort(key=lambda m: -table[m[0]][0] if m[0] in table else 0)
        except AttributeError:  # no RSSI on this firmware
            pass
        return known


def asking_text():
    me = my_id()
    return tuple(((me, 23), (w, 33)) for w in ("asking   ", "asking.  ", "asking.."))


def ask(mac, my_idx, on_wait):
    """Ask mac for a visit. Returns "yes", "no" or "busy"."""
    add_peer(mac)
    drain()
    start = time.ticks_ms()
    seen_at = None
    last_ask = None
    while True:
        now = time.ticks_ms()
        if seen_at is None:
            if time.ticks_diff(now, start) >= ASK_REPLY_MS:
                return "busy"  # their radio is off (busy) or they're out of range
            if last_ask is None or time.ticks_diff(now, last_ask) >= ASK_EVERY_MS:
                safe_send(mac, pack(ASK, my_idx))
                last_ask = now
        elif time.ticks_diff(now, seen_at) >= ANSWER_WAIT_MS:
            return "no"  # heard us, but no answer came back
        while e.any():
            sender, msg = e.recv(0)
            if sender != mac:
                continue
            kind, _ = parse(msg)
            if kind == SEEN and seen_at is None:
                seen_at = now
            elif kind == YES:
                return "yes"
            elif kind == NO:
                return "no"
        on_wait()
        time.sleep_ms(20)


def visitor_sequence(mac):
    """PLACEHOLDER: what the visitor's keychain does during the visit.
    For now its screen stays blank until the host says END (or VISIT_MAX_MS)."""
    oled.fill(0)
    oled.show()
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < VISIT_MAX_MS:
        while e.any():
            sender, msg = e.recv(0)
            if sender == mac and parse(msg)[0] == END:
                log(INFO, "visit over")
                return
        time.sleep_ms(50)
    log(INFO, "visit: no END from host, stopping")


def note(text):
    oled.fill(0)
    scroll(Popup(oled, text, ["ok"]))
    wait_release()


def find_friend():
    """Search for idle keychains and let the person pick one (used by Visit and Message).
    Returns their mac, or None (nobody found, cancelled with LEFT, "back" or timed out)."""
    from games.shooter import search_text, pick_from_list, AGAIN  # same search screen and list

    scan = Scan()
    cancel = lambda: left_btn.value() == 1
    while True:
        anim = loading_screen.Animation(text=search_text())
        try:
            found = scan.search(cancel, anim.tick)
        finally:
            anim.close()
        if not found:
            return None
        choice = pick_from_list(scan)
        wait_release()
        if choice is AGAIN:  # (not ==: choice may be a MAC, i.e. bytes)
            scan.found.clear()
            continue
        return choice


def visit(pet_mode):
    """The "Visit" menu item."""
    my_idx = characters.NAMES.index(pet_mode.name)
    choice = find_friend()
    if choice is not None:
        who = short_id(choice)
        anim = loading_screen.Animation(text=asking_text(), intro=False)
        try:
            answer = ask(choice, my_idx, anim.tick)
        finally:
            anim.close()
        log(INFO, "visit", who, "->", answer)
        if answer == "yes":
            visitor_sequence(choice)
        elif answer == "busy":
            note(who + " is busy")
        else:
            note(who + " declined your visit")
    oled.fill(0)
    oled.show()
    drain()
