# logger.py - tiny logging for the keychain: console + a size-limited log file on flash
#
#   from logger import log, log_exception, DEBUG, INFO, WARN, ERROR
#   log(INFO, "pet loaded:", name)
#   try: ...
#   except Exception as err: log_exception(err, "while loading")
#
# Only WARN and ERROR go to the file by default (fewer flash writes). The file is
# capped at LOG_MAX_BYTES: when it fills up it becomes log.old.txt and a new one starts,
# so the newest ~16 KB of history is kept.

import os
import sys
import time

DEBUG, INFO, WARN, ERROR = 10, 20, 30, 40
LOG_LEVEL = INFO  # console prints this level and up
LOG_FILE_LEVEL = DEBUG  # the file keeps this level and up
LOG_FILE = "log.txt"
LOG_OLD = "log.old.txt"
LOG_MAX_BYTES = 8 * 1024

_NAMES = {DEBUG: "DEBUG", INFO: "INFO", WARN: "WARN", ERROR: "ERROR"}


def log(level, *parts):
    """log(INFO, "anything", 42, ...): one line, stamped with ms since boot."""
    if level < LOG_LEVEL and level < LOG_FILE_LEVEL:
        return
    line = "%8d %-5s %s" % (time.ticks_ms(), _NAMES.get(level, level), " ".join(str(p) for p in parts))
    if level >= LOG_LEVEL:
        print(line)
    if level >= LOG_FILE_LEVEL:
        _write(line + "\n")


def log_exception(err, where=""):
    """An ERROR line plus the full traceback, to the console and the file."""
    log(ERROR, where, repr(err))
    sys.print_exception(err)
    _write(None, err)


def log_boot():
    """One line at start-up saying why the board started (power-on, deep sleep wake...)."""
    try:
        import machine

        cause_code = machine.reset_cause()
        causes = {
            machine.PWRON_RESET: "power on",
            machine.HARD_RESET: "hard reset",
            machine.WDT_RESET: "watchdog",
            machine.DEEPSLEEP_RESET: "deep sleep wake",
            machine.SOFT_RESET: "soft reset",
        }
        cause = causes.get(cause_code, cause_code)
    except (ImportError, AttributeError):
        cause = "unknown"
        cause_code = None
    log(WARN, "---- boot:", cause)  # WARN so every boot is also marked in the file
    return cause_code


def _write(text, err=None):
    try:
        try:
            if os.stat(LOG_FILE)[6] > LOG_MAX_BYTES:  # [6] = file size
                try:
                    os.remove(LOG_OLD)
                except OSError:
                    pass
                os.rename(LOG_FILE, LOG_OLD)
        except OSError:
            pass  # no log file yet
        with open(LOG_FILE, "a") as f:
            if err is None:
                f.write(text)
            else:
                sys.print_exception(err, f)
    except OSError:
        pass  # never let logging crash the keychain
