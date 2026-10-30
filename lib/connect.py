# ---- used by games/shooter.py or any two player games

# "time" gives us millisecond timers (ticks_ms) and delays (sleep_ms).
import time
# "struct" packs numbers into raw bytes and unpacks them again; radio messages are just bytes.
import struct
# "network" controls the Wi-Fi hardware; ESP-NOW uses the Wi-Fi radio, so we need it switched on.
import network
# "espnow" is MicroPython's built-in ESP-NOW module: direct device-to-device radio messages, no router.
import espnow

from bitmaps import characters
from logger import log, INFO, WARN


CHANNEL = 6                 # both keychains must use the same Wi-Fi channel
MAGIC = 0xD1                # first byte of every message: ignore other ESP-NOW devices
HELLO_EVERY_MS = 500        # how often to shout HELLO while searching


HELLO = 1                   # searching: "is anyone there?"   value = my character index
READY = 2                   # handshake reply                  value = my character index
POS = 3                     # my centre x
FIRE = 4                    # I fired from this centre x
LIVES = 5                   # my lives left (sent 3 times)
PING = 6                    # heartbeat, value unused
QUIT = 7                    # I went idle / left the game
REMATCH = 8                 # I chose rematch (1) or exit (0)
PICK = 9                    # I picked you in my list    value = my character index

BCAST = b"\xff" * 6         # broadcast address: every ESP-NOW device in range


sta = network.WLAN(network.STA_IF)
e = espnow.ESPNow()  # created now so "from connect import e" gets the real object;
                    # it is only switched on by radio_on()


def radio_on():
    """Switch the radio on for a 2-player game. Safe to call more than once."""
    log(INFO, "radio on")
    sta.active(True)
    sta.config(channel=CHANNEL)
    e.active(True)
    try:
        e.add_peer(BCAST)
    except OSError:          # already registered
        pass


def radio_off():
    """Switch the radio off again (saves battery). Call when the 2-player game ends."""
    if e.active():
        log(INFO, "radio off")
        e.active(False)
        sta.active(False)


def pack(kind, value=0):
    """4 bytes: magic, type, value (0-65535)."""
    return struct.pack("<BBH", MAGIC, kind, value & 0xFFFF)


def parse(msg):
    """(kind, value) for one of our messages, or (None, 0) for anything else."""
    if msg is None or len(msg) != 4:
        return None, 0
    magic, kind, value = struct.unpack("<BBH", msg)
    if magic != MAGIC:
        return None, 0
    return kind, value


def safe_send(mac, data):
    """Send without crashing on radio errors. True if the other radio confirmed it
    (always True for broadcasts)."""
    try:
        return e.send(mac, data)
    except OSError as err:
        log(WARN, "send failed:", err)
        return False


def drain():
    """Throw away any messages still waiting (e.g. left over from the last match)."""
    while e.any():
        e.recv(0)


def add_peer(mac):
    try:
        e.add_peer(mac)
    except OSError:          # already registered
        pass


SEARCH_MIN_MS = 3000        # search at least this long, so everyone nearby is heard
SEARCH_MAX_MS = 15000       # give up if nobody at all answered by then
MAX_FOUND = 5               # most keychains listed (each menu entry costs ~1 KB of RAM)
PICK_EVERY_MS = 500         # while waiting, repeat "I picked you" this often
WAIT_MS = 20000             # give up waiting for the other person after this long


def short_id(mac):
    """The last 3 bytes of a MAC address, as shown on screen: "0D:15:3E"."""
    return "%02X:%02X:%02X" % (mac[3], mac[4], mac[5])


def my_id():
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
        self.name_idx = characters.NAMES.index(name)
        self.found = {}             # mac -> pet index they sent with HELLO
        self.picked_me = set()      # macs that sent PICK to us
        self.last_hello = None

    def poll(self):
        """Say HELLO every HELLO_EVERY_MS and read every message waiting. Never blocks."""
        now = time.ticks_ms()
        if self.last_hello is None or time.ticks_diff(now, self.last_hello) >= HELLO_EVERY_MS:
            safe_send(BCAST, pack(HELLO, self.name_idx))
            self.last_hello = now
        while e.any():
            mac, msg = e.recv(0)
            kind, value = parse(msg)
            if kind == HELLO or kind == PICK:
                if mac in self.found or len(self.found) < MAX_FOUND:
                    self.found[mac] = value
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
                on_wait()
            if cancel is not None and cancel():
                log(INFO, "search cancelled")
                return False
            took = time.ticks_diff(time.ticks_ms(), start)
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
        known = [(mac, characters.NAMES[i]) for mac, i in self.found.items()
                 if i < len(characters.NAMES)]
        try:
            table = e.peers_table                     # mac -> [rssi, time]
            known.sort(key=lambda m: -table[m[0]][0] if m[0] in table else 0)
        except AttributeError:                        # no RSSI on this firmware
            pass
        return known

    def wait_for(self, mac, cancel=None, on_wait=None):
        """Tell mac we picked them (again every PICK_EVERY_MS) until they picked us too.
        Returns True when paired, False if cancelled or after WAIT_MS."""
        add_peer(mac)
        start = time.ticks_ms()
        last_pick = None
        while time.ticks_diff(time.ticks_ms(), start) < WAIT_MS:
            now = time.ticks_ms()
            if last_pick is None or time.ticks_diff(now, last_pick) >= PICK_EVERY_MS:
                safe_send(mac, pack(PICK, self.name_idx))
                last_pick = now
            self.poll()
            if mac in self.picked_me:                 # they picked us too: paired
                for _ in range(3):
                    safe_send(mac, pack(PICK, self.name_idx))   # make sure they hear ours
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
    """True on exactly one of the two keychains (the one with the lower MAC)."""
    return sta.config("mac") < opp_mac


def wait_for(opp_mac, kinds, timeout_ms):
    """Wait for a message of one of the given kinds from opp_mac.
    Returns (kind, value), or (None, 0) on timeout. Other messages are dropped."""
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
        mac, msg = e.recv(50)
        kind, value = parse(msg)
        if mac == opp_mac and kind in kinds:
            return kind, value
    return None, 0
