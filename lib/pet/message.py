# ---- sending a message to another keychain (used by pet/pet_mode.py)
#
# Sender (picks "Message" in the pet menu):
#   search -> list of keychains found (pet + ID) -> pick one -> pick a message
#   -> envelope glides across with a shooting-star trail, "sending"
#     receiver said GOT     -> "sent!" for SENT_MS
#     no GOT in REPLY_MS    -> popup "<ID> is busy"
# Receiver (only while its face is idle in PetMode.run(), the only time its radio is on):
#   envelope glides in to the middle -> popup "<ID>:\n<message>" (no button; tap either pad to close)
#
# Messages (4 bytes, see connect.py): MSG (value = index into MESSAGES), GOT.

import time
import random
import framebuf

from peripherals import oled, OLED_W, OLED_H, left_btn, right_btn
from connect import e, parse, pack, safe_send, add_peer, drain, short_id, MSG, GOT
from bitmaps import mail, stars
from ui.menu import Menu
from ui.popup import Popup
from utils import scroll, Choice, centred, wait_release
from pet import visit
from logger import log, INFO

# ORDER MATTERS: the index is what's sent, so only ever ADD messages at the end
# (keep each under ~13 characters so it fits the menu)
MESSAGES = ("Hi!", "Miss you", "Play with me?", "Good night", "Thank you!", "<3")

SEND_EVERY_MS = 500  # sender: repeat MSG this often until GOT comes back
REPLY_MS = 3000  # no GOT in this long = "<ID> is busy"
SENT_MS = 1000  # how long "sent!" stays
ARRIVED_MS = 400  # receiver: envelope rests in the middle before the message pops up

# ---- the envelope animation
SPEED = 3  # px per frame (the envelope glides, it doesn't bob)
FRAME_MS = 33  # about 30 frames per second
ENV_Y = 24  # the envelope's middle row
SPARKS_EVERY = 3  # 2 new sparkles every 3rd frame, 1 on the others
SPARK_BEHIND = (4, 12)  # new sparkles start this many px behind the envelope
SPARK_SPREAD = (-6, 5)  # ...and this many px above / below its middle
SPARK_LIFE = (10, 18)  # frames a sparkle lasts
SPARK_DRIFT = 2  # px a sparkle drifts back each frame (stretches the tail)
TEXT_Y = 52

_env = framebuf.FrameBuffer(mail.ENVELOPE, mail.ENVELOPE_W, mail.ENVELOPE_H, framebuf.MONO_HLSB)
_star5 = framebuf.FrameBuffer(stars.STAR_5, stars.STAR_5_W, stars.STAR_5_H, framebuf.MONO_HLSB)
_star3 = framebuf.FrameBuffer(stars.STAR_3, stars.STAR_3_W, stars.STAR_3_H, framebuf.MONO_HLSB)


def _spark(age, life, x, y):
    """New sparkles are 5 x 5 stars, then 3 x 3, then a single dot: the tail tapers."""
    f = age * 100 // life
    if f < 35:
        oled.blit(_star5, x - 2, y - 2, 0)
    elif f < 70:
        oled.blit(_star3, x - 1, y - 1, 0)
    else:
        oled.pixel(x, y, 1)


def fly(text=None, stop_x=None, on_frame=None):
    """The envelope glides in from the left with a shooting-star trail.
    stop_x=None: it carries on off the right edge.  stop_x=n: it stops with its
    left edge at x=n.  text: shown under it.  on_frame(): called once per frame."""
    w, h = mail.ENVELOPE_W, mail.ENVELOPE_H
    end = OLED_W + 30 if stop_x is None else stop_x  # +30: let the tail leave the screen too
    sparks = []  # [x, y, age, life]
    x, frame = -w, 0
    while x < end or sparks:
        start = time.ticks_ms()
        if x < min(end, OLED_W):
            for _ in range(2 if frame % SPARKS_EVERY == 0 else 1):
                sparks.append(
                    [
                        x - random.randint(*SPARK_BEHIND),
                        ENV_Y + random.randint(*SPARK_SPREAD),
                        0,
                        random.randint(*SPARK_LIFE),
                    ]
                )
        oled.fill(0)
        for s in sparks:
            _spark(s[2], s[3], s[0], s[1])
        if x < OLED_W:
            oled.blit(_env, x, ENV_Y - h // 2, 0)
        if text:
            centred(text, TEXT_Y)
        oled.show()
        for s in sparks:
            s[2] += 1
            s[0] -= SPARK_DRIFT
        sparks = [s for s in sparks if s[2] < s[3]]
        if x < end:
            x = min(end, x + SPEED)
        frame += 1
        if on_frame is not None:
            on_frame()
        wait = FRAME_MS - time.ticks_diff(time.ticks_ms(), start)
        if wait > 0:
            time.sleep_ms(wait)
    if stop_x is not None:  # rest in the middle, trail gone
        oled.fill(0)
        oled.blit(_env, x, ENV_Y - h // 2, 0)
        oled.show()


# ============================================================== receiver side
def receive(mac, index):
    """Someone sent us a message (from PetMode.run(), via visit.listen())."""
    for _ in range(3):
        safe_send(mac, pack(GOT))  # straight away, so the sender knows it arrived
    who = short_id(mac)
    text = MESSAGES[index] if index < len(MESSAGES) else "?"
    log(INFO, "message from", who, ":", text)
    fly(stop_x=(OLED_W - mail.ENVELOPE_W) // 2)
    time.sleep_ms(ARRIVED_MS)
    Popup(oled, who + ":\n" + text, []).display()  # no button: it stays until a pad is tapped
    wait_release()  # a finger already down doesn't count
    while not (left_btn.value() or right_btn.value()):
        time.sleep_ms(20)
    wait_release()  # the tap that closed it isn't a menu tap
    drain()  # the sender's repeats of the same MSG
    oled.fill(0)
    oled.show()


# ============================================================== sender side
def pick_message():
    """Menu of MESSAGES + "back". Returns an index, or None."""
    chosen = Choice(None)

    def on_select(_, index, __):
        if index < len(MESSAGES):
            chosen.value = index

    scroll(Menu(oled, OLED_W, OLED_H, list(MESSAGES) + ["back"], on_select=on_select))
    wait_release()
    return chosen.value


def deliver(mac, index):
    """Send MESSAGES[index] to mac while the envelope flies. True once they said GOT."""
    add_peer(mac)
    drain()
    start = time.ticks_ms()
    state = {"got": False, "last": None}

    def poll():
        now = time.ticks_ms()
        if not state["got"] and (state["last"] is None or time.ticks_diff(now, state["last"]) >= SEND_EVERY_MS):
            safe_send(mac, pack(MSG, index))
            state["last"] = now
        while e.any():
            sender, msg = e.recv(0)
            if sender == mac and parse(msg)[0] == GOT:
                state["got"] = True

    fly(text="sending", on_frame=poll)
    while not state["got"] and time.ticks_diff(time.ticks_ms(), start) < REPLY_MS:
        poll()  # the flight was shorter than REPLY_MS: keep waiting a little
        time.sleep_ms(20)
    return state["got"]


def send(pet_mode):
    """The "Message" menu item."""
    mac = visit.find_friend()
    if mac is not None:
        index = pick_message()
        if index is not None:
            who = short_id(mac)
            if deliver(mac, index):
                log(INFO, "message sent to", who)
                oled.fill(0)
                centred("sent!", (OLED_H - 8) // 2)
                oled.show()
                time.sleep_ms(SENT_MS)
            else:
                log(INFO, "message: no reply from", who)
                visit.note(who + " is busy")
    oled.fill(0)
    oled.show()
    drain()
