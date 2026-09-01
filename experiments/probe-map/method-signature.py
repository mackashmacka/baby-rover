import serial, time
# One pin at a time, each with a UNIQUE edge count = an unforgeable signature.
# STBY only ever goes HIGH with PWM=0 and IN1=IN2=0 -> TB6612 "Stop". No rotation.
PROG = b"""
from machine import Pin
import time
stby=Pin(5,Pin.OUT); stby.value(0)
p2=Pin(2,Pin.OUT); p3=Pin(3,Pin.OUT); p4=Pin(4,Pin.OUT)
p2.value(0); p3.value(0); p4.value(0)
def burst(pin,n):
    for _ in range(n):
        pin.value(1); time.sleep_ms(5); pin.value(0); time.sleep_ms(5)
    time.sleep_ms(300)
burst(p2,5)      # GP2  -> 10 edges
burst(p3,10)     # GP3  -> 20 edges
burst(p4,20)     # GP4  -> 40 edges
burst(stby,40)   # GP5  -> 80 edges
print("DONE")
"""
s=serial.Serial('/dev/rover-pico',115200,timeout=2)
s.write(b'\x03'); time.sleep(0.3); s.read(s.in_waiting or 1)
s.write(b'\x05'); time.sleep(0.3); s.read(s.in_waiting or 1)
s.write(PROG); s.write(b'\x04'); time.sleep(4.0)
print(s.read(s.in_waiting or 1).decode(errors='replace').strip()[-40:]); s.close()
