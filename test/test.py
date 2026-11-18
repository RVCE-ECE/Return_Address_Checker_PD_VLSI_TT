# SPDX-License-Identifier: Apache-2.0
"""cocotb tests for tt_um_sidisnotacoder_shadowstack.

Every packet is mirrored into the golden model (model/shadow_model.py) and the
tile's outputs are compared with it after each packet. Directed tests also
assert the expected values explicitly, so a shared bug in the model and RTL
doesn't go unnoticed.
"""

import glob
import json
import os
import random

import cocotb

from tb_host import (
    GL,
    C_EBREAK,
    C_NOP,
    NOP,
    Host,
    addi,
    auipc,
    c_add,
    c_j,
    c_jal,
    c_jalr,
    c_jr,
    c_mv,
    jal,
    jalr,
    lpad,
    parse_packet_stream,
)

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE_DIR = os.path.join(HERE, "..", "traces")
RANDOM_PACKETS = int(os.environ.get("RANDOM_PACKETS", "800" if GL else "5000"))


async def new_host(dut):
    h = Host(dut)
    await h.start()
    return h


def status(h):
    m = h.model
    return dict(depth=m.depth, fault=m.fault, overflow=m.overflow, underflow=m.underflow,
                lp_fault=m.lp_fault, lost=m.lost, fault_count=m.fault_count)


# --------------------------------------------------------------------------
@cocotb.test()
async def test_reset_and_id(dut):
    h = await new_host(dut)
    assert await h.read_view(7) == 0x5A
    assert int(dut.uo_out.value) == 0
    assert (int(dut.uio_out.value) >> 3) == 0


@cocotb.test()
async def test_hint_table_32bit(dut):
    """Every JAL/JALR row of the RAS hint table."""
    h = await new_host(dut)
    # JAL: push iff rd is link
    for rd, pushes in [(0, False), (1, True), (5, True), (2, False), (7, False)]:
        await h.reset_stack()
        await h.trace(0x100, jal(rd))
        assert h.model.depth == (1 if pushes else 0), f"JAL rd=x{rd}"
    # JALR rows: (rd, rs1) -> (depth after, pop expected)
    rows = [
        (0, 6, 1, False),   # neither link: no action (depth stays 1)
        (0, 1, 0, True),    # return via x1
        (0, 5, 0, True),    # return via x5
        (1, 6, 2, False),   # call via x1
        (5, 6, 2, False),   # call via x5
        (1, 5, 1, True),    # pop then push (rd != rs1)
        (5, 1, 1, True),    # pop then push (rd != rs1)
        (1, 1, 2, False),   # rd == rs1: push
        (5, 5, 2, False),   # rd == rs1: push
    ]
    for rd, rs1, depth_after, popped in rows:
        await h.reset_stack()
        await h.trace(0x200, jal(1))             # depth 1, expects 0x204
        await h.trace(0x300, jalr(rd, rs1))
        assert h.model.depth == depth_after, f"JALR rd=x{rd} rs1=x{rs1}"
        assert h.model.pending == popped
        await h.trace(0x204, NOP)                # correct target if it popped
        assert not h.model.fault
    # JALR with funct3 != 0 is not a jump
    await h.reset_stack()
    await h.trace(0x400, jalr(1, 6, f3=1))
    assert h.model.depth == 0


@cocotb.test()
async def test_hint_table_compressed(dut):
    h = await new_host(dut)
    junk = 0xA50000  # inst[23:16] must be ignored for compressed encodings
    await h.trace(0x100, c_jal() | junk)
    assert h.model.depth == 1 and h.model.stack[h.model.tp] == 0x102
    await h.trace(0x200, c_jr(1) | junk)          # C.JR x1: return
    await h.trace(0x102, C_NOP)
    assert h.model.depth == 0 and not h.model.fault
    await h.trace(0x300, c_jalr(6))               # C.JALR x6: push
    await h.trace(0x400, c_jalr(1))               # C.JALR x1: rd==rs1 -> push
    assert h.model.depth == 2
    await h.trace(0x500, c_jalr(5))               # C.JALR x5: pop then push
    assert h.model.depth == 2 and h.model.pending
    await h.trace(0x402, C_NOP)
    await h.trace(0x600, c_jr(5))                 # C.JR x5: return to 0x502
    await h.trace(0x502, C_NOP)
    await h.trace(0x700, c_jr(6))                 # C.JR x6: plain indirect jump
    assert h.model.depth == 1
    assert not h.model.fault


@cocotb.test()
async def test_decoys(dut):
    """Encodings that must not be treated as calls or returns."""
    h = await new_host(dut)
    await h.trace(0x10, jal(1))
    decoys = [c_mv(1, 5), c_mv(5, 1), c_add(1, 5), c_add(5, 1), C_EBREAK, c_j(), c_j(0x7FF),
              jal(0), jal(3), jalr(0, 6), jalr(2, 3), C_NOP, NOP, addi(1, 1, 4), auipc(1),
              (4 << 13) | 0b10, (4 << 13) | (1 << 12) | 0b10]  # rs1=0 forms
    for i, inst in enumerate(decoys):
        await h.trace(0x1000 + 4 * i, inst)
        assert h.model.depth == 1 and not h.model.pending, f"decoy {inst:#x}"
    assert not h.model.fault


@cocotb.test()
async def test_return_address_width(dut):
    h = await new_host(dut)
    await h.trace(0x1000, jal(1))                 # 32-bit call -> 0x1004
    await h.trace(0x2000, c_jal())                # 16-bit call -> 0x2002
    await h.trace(0x3000, c_jr(1))
    await h.trace(0x2004, NOP)                    # wrong: should be 0x2002
    assert h.model.fault and h.model.last_bad_pc == 0x2004
    await h.trace(0x3002, jalr(0, 1))
    await h.trace(0x1004, NOP)
    assert h.model.fault_count == 1
    assert await h.read_view(5) == 0x04 and await h.read_view(6) == 0x20
    # PC wrap at 16 bits
    await h.clear_flags()
    await h.trace(0xFFFE, c_jal())
    await h.trace(0x10, c_jr(1))
    await h.trace(0x0000, NOP)
    assert not h.model.fault


@cocotb.test()
async def test_pop_then_push(dut):
    """Coroutine-style swaps replace the top entry in place."""
    h = await new_host(dut)
    await h.trace(0x100, jal(1))                  # push 0x104
    await h.trace(0x200, jalr(5, 1))              # expect 0x104, push 0x204
    assert await h.read_view(1) == 0x04
    await h.trace(0x104, NOP)
    await h.trace(0x108, jalr(1, 5))              # expect 0x204, push 0x10C
    await h.trace(0x204, NOP)
    await h.trace(0x208, jalr(0, 1))              # return to 0x10C
    await h.trace(0x999, NOP)                     # wrong
    assert h.model.fault and h.model.depth == 0
    # pop-then-push on an empty stack: underflow, then a normal push
    await h.trace(0x300, jalr(1, 5))
    assert h.model.underflow and h.model.depth == 1


async def recurse(h, depth, base=0x4000, corrupt_level=None, compressed=False):
    """Call `depth` levels deep, then return. Optionally corrupt one return."""
    pc = base
    for i in range(depth):
        await h.trace(pc + 8 * i, c_jal() if compressed else jal(1))
    for i in reversed(range(depth)):
        ra = pc + 8 * i + (2 if compressed else 4)
        await h.trace(0x8000 + i, jalr(0, 1))
        await h.trace(ra ^ (0x40 if i == corrupt_level else 0), NOP)


@cocotb.test()
async def test_overflow_wrap(dut):
    h = await new_host(dut)
    await recurse(h, 20)
    assert not h.model.fault and h.model.overflow and h.model.lost == 0 and h.model.depth == 0
    assert not h.model.underflow
    await h.clear_flags()
    await recurse(h, 20, corrupt_level=17)        # innermost frames are checked
    assert h.model.fault
    await h.clear_flags()
    await recurse(h, 20, corrupt_level=2)         # outer frame is lost: not checked
    assert not h.model.fault and not h.model.underflow


@cocotb.test()
async def test_overflow_saturate(dut):
    h = await new_host(dut)
    await h.config(ovf_sat=True)
    await recurse(h, 20, compressed=True)
    assert not h.model.fault and h.model.overflow and h.model.lost == 0
    await h.clear_flags()
    await recurse(h, 20, corrupt_level=3)         # outermost frames are checked
    assert h.model.fault
    await h.clear_flags()
    await recurse(h, 20, corrupt_level=15)        # inner frame was dropped
    assert not h.model.fault


@cocotb.test()
async def test_underflow_policies(dut):
    h = await new_host(dut)
    await h.trace(0x100, jalr(0, 1))
    assert h.model.underflow and not h.model.fault
    await h.clear_flags()
    await h.config(unf_fault=True)
    await h.trace(0x100, c_jr(1))
    assert h.model.underflow and h.model.fault and h.model.fault_count == 1
    assert h.model.last_bad_pc == 0  # underflow faults don't record a PC
    # Both a mismatch and an underflow fault in one packet count twice
    await h.trace(0x200, jal(1))
    await h.trace(0x300, jalr(0, 1))
    await h.trace(0x999, jalr(0, 5))
    assert h.model.fault_count == 3


@cocotb.test()
async def test_trap_skips_pending(dut):
    h = await new_host(dut)
    await h.trace(0x100, jal(1))
    await h.trace(0x200, jalr(0, 1))
    await h.trace(0x0800, NOP, trap=True)         # interrupt right after the return
    assert not h.model.fault and h.model.skip_count == 1
    assert await h.read_view(8) == 1
    # trap flag with nothing pending: no skip counted, and the packet is still decoded
    await h.trace(0x0804, jal(1), trap=True)
    assert h.model.skip_count == 1 and h.model.depth == 1


@cocotb.test()
async def test_commands(dut):
    h = await new_host(dut)
    for i in range(10):
        await h.trace(0x100 + 4 * i, jal(5))
    await h.trace(0x200, jalr(0, 5))
    await h.trace(0x300, NOP)                     # fault
    await h.reset_stack()
    assert h.model.depth == 0 and h.model.lost == 0 and h.model.fault  # flags kept
    await h.clear_flags()
    assert h.model.flags() == 0 and h.model.fault_count == 0
    await h.config(ovf_sat=True, unf_fault=True, lp_en=True)
    await h.config()
    for v in range(16):
        await h.set_view(v)
    await h.check(full=True)
    # an unknown command header is ignored, and so are its stray bytes
    await h.send_raw([(0x90, 1), (0x12, 0), (0x34, 0)])
    await h.check(full=True)


@cocotb.test()
async def test_resync(dut):
    """Garbage and truncated frames followed by a valid header."""
    h = await new_host(dut)
    await h.send_raw([(0xFF, 0), (0x12, 0), (0x00, 0)])        # junk while idle
    await h.check()
    await h.send_raw([(0x10, 1), (0x00, 0), (0x01, 0)])        # TRACE cut short
    assert (int(dut.uio_out.value) >> 7) & 1 == 1, "busy should be set mid-frame"
    await h.trace(0x100, jal(1))                               # SOF restarts the parser
    assert h.model.depth == 1
    await h.send_raw([(0x40, 1)])                              # CONFIG header only
    await h.set_view(7)                                        # resync to SET_VIEW
    assert int(dut.uo_out.value) == 0x5A
    await h.set_view(0)
    await h.check(full=True)


@cocotb.test()
async def test_lost_saturation_desync(dut):
    """More than 255 lost frames: compares stop until RESET_STACK."""
    h = await new_host(dut)
    await h.config(ovf_sat=True)
    for i in range(8 + 260):
        await h.trace(0x100, jal(1), check=(i % 64 == 0))
    await h.check(full=True)
    assert h.model.lost == 255 and h.model.desync
    for i in range(300):
        await h.trace(0x200, jalr(0, 1), check=False)
        await h.trace(0x500 + i, NOP, check=(i % 64 == 0))   # all wrong, none checked
    await h.check(full=True)
    assert not h.model.fault and h.model.underflow
    await h.reset_stack()
    await h.trace(0x100, jal(1))
    await h.trace(0x200, jalr(0, 1))
    await h.trace(0x300, NOP)
    assert h.model.fault                          # checking resumed


@cocotb.test()
async def test_forward_edge(dut):
    h = await new_host(dut)
    # disabled by default
    await h.trace(0x100, jalr(0, 6))
    await h.trace(0x200, NOP)
    assert not h.model.lp_fault
    await h.config(lp_en=True)
    await h.trace(0x100, jalr(0, 6))
    await h.trace(0x200, lpad(0x123))             # label not checked
    await h.trace(0x204, c_jalr(10))
    await h.trace(0x300, lpad())
    for rs1 in (1, 5, 7):                          # exempt registers
        await h.trace(0x400, jalr(0, rs1))
        await h.trace(0x500, NOP)
        await h.trace(0x600, c_jr(rs1))
        await h.trace(0x700, NOP)
    assert not h.model.lp_fault
    await h.trace(0x100, jalr(1, 6))              # indirect call without landing pad
    await h.trace(0x200, NOP, trap=True)          # trap: check skipped
    assert not h.model.lp_fault and h.model.skip_count == 1
    await h.trace(0x204, c_jr(6))
    await h.trace(0x300, auipc(1))                # auipc x1 is not a landing pad
    assert h.model.lp_fault
    assert (int(dut.uio_out.value) >> 6) & 1 == 1


# ------------------------------------------------------------------ random
def random_stream(rng, n):
    """Yield ('trace', pc, inst, trap) or ('cmd', name, arg) items from a random call tree."""
    calls = [lambda: (jal(rng.choice([1, 5]), rng.getrandbits(13)), 4),
             lambda: (jalr(rng.choice([1, 5]), rng.choice([6, 10, 11, 7])), 4),
             lambda: (jalr(1, 1), 4), lambda: (jalr(5, 5), 4),
             lambda: (c_jal(rng.getrandbits(11)) | rng.getrandbits(8) << 16, 2),
             lambda: (c_jalr(rng.choice([6, 10, 1, 7])), 2)]
    rets = [lambda: jalr(0, 1), lambda: jalr(0, 5), lambda: jalr(rng.choice([0, 2]), rng.choice([1, 5])),
            lambda: c_jr(1), lambda: c_jr(5)]
    stack = []
    pc = 0x1000
    trap = False
    for _ in range(n):
        r = rng.random()
        if r < 0.01:
            yield ("cmd", rng.choice(["reset", "clear", "config"]), rng.getrandbits(3))
            if rng.random() < 0.5:
                stack = []
            continue
        if r < 0.33 and len(stack) < 40:
            inst, size = rng.choice(calls)()
            yield ("trace", pc, inst, trap)
            stack.append((pc + size) & 0xFFFF)
            pc = rng.getrandbits(16) & ~1
        elif r < 0.62 and stack:
            yield ("trace", pc, rng.choice(rets)(), trap)
            pc = stack.pop()
            if rng.random() < 0.03:
                pc ^= 1 << rng.randrange(1, 16)             # corrupted return
        elif r < 0.67:
            yield ("trace", pc, rng.choice([jalr(5, 1), jalr(1, 5), c_jalr(5)]), trap)
            pc = rng.getrandbits(16) & ~1
        elif r < 0.72:
            yield ("trace", pc, rng.getrandbits(24), trap)   # anything at all
            pc = (pc + 4) & 0xFFFF
        elif r < 0.76:
            yield ("trace", pc, rng.choice([lpad(rng.getrandbits(12)), jalr(0, 6), c_jr(10)]), trap)
            pc = (pc + 4) & 0xFFFF
        else:
            inst = rng.choice([NOP, C_NOP, addi(3, 3, 1), c_mv(1, 5), c_add(5, 1), C_EBREAK, c_j()])
            yield ("trace", pc, inst, trap)
            pc = (pc + (4 if inst & 3 == 3 else 2)) & 0xFFFF
        trap = rng.random() < 0.02


@cocotb.test()
async def test_random_call_trees(dut):
    seed = int(os.environ.get("SEED", "12345"))
    rng = random.Random(seed)
    h = await new_host(dut)
    dut._log.info(f"seed={seed} packets={RANDOM_PACKETS}")
    views = [0, 0, 0, 1, 3, 4, 8]
    for i, item in enumerate(random_stream(rng, RANDOM_PACKETS)):
        if item[0] == "trace":
            _, pc, inst, trap = item
            await h.trace(pc, inst, trap)
        elif item[1] == "reset":
            await h.reset_stack()
        elif item[1] == "clear":
            await h.clear_flags()
        else:
            await h.config(item[2] & 1, item[2] >> 1 & 1, item[2] >> 2 & 1)
        if i % 97 == 0:
            await h.set_view(rng.choice(views))
        if i % 500 == 0:
            await h.check(full=True)
    await h.check(full=True)
    dut._log.info(f"final status: {status(h)}")


# ------------------------------------------------------------------ traces
@cocotb.test()
async def test_trace_replay(dut):
    """Replay the Spike traces in traces/*.bin and check the expected results."""
    files = sorted(glob.glob(os.path.join(TRACE_DIR, "*.bin")))
    if not files:
        dut._log.warning("no traces found; skipping")
        return
    h = await new_host(dut)
    for path in files:
        name = os.path.basename(path)
        with open(path, "rb") as f:
            packets = parse_packet_stream(f.read())
        with open(path[:-4] + ".json") as f:
            expect = json.load(f)
        await h.reset_stack()
        await h.clear_flags()
        await h.config(**expect.get("config", {}))
        for i, (hdr, payload) in enumerate(packets):
            await h.packet(hdr >> 4, payload, trap=bool(hdr & 1), check=(i % 50 == 0))
        await h.check(full=True)
        m = h.model
        dut._log.info(f"{name}: {len(packets)} packets, fault={int(m.fault)} lp_fault={int(m.lp_fault)} "
                      f"faults={m.fault_count} last_bad_pc={m.last_bad_pc:#06x}")
        assert int(m.fault) == expect["fault"], name
        assert int(m.lp_fault) == expect.get("lp_fault", 0), name
        if expect["fault"]:
            assert m.last_bad_pc == expect["last_bad_pc"], name
            if "win_addr" in expect:  # the hijacked return lands on win()'s entry
                assert m.last_bad_pc == expect["win_addr"], name
