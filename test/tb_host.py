# SPDX-License-Identifier: Apache-2.0
"""cocotb host driver for the shadow-stack monitor, paired with the golden model."""

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model"))

from shadow_model import (  # noqa: E402
    CMD_CLEAR_FLAGS,
    CMD_CONFIG,
    CMD_RESET_STACK,
    CMD_SET_VIEW,
    CMD_TRACE,
    HW_CONFIG,
    NUM_VIEWS,
    PAYLOAD_LEN,
    ShadowModel,
    trace_payload,
)

# Host-side timing, in tile clocks. The synchronizer + edge detector needs
# 3 clocks; 4 per phase leaves margin.
SETUP = 2
HIGH = 4
LOW = 3

GL = os.environ.get("GATES") == "yes"


# ---------------------------------------------------------------- encoders
def jal(rd, imm_bits=0):
    return ((imm_bits & 0x1FFF) << 12 | (rd & 31) << 7 | 0x6F) & 0xFFFFFF


def jalr(rd, rs1, f3=0):
    return ((rs1 & 31) << 15 | (f3 & 7) << 12 | (rd & 31) << 7 | 0x67) & 0xFFFFFF


def lpad(label=0):
    return ((label & 0xFFF) << 12 | 0x17) & 0xFFFFFF


def auipc(rd, imm=0):
    return ((imm & 0xFFF) << 12 | (rd & 31) << 7 | 0x17) & 0xFFFFFF


def addi(rd, rs1, imm=0):
    return ((imm & 0xF) << 20 | (rs1 & 31) << 15 | (rd & 31) << 7 | 0x13) & 0xFFFFFF


def c_jal(off=0):
    return (1 << 13) | ((off & 0x7FF) << 2) | 0b01


def c_j(off=0):
    return (5 << 13) | ((off & 0x7FF) << 2) | 0b01


def c_jr(rs1):
    return (4 << 13) | (rs1 & 31) << 7 | 0b10


def c_jalr(rs1):
    return (4 << 13) | (1 << 12) | (rs1 & 31) << 7 | 0b10


def c_mv(rd, rs2):
    return (4 << 13) | (rd & 31) << 7 | (rs2 & 31) << 2 | 0b10


def c_add(rd, rs2):
    return (4 << 13) | (1 << 12) | (rd & 31) << 7 | (rs2 & 31) << 2 | 0b10


C_EBREAK = 0x9002
C_NOP = 0x0001
NOP = 0x000013


# ------------------------------------------------------------------ driver
class Host:
    def __init__(self, dut, model_kwargs=None):
        self.dut = dut
        self.model = ShadowModel(**(model_kwargs or HW_CONFIG))
        self.n_packets = 0

    async def start(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
        dut.ena.value = 1
        dut.ui_in.value = 0
        dut.uio_in.value = 0
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 5)
        dut.rst_n.value = 1
        await ClockCycles(dut.clk, 2)
        self.model.hard_reset()
        await self.check(full=True)

    async def send_byte(self, b, sof):
        dut = self.dut
        dut.ui_in.value = b & 0xFF
        dut.uio_in.value = (1 if sof else 0) << 1
        await ClockCycles(dut.clk, SETUP)
        dut.uio_in.value = (1 if sof else 0) << 1 | 1
        await ClockCycles(dut.clk, HIGH)
        dut.uio_in.value = (1 if sof else 0) << 1
        await ClockCycles(dut.clk, LOW)

    async def send_raw(self, pairs):
        """Send [(byte, sof), ...] without touching the model."""
        for b, s in pairs:
            await self.send_byte(b, s)

    async def packet(self, cmd, payload=b"", trap=False, check=True):
        hdr = (cmd << 4) | (1 if trap else 0)
        await self.send_byte(hdr, 1)
        for b in payload:
            await self.send_byte(b, 0)
        await ClockCycles(self.dut.clk, 2)
        self.model.packet(hdr, bytes(payload))
        self.n_packets += 1
        if check:
            await self.check()

    async def trace(self, pc, inst, trap=False, check=True):
        await self.packet(CMD_TRACE, trace_payload(pc, inst), trap, check)

    async def reset_stack(self):
        await self.packet(CMD_RESET_STACK)

    async def clear_flags(self):
        await self.packet(CMD_CLEAR_FLAGS)

    async def config(self, ovf_sat=False, unf_fault=False, lp_en=False):
        await self.packet(CMD_CONFIG, bytes([ovf_sat | unf_fault << 1 | lp_en << 2]))

    async def set_view(self, v, check=True):
        await self.packet(CMD_SET_VIEW, bytes([v]), check=check)

    def _uio_flags(self):
        uio = int(self.dut.uio_out.value)
        return (uio >> 3) & 0xF, (uio >> 7) & 1

    async def check(self, full=False):
        """Compare the RTL outputs with the model (current view, or all views)."""
        dut = self.dut
        flags, busy = self._uio_flags()
        assert busy == 0, "busy still set after a complete packet"
        assert flags == self.model.flags(), (
            f"pkt {self.n_packets}: uio flags {flags:04b} != model {self.model.flags():04b}"
        )
        assert int(dut.uio_oe.value) == 0xF8
        got = int(dut.uo_out.value)
        exp = self.model.readback()
        assert got == exp, f"pkt {self.n_packets}: view {self.model.view} uo_out {got:#04x} != model {exp:#04x}"
        if full:
            saved = self.model.view
            for v in range(NUM_VIEWS + 1):  # include one unused view
                await self.set_view(v)
            await self.set_view(saved)

    async def read_view(self, v):
        saved = self.model.view
        await self.set_view(v)
        val = int(self.dut.uo_out.value)
        await self.set_view(saved)
        return val


def parse_packet_stream(data: bytes):
    """Split a .bin trace file (concatenated packets) into (header, payload) tuples."""
    i = 0
    out = []
    while i < len(data):
        hdr = data[i]
        n = PAYLOAD_LEN.get(hdr >> 4, 0)
        out.append((hdr, data[i + 1 : i + 1 + n]))
        i += 1 + n
    return out


__all__ = [
    "Host", "GL", "HW_CONFIG", "parse_packet_stream",
    "jal", "jalr", "lpad", "auipc", "addi", "c_jal", "c_j", "c_jr", "c_jalr", "c_mv", "c_add",
    "C_EBREAK", "C_NOP", "NOP",
    "CMD_TRACE", "CMD_RESET_STACK", "CMD_CLEAR_FLAGS", "CMD_CONFIG", "CMD_SET_VIEW",
]
