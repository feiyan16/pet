from machine import Pin, I2C
from ssd1306 import SSD1306_I2C

left_btn = Pin(3, Pin.IN)
right_btn = Pin(2, Pin.IN)

OLED_W = 128
OLED_H = 64

# Initialize I2C (adjust bus/pins to match your board; e.g., I2C(0) on Pico)
i2c = I2C(0, scl=Pin(7), sda=Pin(6), freq=400000)
# Initialize 128x64 OLED display
oled = SSD1306_I2C(OLED_W, OLED_H, i2c)
