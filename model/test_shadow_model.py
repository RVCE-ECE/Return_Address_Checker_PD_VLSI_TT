# SPDX-License-Identifier: Apache-2.0
"""Hand-checked unit tests for the golden model (run: pytest model/)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shadow_model import ShadowModel, decode  # noqa: E402


def jalr(rd, rs1):
    return rs1 << 15 | rd << 7 | 0x67


def jal(rd):
    return rd << 7 | 0x6F


C_JR = lambda r: 0x8002 | r << 7          # noqa: E731
C_JALR = lambda r: 0x9002 | r << 7        # noqa: E731


@pytest.mark.parametrize("inst,push,pop", [
    (jal(0), 0, 0), (jal(1), 1, 0), (jal(5), 1, 0), (jal(2), 0, 0),
    (jalr(0, 6), 0, 0), (jalr(0, 1), 0, 1), (jalr(0, 5), 0, 1),
    (jalr(1, 6), 1, 0), (jalr(5, 6), 1, 0),
    (jalr(1, 5), 1, 1), (jalr(5, 1), 1, 1),
    (jalr(1, 1), 1, 0), (jalr(5, 5), 1, 0),
    (0x2001, 1, 0),                 # C.JAL
    (0xA001, 0, 0),                 # C.J
    (C_JR(1), 0, 1), (C_JR(5), 0, 1), (C_JR(6), 0, 0),
    (C_JALR(1), 1, 0), (C_JALR(5), 1, 1), (C_JALR(6), 1, 0),
    (0x9002, 0, 0),                 # C.EBREAK
    (0x8096, 0, 0),                 # C.MV x1, x5
    (0x9096, 0, 0),                 # C.ADD x1, x5
])
def test_decode_table(inst, push, pop):
    d = decode(inst)
    assert (d.push, d.pop) == (bool(push), bool(pop))


def test_lpad_and_indirect():
    assert decode(0x00017).is_lpad and not decode(0x00097).is_lpad
    assert decode(jalr(0, 6)).indirect_nonret
    for r in (1, 5, 7):
        assert not decode(jalr(0, r)).indirect_nonret
        assert not decode(C_JR(r)).indirect_nonret
    assert not decode(jal(1)).indirect_nonret


def call_return(m, n, bad=None, c=False):
    for i in range(n):
        m.trace(0x100 + 8 * i, 0x2001 if c else jal(1))
    for i in reversed(range(n)):
        m.trace(0x900, jalr(0, 1))
        m.trace(0x100 + 8 * i + (2 if c else 4) + (0x40 if i == bad else 0), 0x13)


def test_wrap_checks_innermost():
    m = ShadowModel(depth=4)
    call_return(m, 10)
    assert (m.fault, m.overflow, m.underflow, m.lost) == (False, True, False, 0)
    m.clear_flags()
    call_return(m, 10, bad=8)
    assert m.fault
    m.clear_flags()
    call_return(m, 10, bad=1)
    assert not m.fault


def test_saturate_checks_outermost():
    m = ShadowModel(depth=4)
    m.config(1)
    call_return(m, 10, c=True)
    assert (m.fault, m.lost) == (False, 0)
    call_return(m, 10, bad=2)
    assert m.fault
    m.clear_flags()
    call_return(m, 10, bad=7)
    assert not m.fault


def test_underflow_policy():
    m = ShadowModel()
    m.trace(0, jalr(0, 1))
    assert m.underflow and not m.fault
    m.config(2)
    m.trace(0, jalr(0, 1))
    assert m.fault and m.fault_count == 1


def test_trap_skip_and_mismatch():
    m = ShadowModel()
    m.trace(0x10, jal(1))
    m.trace(0x20, jalr(0, 1))
    m.trace(0x99, 0x13, trap=True)
    assert not m.fault and m.skip_count == 1
    m.trace(0x10, jal(1))
    m.trace(0x20, jalr(0, 1))
    m.trace(0x98, 0x13)
    assert m.fault and m.last_bad_pc == 0x98 and m.expected == 0x14


def test_addr_width():
    m = ShadowModel(addr_w=12)
    m.trace(0x1FFC, jal(1))          # ra = 0x2000 -> 0x000 in 12 bits
    m.trace(0x0, jalr(0, 1))
    m.trace(0x3000, 0x13)            # aliases with 0x000
    assert not m.fault
