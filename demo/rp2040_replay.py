# SPDX-License-Identifier: Apache-2.0
"""MicroPython replay demo for the Tiny Tapeout demo board (RP2040).

UNTESTED ON HARDWARE. It is written against the Tiny Tapeout MicroPython SDK
(`ttboard`). Check the attribute names against the SDK version on your board;
they are kept in the small `Pins` adapter below so only that class needs changing.

Setup:
    mpremote cp traces/clean.bin :clean.bin
    mpremote cp traces/attack.bin :attack.bin
    mpremote cp demo/rp2040_replay.py :rp2040_replay.py
    mpremote exec "import rp2040_replay; rp2040_replay.main()"

Demo:
    1. replay clean.bin   -> no fault
    2. CLEAR_FLAGS
    3. replay attack.bin  -> fault LED (uio[3]) lights
    4. read last_bad_pc (views 5/6); it should equal win() (0x006a in attack.json)
"""

import time

from ttboard.demoboard import DemoBoard

PROJECT = "tt_um_sidisnotacoder_shadowstack"
CLOCK_HZ = 10_000_000   # tile clock from the RP2040; the protocol is asynchronous to it

CMD_TRACE, CMD_RESET, CMD_CLEAR, CMD_CONFIG, CMD_VIEW = 1, 2, 3, 4, 5
PAYLOAD_LEN = {CMD_TRACE: 5, CMD_RESET: 0, CMD_CLEAR: 0, CMD_CONFIG: 1, CMD_VIEW: 1}


class Pins:
    """Adapter over the ttboard SDK. uio[2:0] are driven by the RP2040."""

    def __init__(self):
        self.tt = DemoBoard.get()
        getattr(self.tt.shuttle, PROJECT).enable()
        self.tt.uio_oe_pico.value = 0b0000_0111
        self.tt.uio_in.value = 0
        self.tt.ui_in.value = 0
        self.tt.clock_project_PWM(CLOCK_HZ)
        self.tt.reset_project(True)
        time.sleep_ms(1)
        self.tt.reset_project(False)

    def put(self, byte, sof):
        sof_bit = 0b10 if sof else 0
        self.tt.ui_in.value = byte
        self.tt.uio_in.value = sof_bit
        self.tt.uio_in.value = sof_bit | 1      # STROBE high (>= 3 tile clocks)
        self.tt.uio_in.value = sof_bit           # STROBE low

    def readback(self):
        return int(self.tt.uo_out.value)

    def flags(self):
        uio = int(self.tt.uio_out.value)
        return dict(fault=uio >> 3 & 1, overflow=uio >> 4 & 1, underflow=uio >> 5 & 1,
                    lp_fault=uio >> 6 & 1, busy=uio >> 7 & 1)


def send(p, cmd, payload=b"", trap=False):
    p.put((cmd << 4) | (1 if trap else 0), True)
    for b in payload:
        p.put(b, False)


def read_view(p, v):
    send(p, CMD_VIEW, bytes([v]))
    return p.readback()


def replay(p, path, chunk=512):
    """Stream a .bin file (concatenated packets) from flash."""
    n = 0
    with open(path, "rb") as f:
        buf = b""
        while True:
            data = f.read(chunk)
            if not data and not buf:
                break
            buf += data
            i = 0
            while i < len(buf):
                n_pay = PAYLOAD_LEN.get(buf[i] >> 4, 0)
                if i + 1 + n_pay > len(buf):
                    if data:
                        break                    # incomplete packet; read more
                    return n                     # truncated file
                p.put(buf[i], True)
                for b in buf[i + 1:i + 1 + n_pay]:
                    p.put(b, False)
                i += 1 + n_pay
                n += 1
            buf = buf[i:]
    return n


def report(p, label):
    st = read_view(p, 0)
    print("%-8s status=0x%02x depth=%d %s faults=%d lost=%d" % (
        label, st, st >> 4, p.flags(), read_view(p, 3), read_view(p, 4)))
    send(p, CMD_VIEW, b"\x00")                   # leave status on uo_out for the 7-seg/LEDs


def main():
    p = Pins()
    ident = read_view(p, 7)
    print("ID byte: 0x%02x (%s)" % (ident, "ok" if ident == 0x5A else "UNEXPECTED"))

    send(p, CMD_CONFIG, b"\x00")                 # WRAP, underflow IGNORE, no forward-edge
    t = time.ticks_ms()
    n = replay(p, "clean.bin")
    print("clean.bin: %d packets in %d ms" % (n, time.ticks_diff(time.ticks_ms(), t)))
    report(p, "clean")

    send(p, CMD_CLEAR)
    send(p, CMD_RESET)
    n = replay(p, "attack.bin")
    print("attack.bin: %d packets" % n)
    report(p, "attack")
    bad = read_view(p, 5) | read_view(p, 6) << 8
    print("last_bad_pc = 0x%04x (expected win() = 0x006a)" % bad)
    send(p, CMD_VIEW, b"\x00")


if __name__ == "__main__":
    main()
