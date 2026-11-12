# connect.py - the radio: short messages between keychains over ESP-NOW
#
# Used by games/shooter.py (2-player game), pet/visit.py (visiting) and
# pet/message.py (messages). ESP-NOW sends small packets straight from one
# ESP32 to another, with no Wi-Fi router.
#
# Every message is 4 bytes:  MAGIC (1 byte) | kind (1 byte) | value (2 bytes)
# e.g. pack(HELLO, 2) -> b"\xd1\x01\x02\x00".  parse() turns it back into (kind, value).
#
# The radio is off most of the time (saves battery): radio_on() / radio_off().

# "time" gives us millisecond timers (ticks_ms) and delays (sleep_ms).
import time

# "struct" packs numbers into raw bytes and unpacks them again; radio messages are just bytes.
import struct

# "network" controls the Wi-Fi hardware; ESP-NOW uses the Wi-Fi radio, so we need it switched on.
import network

# "espnow" is MicroPython's built-in ESP-NOW module: direct device-to-device radio messages, no router.
import espnow

from bitmaps import characters  # NAMES: pet index <-> pet name (sent in HELLO / HERE...)
from logger import log, INFO, WARN

CHANNEL = 6  # both keychains must use the same Wi-Fi channel
MAGIC = 0xD1  # first byte of every message: ignore other ESP-NOW devices
HELLO_EVERY_MS = 500  # how often to shout HELLO while searching

# ---- message kinds (the second byte). Never renumber: other keychains rely on them.
# 2-player game (games/shooter.py)
HELLO = 1  # searching: "is anyone there?"   value = my character index
READY = 2  # handshake reply                  value = my character index
POS = 3  # my centre x
FIRE = 4  # I fired from this centre x
LIVES = 5  # my lives left (sent 3 times)
PING = 6  # heartbeat, value unused
QUIT = 7  # I went idle / left the game
REMATCH = 8  # I chose rematch (1) or exit (0)
PICK = 9  # I picked you in my list    value = my character index
# visiting (pet/visit.py)
SCAN = 10  # "who can be visited?"            value unused
HERE = 11  # reply to SCAN: I'm idle          value = my character index
ASK = 12  # "can I visit you?"                value = my character index
SEEN = 13  # got your ASK, asking my person    value unused
YES = 14  # visit accepted                    value unused
NO = 15  # visit declined (or no answer)     value unused
END = 16  # the visit is over                 value unused
# messages (pet/message.py)
MSG = 17  # a message for you                value = message index
GOT = 18  # reply to MSG: got it             value unused

BCAST = b"\xff" * 6  # broadcast address: every ESP-NOW device in range


sta = network.WLAN(network.STA_IF)  # the Wi-Fi interface ("station" mode); ESP-NOW rides on it
e = espnow.ESPNow()  # created now so "from connect import e" gets the real object;
# it is only switched on by radio_on()


def radio_on():
    """Switch the radio on (2-player game, visits, messages, or the idle pet
    listening for friends). Safe to call more than once."""
    log(INFO, "radio on")
    sta.active(True)  # power up the Wi-Fi hardware
    sta.config(channel=CHANNEL)  # everyone on the same channel, or they can't hear each other
    e.active(True)  # start ESP-NOW
    try:
        e.add_peer(BCAST)  # ESP-NOW only sends to registered addresses: register "everyone"
    except OSError:  # already registered
        pass


def radio_off():
    """Switch the radio off again (saves battery)."""
    if e.active():
        log(INFO, "radio off")
        e.active(False)  # stop ESP-NOW (also throws away waiting messages)
        sta.active(False)  # power down the Wi-Fi hardware


def pack(kind, value=0):
    """4 bytes: magic, type, value (0-65535)."""
    # "<BBH" = little-endian: unsigned byte, unsigned byte, unsigned 2-byte number
    return struct.pack("<BBH", MAGIC, kind, value & 0xFFFF)  # & 0xFFFF keeps value in 0-65535


def parse(msg):
    """(kind, value) for one of our messages, or (None, 0) for anything else."""
    if msg is None or len(msg) != 4:  # nothing received, or not our 4-byte format
        return None, 0
    magic, kind, value = struct.unpack("<BBH", msg)
    if magic != MAGIC:  # some other ESP-NOW device's message
        return None, 0
    return kind, value


def safe_send(mac, data):
    """Send without crashing on radio errors. True if the other radio confirmed it
    (always True for broadcasts)."""
    try:
        return e.send(mac, data)
    except OSError as err:  # e.g. radio off, or the peer isn't registered
        log(WARN, "send failed:", err)
        return False


def drain():
    """Throw away any messages still waiting (e.g. left over from the last match)."""
    while e.any():  # any() = is a message waiting?
        e.recv(0)  # read it (0 = don't wait) and ignore it


def add_peer(mac):
    """Register mac so we can send to it directly (needed before e.send(mac, ...))."""
    try:
        e.add_peer(mac)
    except OSError:  # already registered
        pass


# ---- finding and pairing with another keychain (2-player game)
SEARCH_MIN_MS = 3000  # search at least this long, so everyone nearby is heard
SEARCH_MAX_MS = 15000  # give up if nobody at all answered by then
MAX_FOUND = 5  # most keychains listed (each menu entry costs ~1 KB of RAM)
PICK_EVERY_MS = 500  # while waiting, repeat "I picked you" this often
WAIT_MS = 20000  # give up waiting for the other person after this long


def short_id(mac):
    """The last 3 bytes of a MAC address, as shown on screen: "0D:15:3E"."""
    return "%02X:%02X:%02X" % (mac[3], mac[4], mac[5])  # %02X = 2-digit uppercase hex


def my_id():
    """This keychain's short ID (last 3 bytes of its own MAC address)."""
    return short_id(sta.config("mac"))


class Lobby:
    """Finds the keychains nearby, and pairs with one only when both people
    picked each other.

        lobby = Lobby("bean")
        lobby.search(cancel, on_wait)     # True if anyone answered
        lobby.entries()                   # [(mac, pet name), ...], nearest first
        lobby.wait_for(mac, cancel, on_wait)   # True once they picked us too
    Call poll() often (e.g. from a menu) so we keep answering and listening."""

    def __init__(self, name):
        self.name_idx = characters.NAMES.index(name)  # our pet's number, sent to others
        self.found = {}  # mac -> pet index they sent with HELLO
        self.picked_me = set()  # macs that sent PICK to us
        self.last_hello = None  # when we last shouted HELLO (None = never)

    def poll(self):
        """Say HELLO every HELLO_EVERY_MS and read every message waiting. Never blocks."""
        now = time.ticks_ms()
        if self.last_hello is None or time.ticks_diff(now, self.last_hello) >= HELLO_EVERY_MS:
            safe_send(BCAST, pack(HELLO, self.name_idx))  # to everyone in range
            self.last_hello = now
        while e.any():
            mac, msg = e.recv(0)  # mac = who sent it
            kind, value = parse(msg)
            if kind == HELLO or kind == PICK:  # another searcher
                if mac in self.found or len(self.found) < MAX_FOUND:  # keep the list small
                    self.found[mac] = value  # remember their pet
                if kind == PICK:
                    self.picked_me.add(mac)

    def search(self, cancel=None, on_wait=None):
        """Listen for SEARCH_MIN_MS (longer if nobody answered yet, up to SEARCH_MAX_MS).
        cancel()  : return True to stop early.  on_wait() : called about every 20 ms.
        Returns True if at least one keychain was found."""
        radio_on()
        start = time.ticks_ms()
        while True:
            self.poll()
            if on_wait is not None:
                on_wait()  # e.g. move the search animation
            if cancel is not None and cancel():
                log(INFO, "search cancelled")
                return False
            took = time.ticks_diff(time.ticks_ms(), start)  # ms since the search began
            if self.entries() and took >= SEARCH_MIN_MS:
                log(INFO, "found", len(self.entries()), "keychain(s)")
                return True
            if took >= SEARCH_MAX_MS:
                log(INFO, "no opponent found")
                return False
            time.sleep_ms(20)

    def entries(self):
        """[(mac, pet name), ...] for keychains whose pet this keychain knows,
        strongest signal (nearest) first."""
        # pet indexes we don't know (a keychain with newer code) are left out
        known = [(mac, characters.NAMES[i]) for mac, i in self.found.items() if i < len(characters.NAMES)]
        try:
            table = e.peers_table  # mac -> [rssi, time]
            # RSSI is negative (e.g. -50 is stronger than -80), so sort by -RSSI:
            # strongest first. Keychains with no RSSI yet get 0 and come first.
            known.sort(key=lambda m: -table[m[0]][0] if m[0] in table else 0)
        except AttributeError:  # no RSSI on this firmware
            pass
        return known

    def wait_for(self, mac, cancel=None, on_wait=None):
        """Tell mac we picked them (again every PICK_EVERY_MS) until they picked us too.
        Returns True when paired, False if cancelled or after WAIT_MS."""
        add_peer(mac)  # so we can send to them directly
        start = time.ticks_ms()
        last_pick = None
        while time.ticks_diff(time.ticks_ms(), start) < WAIT_MS:
            now = time.ticks_ms()
            if last_pick is None or time.ticks_diff(now, last_pick) >= PICK_EVERY_MS:
                safe_send(mac, pack(PICK, self.name_idx))  # "I picked you"
                last_pick = now
            self.poll()  # read their messages (and keep saying HELLO)
            if mac in self.picked_me:  # they picked us too: paired
                for _ in range(3):
                    safe_send(mac, pack(PICK, self.name_idx))  # make sure they hear ours
                log(INFO, "paired with", short_id(mac))
                return True
            if on_wait is not None:
                on_wait()
            if cancel is not None and cancel():
                log(INFO, "waiting cancelled")
                return False
            time.sleep_ms(20)
        log(INFO, "no answer from", short_id(mac))
        return False


def i_am_first(opp_mac):
    """True on exactly one of the two keychains (the one with the lower MAC).
    Used when both sides must agree who does something first."""
    return sta.config("mac") < opp_mac  # bytes compare byte by byte, so this is the same answer on both


def wait_for(opp_mac, kinds, timeout_ms):
    """Wait for a message of one of the given kinds from opp_mac.
    Returns (kind, value), or (None, 0) on timeout. Other messages are dropped."""
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
        mac, msg = e.recv(50)  # wait up to 50 ms for a message; (None, None) if none came
        kind, value = parse(msg)
        if mac == opp_mac and kind in kinds:
            return kind, value
    return None, 0
