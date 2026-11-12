# peripherals.py - the hardware on the keychain, set up once and shared by every module
#
#   from peripherals import oled, OLED_W, OLED_H, left_btn, right_btn
#
# Board: Seeed XIAO ESP32-C3.
#   left touch pad  (TTP223) -> GPIO3 (D1)
#   right touch pad (TTP223) -> GPIO2 (D0)
#   OLED SSD1306 128 x 64    -> I2C: SDA GPIO6 (D4), SCL GPIO7 (D5)
# A TTP223 pad reads 1 while it's touched and 0 when it's not.

from machine import Pin, I2C  # Pin = a GPIO pin, I2C = the 2-wire bus the OLED talks on
from ssd1306 import SSD1306_I2C  # MicroPython's driver for this OLED chip

left_btn = Pin(3, Pin.IN)  # left pad: an input; left_btn.value() is 1 while touched
right_btn = Pin(2, Pin.IN)  # right pad: same, on GPIO2

OLED_W = 128  # screen width in pixels
OLED_H = 64  # screen height in pixels

# The I2C bus to the OLED: bus 0, clock on GPIO7, data on GPIO6.
# 400 kHz ("fast mode") sends a full screen (1 KB) in about 25 ms.
i2c = I2C(0, scl=Pin(7), sda=Pin(6), freq=400000)
# The screen itself. oled is also a framebuf.FrameBuffer: draw with oled.text(),
# oled.pixel(), oled.blit()..., then oled.show() sends the picture to the screen.
oled = SSD1306_I2C(OLED_W, OLED_H, i2c)
