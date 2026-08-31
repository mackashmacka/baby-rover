# Power supply

**The rule, and it generalises well beyond this project: judge a supply by its
sag under load, not its open-circuit voltage.** Open circuit tells you what a
source *is*; loaded tells you what it can *do*.

> ⚠️ **Revised 2026-08-31.** This page used to recommend a **6×AA pack**. That
> was written when the motor was believed to be 12 V. **It is 6 V** — see
> [[n20-motors]] — and 6×AA is now the *wrong part*. Corrected below.

## The 9 V 6F22 — measured, and still unusable

- 8.2 V open circuit
- 7.7 V under only ~50 mA
- ⇒ **~10 Ω internal resistance**

Now quantifiable against the real motors: four motors at 100 mA each = 400 mA,
which into ~10 Ω is a **4 V drop ⇒ 4.2 V delivered** — already below the motor's
4.5 V minimum before it does any useful work. At stall it collapses completely.

**The chemistry is the problem, not the charge.** It is carbon-zinc; another
one behaves identically.

## The bench supply — corrected

The motor window is **4.5–6 V**.

| Option | Voltage | Verdict |
|---|---|---|
| **Lab PSU at 6.0 V** | 6.0 | ✅ **Best** — current limiting turns a wiring mistake into a beep instead of smoke |
| 4× AA alkaline | 6.0 | ✅ Fine |
| 5× AA NiMH | 6.0 | ✅ Fine |
| ~~6× AA alkaline~~ | ~~9.0~~ | 🔴 **150% of rated. Would cook the motors** |
| ~~6× AA NiMH~~ | ~~7.2~~ | 🔴 120% of rated. Still over |

## The rover chain

`4S LiPo (16.8 V) → Pololu D24V90F5 → 5 V, 9 A rail`

**16.8 V exceeds the TB6612's 13.5 V `VM` maximum.** The regulator is not
optional, and the LiPo must never reach the driver directly.

**5 V `VM` is a legitimate operating point**, not a compromise — it sits inside
the 4.5–6 V window with margin at both ends. (The H-bridge drops ~0.5 V, so the
motor sees ~4.5–5 V under load.)

## The counter-intuitive part

The **Pi 5 and D415 draw several amps** — many times the entire motor
subsystem, which is under 1 A even stalled. The 9 A regulator is sized for the
computer, not the drivetrain. Size the battery and the wiring around that.

Related: [[n20-motors]], [[debugging-method]]
