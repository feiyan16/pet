# main.py - MicroPython runs this file automatically every time the keychain starts
# (power on, reset button, or waking from deep sleep).
#
# It stays tiny on purpose: all the start-up work lives in lib/app.py, so it can
# be compiled to .mpy or frozen into the firmware. main.py itself must stay a .py
# file, because MicroPython only looks for "main.py" by that exact name.

import connect  # set up the Wi-Fi radio driver first (ESP-NOW needs it), before anything else uses RAM
import app  # importing app runs the whole start-up: loading screen, pet choice or pet mode
