import serial, time
# Fully passive: configure as INPUT with internal pull-up then pull-down and read back.
# Nothing attached -> follows the pull.  Something driving -> ignores it.
PROG = b"""
from machine import Pin
import time
Pin(5,Pin.OUT).value(0)
for gp in (12,13):
    u=Pin(gp,Pin.IN,Pin.PULL_UP); time.sleep_ms(50); a=u.value()
    d=Pin(gp,Pin.IN,Pin.PULL_DOWN); time.sleep_ms(50); b=d.value()
    print("GP%d pullup=%d pulldown=%d %s" % (gp,a,b,
        "FLOATING - nothing attached" if (a==1 and b==0) else
        "HELD LOW by something external" if (a==0 and b==0) else
        "HELD HIGH by something external" if (a==1 and b==1) else "odd"))
print("DONE")
"""
s=serial.Serial('/dev/rover-pico',115200,timeout=2)
s.write(b'\x03'); time.sleep(0.3); s.read(s.in_waiting or 1)
s.write(b'\x05'); time.sleep(0.3); s.read(s.in_waiting or 1)
s.write(PROG); s.write(b'\x04'); time.sleep(2.5)
out=s.read(s.in_waiting or 1).decode(errors='replace')
for l in out.splitlines():
    if l.startswith("GP") or l.startswith("DONE"): print(l)
s.close()
