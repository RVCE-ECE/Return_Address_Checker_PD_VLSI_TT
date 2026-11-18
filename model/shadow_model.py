"""Golden model of the Zicfiss-inspired external shadow-stack monitor.

This is the executable specification. The RTL must match it after every
packet. See docs/design.md for the prose version of the rules.
"""

from dataclasses import dataclass

# Packet commands (header[7:4])
CMD_TRACE = 0x1
CMD_RESET_STACK = 0x2
CMD_CLEAR_FLAGS = 0x3
CMD_CONFIG = 0x4
CMD_SET_VIEW = 0x5

PAYLOAD_LEN = {CMD_TRACE: 5, CMD_RESET_STACK: 0, CMD_CLEAR_FLAGS: 0, CMD_CONFIG: 1, CMD_SET_VIEW: 1}

ID_BYTE = 0x5A
NUM_VIEWS = 9

# Parameters of the hardware that is actually built. Keep in sync with the
# defaults in src/tt_um_sidisnotacoder_shadowstack.v (1x1 tile: DEPTH 6, no
# forward-edge check, no last_bad_pc). The 1x2 build uses
# dict(depth=8, addr_w=16, lp_en_built=True, lbpc_built=True).
HW_CONFIG = dict(depth=6, addr_w=16, lp_en_built=False, lbpc_built=False)

LINK = (1, 5)
LP_EXEMPT = (1, 5, 7)


@dataclass
class Decoded:
    push: bool = False
    pop: bool = False
    is_c: bool = False
    indirect_nonret: bool = False
    is_lpad: bool = False


def decode(inst: int) -> Decoded:
    """Classify a 32-bit or 16-bit RISC-V instruction (only inst[23:0] is used)."""
    inst &= 0xFFFFFF
    d = Decoded()
    if inst & 0b11 == 0b11:
        opcode = inst & 0x7F
        rd = (inst >> 7) & 0x1F
        f3 = (inst >> 12) & 0x7
        rs1 = (inst >> 15) & 0x1F
        if opcode == 0b1101111:  # JAL
            d.push = rd in LINK
        elif opcode == 0b1100111 and f3 == 0:  # JALR
            rd_l, rs_l = rd in LINK, rs1 in LINK
            if not rd_l and rs_l:
                d.pop = True
            elif rd_l and not rs_l:
                d.push = True
            elif rd_l and rs_l:
                d.push = True
                d.pop = rd != rs1
            d.indirect_nonret = rs1 not in LP_EXEMPT
        elif opcode == 0b0010111 and rd == 0:  # AUIPC x0 == LPAD
            d.is_lpad = True
        return d

    d.is_c = True
    inst &= 0xFFFF
    q = inst & 0b11
    f3 = (inst >> 13) & 0x7
    bit12 = (inst >> 12) & 1
    rs1 = (inst >> 7) & 0x1F
    rs2 = (inst >> 2) & 0x1F
    if q == 0b01 and f3 == 0b001:  # C.JAL (RV32)
        d.push = True
    elif q == 0b10 and f3 == 0b100 and rs1 != 0 and rs2 == 0:
        if bit12 == 0:  # C.JR
            d.pop = rs1 in LINK
        else:  # C.JALR (rd = x1)
            d.push = True
            d.pop = rs1 == 5
        d.indirect_nonret = rs1 not in LP_EXEMPT
    return d


def _sat_inc(v: int) -> int:
    return v if v == 0xFF else v + 1


class ShadowModel:
    def __init__(self, depth: int = 8, addr_w: int = 16, lp_en_built: bool = True, lbpc_built: bool = True):
        """Defaults describe the full design. Use ShadowModel(**HW_CONFIG) for the built tile."""
        assert 1 <= depth <= 15 and 1 <= addr_w <= 16
        self.DEPTH = depth
        self.ADDR_W = addr_w
        self.AMASK = (1 << addr_w) - 1
        self.lp_en_built = lp_en_built
        self.lbpc_built = lbpc_built
        self.hard_reset()

    # ------------------------------------------------------------------ state
    def hard_reset(self):
        self.stack = [0] * self.DEPTH
        self.tp = 0  # index of top entry (valid when depth > 0)
        self.depth = 0
        self.lost = 0
        self.desync = False
        self.pending = False
        self.lp_pending = False
        self.expected = 0
        self.fault = self.overflow = self.underflow = self.lp_fault = False
        self.fault_count = 0
        self.skip_count = 0
        self.last_bad_pc = 0
        self.ovf_sat = False
        self.unf_fault = False
        self.lp_en = False
        self.view = 0

    # --------------------------------------------------------------- commands
    def reset_stack(self):
        self.tp = 0
        self.depth = 0
        self.lost = 0
        self.desync = False
        self.pending = False
        self.lp_pending = False

    def clear_flags(self):
        self.fault = self.overflow = self.underflow = self.lp_fault = False
        self.fault_count = 0
        self.skip_count = 0
        self.last_bad_pc = 0

    def config(self, b: int):
        self.ovf_sat = bool(b & 1)
        self.unf_fault = bool(b & 2)
        self.lp_en = bool(b & 4) and self.lp_en_built

    def set_view(self, v: int):
        self.view = v & 0xF

    def packet(self, header: int, payload: bytes = b""):
        cmd = header >> 4
        if cmd == CMD_TRACE:
            pc = payload[0] | payload[1] << 8
            inst = payload[2] | payload[3] << 8 | payload[4] << 16
            self.trace(pc, inst, bool(header & 1))
        elif cmd == CMD_RESET_STACK:
            self.reset_stack()
        elif cmd == CMD_CLEAR_FLAGS:
            self.clear_flags()
        elif cmd == CMD_CONFIG:
            self.config(payload[0])
        elif cmd == CMD_SET_VIEW:
            self.set_view(payload[0])

    # ------------------------------------------------------------------ trace
    def _next(self, i):
        return 0 if i == self.DEPTH - 1 else i + 1

    def _prev(self, i):
        return self.DEPTH - 1 if i == 0 else i - 1

    def _raise_fault(self):
        self.fault = True
        self.fault_count = _sat_inc(self.fault_count)

    def _pop_kind(self) -> str:
        """Which kind of pop happens now: 'entry', 'skip' (consume lost) or 'under'."""
        if self.ovf_sat:
            if self.lost > 0:
                return "skip"
            return "entry" if self.depth > 0 else "under"
        if self.depth > 0:
            return "entry"
        return "skip" if self.lost > 0 else "under"

    def _push(self, ra):
        if self.depth < self.DEPTH:
            self.tp = self._next(self.tp) if self.depth > 0 else self.tp
            self.stack[self.tp] = ra
            self.depth += 1
            return
        self.overflow = True
        if self.lost == 0xFF:
            self.desync = True
        else:
            self.lost += 1
        if not self.ovf_sat:  # WRAP: overwrite oldest
            self.tp = self._next(self.tp)
            self.stack[self.tp] = ra

    def trace(self, pc: int, inst: int, trap: bool = False):
        pc_a = pc & self.AMASK
        d = decode(inst)

        # 1. Resolve the previous packet's pending return / landing-pad check.
        if trap:
            if self.pending or self.lp_pending:
                self.skip_count = _sat_inc(self.skip_count)
        else:
            if self.pending and pc_a != self.expected:
                self._raise_fault()
                if self.lbpc_built:
                    self.last_bad_pc = pc_a
            if self.lp_pending and not d.is_lpad:
                self.lp_fault = True
        self.pending = False
        self.lp_pending = False

        # 2. Act on this packet's instruction.
        ra = (pc + (2 if d.is_c else 4)) & self.AMASK
        if d.pop:
            kind = self._pop_kind()
            if kind == "entry":
                self.expected = self.stack[self.tp]
                self.pending = not self.desync
                if d.push:  # pop-then-push: replace the top in place
                    self.stack[self.tp] = ra
                else:
                    self.depth -= 1
                    if self.depth > 0:
                        self.tp = self._prev(self.tp)
            else:
                if kind == "skip":
                    self.lost -= 1
                else:
                    self.underflow = True
                    if self.unf_fault and not self.desync:
                        self._raise_fault()
                if d.push:
                    self._push(ra)
        elif d.push:
            self._push(ra)

        if d.indirect_nonret and self.lp_en:
            self.lp_pending = True

    # --------------------------------------------------------------- readback
    def view_value(self, v: int) -> int:
        v &= 0xF
        if v == 0:
            return (self.depth & 0xF) << 4 | self.lp_fault << 3 | self.underflow << 2 | self.overflow << 1 | self.fault
        if v == 1:
            return self.expected & 0xFF
        if v == 2:
            return (self.expected >> 8) & 0xFF
        if v == 3:
            return self.fault_count
        if v == 4:
            return self.lost
        if v == 5:
            return self.last_bad_pc & 0xFF
        if v == 6:
            return (self.last_bad_pc >> 8) & 0xFF
        if v == 7:
            return ID_BYTE
        if v == 8:
            return self.skip_count
        return 0

    def readback(self) -> int:
        return self.view_value(self.view)

    def flags(self) -> int:
        """uio_out[7:3] excluding busy: {lp_fault, underflow, overflow, fault}."""
        return self.lp_fault << 3 | self.underflow << 2 | self.overflow << 1 | self.fault


def encode_packet(cmd: int, payload: bytes = b"", trap: bool = False) -> list:
    """Return [(byte, sof), ...] for one packet."""
    hdr = (cmd << 4) | (1 if trap else 0)
    return [(hdr, 1)] + [(b, 0) for b in payload]


def trace_payload(pc: int, inst: int) -> bytes:
    return bytes([pc & 0xFF, (pc >> 8) & 0xFF, inst & 0xFF, (inst >> 8) & 0xFF, (inst >> 16) & 0xFF])
