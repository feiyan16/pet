# logger.py - tiny logging for the keychain: console + a size-limited log file on flash
#
#   from logger import log, log_exception, DEBUG, INFO, WARN, ERROR
#   log(INFO, "pet loaded:", name)
#   try: ...
#   except Exception as err: log_exception(err, "while loading")
#
# The console (Thonny / a serial monitor) shows INFO and up; the file log.txt
# keeps LOG_FILE_LEVEL and up. LOG_FILE_LEVEL is DEBUG now, so everything goes
# to the file; set it to WARN for fewer flash writes. The file is capped at
# LOG_MAX_BYTES: when it fills up it becomes log.old.txt and a new one starts,
# so the newest ~16 KB of history (2 x 8 KB) is kept.

import os  # file sizes, rename, remove
import sys  # print_exception: prints a traceback
import time  # ticks_ms: milliseconds since the board started

# Levels: a bigger number = more serious. A line is kept if its level >= the limit.
DEBUG, INFO, WARN, ERROR = 10, 20, 30, 40
LOG_LEVEL = INFO  # console prints this level and up
LOG_FILE_LEVEL = DEBUG  # the file keeps this level and up
LOG_FILE = "log.txt"  # the current log, in the board's top folder
LOG_OLD = "log.old.txt"  # the previous log, once log.txt got full
LOG_MAX_BYTES = 8 * 1024  # log.txt is moved to log.old.txt once it's bigger than this

_NAMES = {DEBUG: "DEBUG", INFO: "INFO", WARN: "WARN", ERROR: "ERROR"}  # level -> text in the line


def log(level, *parts):
    """log(INFO, "anything", 42, ...): one line, stamped with ms since boot."""
    if level < LOG_LEVEL and level < LOG_FILE_LEVEL:
        return  # too unimportant for both the console and the file: skip the work
    # e.g. "   12345 INFO  pet loaded: bean"  (time right-aligned in 8, level left-aligned in 5)
    line = "%8d %-5s %s" % (time.ticks_ms(), _NAMES.get(level, level), " ".join(str(p) for p in parts))
    if level >= LOG_LEVEL:
        print(line)  # to the console
    if level >= LOG_FILE_LEVEL:
        _write(line + "\n")  # to log.txt


def log_exception(err, where=""):
    """An ERROR line plus the full traceback, to the console and the file."""
    log(ERROR, where, repr(err))  # repr: shows the error's type too, e.g. OSError(2,)
    sys.print_exception(err)  # traceback to the console
    _write(None, err)  # and to the file


def log_boot():
    """One line at start-up saying why the board started (power-on, deep sleep wake...).
    Returns machine.reset_cause() (a number, compare with machine.PWRON_RESET etc.),
    or None if it couldn't be read."""
    try:
        import machine  # imported here so the logger also works off the board (tests)

        cause_code = machine.reset_cause()  # why the chip (re)started, as a number
        causes = {  # number -> words for the log line
            machine.PWRON_RESET: "power on",
            machine.HARD_RESET: "hard reset",
            machine.WDT_RESET: "watchdog",
            machine.DEEPSLEEP_RESET: "deep sleep wake",
            machine.SOFT_RESET: "soft reset",
        }
        cause = causes.get(cause_code, cause_code)  # unknown numbers are logged as the number
    except (ImportError, AttributeError):  # no machine module, or a constant this port lacks
        cause = "unknown"
        cause_code = None
    log(WARN, "---- boot:", cause)  # WARN so every boot is also marked in the file
    return cause_code


def _write(text, err=None):
    """Append text (or err's traceback, if err is given) to log.txt,
    moving a full log.txt to log.old.txt first."""
    try:
        try:
            if os.stat(LOG_FILE)[6] > LOG_MAX_BYTES:  # [6] = file size
                try:
                    os.remove(LOG_OLD)  # the old-old log goes
                except OSError:
                    pass  # there wasn't one
                os.rename(LOG_FILE, LOG_OLD)  # the full log becomes the old one
        except OSError:
            pass  # no log file yet
        with open(LOG_FILE, "a") as f:  # "a" = append to the end (creates the file if needed)
            if err is None:
                f.write(text)
            else:
                sys.print_exception(err, f)  # the traceback, written into the file
    except OSError:
        pass  # never let logging crash the keychain (e.g. flash full)
