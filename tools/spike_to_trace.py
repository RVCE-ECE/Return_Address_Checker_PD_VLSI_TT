#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Convert a Spike commit log into shadow-stack TRACE packets.

    spike --isa=rv32imc_zicsr -l --log-commits prog.elf 2> prog.log
    tools/spike_to_trace.py prog.log -o traces/prog.bin [--elf prog.elf]

Log lines used (checked against riscv-isa-sim master, 2026):
    core   0: 3 0x80000000 (0x00001117) x2  0x80001000     <- commit: priv pc (inst)
    core   0: exception trap_machine_ecall, epc 0x80000262  <- next commit gets TRAP=1
    core   0: >>>>  trap_entry                              <- ignored
Execute lines ("core 0: 0x... (0x...) disasm") have no priv field and are ignored.

Outputs:
    <out>.bin   concatenated packets (header byte + payload), SOF on each header
    <out>.json  golden-model result for the trace (used by the cocotb replay test)

By default the trace is "compacted": only the packets the monitor needs are kept
(jumps, the instruction right after any jump, and trap-flagged instructions).
The tool checks that the full and compacted traces give identical model results.
"""

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model"))
from shadow_model import CMD_TRACE, HW_CONFIG, ShadowModel, decode, trace_payload  # noqa: E402

COMMIT_RE = re.compile(r"^core\s+\d+:\s+(\d)\s+0x([0-9a-fA-F]+)\s+\(0x([0-9a-fA-F]+)\)")
EXC_RE = re.compile(r"^core\s+\d+:\s+(exception|interrupt|trap)\b")


def parse_log(path, min_pc):
    """Return [(pc, inst, trap)] for committed instructions with pc >= min_pc."""
    recs = []
    trap = False
    with open(path, errors="replace") as f:
        for line in f:
            if EXC_RE.match(line):
                trap = True
                continue
            m = COMMIT_RE.match(line)
            if not m:
                continue
            pc = int(m.group(2), 16)
            inst = int(m.group(3), 16)
            if pc < min_pc:
                continue
            recs.append((pc, inst, trap))
            trap = False
    return recs


def is_relevant(inst):
    d = decode(inst)
    return d.push or d.pop or d.indirect_nonret


def compact(recs):
    out = []
    prev_relevant = False
    for pc, inst, trap in recs:
        rel = is_relevant(inst)
        if rel or prev_relevant or trap:
            out.append((pc, inst, trap))
        prev_relevant = rel
    return out


FULL_CONFIG = dict(depth=8, addr_w=16, lp_en_built=True, lbpc_built=True)


def run_model(recs, cfg=0, hw=None):
    m = ShadowModel(**(hw or HW_CONFIG))
    m.config(cfg)
    first_fault_pc = None
    for pc, inst, trap in recs:
        m.trace(pc, inst & 0xFFFFFF, trap)
        if m.fault and first_fault_pc is None:
            first_fault_pc = m.last_bad_pc
    return m, first_fault_pc


def summary(m):
    return dict(fault=int(m.fault), overflow=int(m.overflow), underflow=int(m.underflow),
                lp_fault=int(m.lp_fault), fault_count=m.fault_count, lost=m.lost,
                skip_count=m.skip_count, last_bad_pc=m.last_bad_pc, depth=m.depth)


def symbol_addr(elf, nm, name):
    try:
        out = subprocess.run([nm, elf], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == name:
            return int(parts[0], 16)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("-o", "--out", required=True, help="output .bin path (a .json is written next to it)")
    ap.add_argument("--min-pc", type=lambda s: int(s, 0), default=0x80000000,
                    help="drop instructions below this PC (Spike boot ROM); default 0x80000000")
    ap.add_argument("--full", action="store_true", help="keep every instruction (no compaction)")
    ap.add_argument("--config", type=lambda s: int(s, 0), default=0, help="CONFIG byte to replay with")
    ap.add_argument("--elf", help="ELF file, to look up the 'win' symbol for the report")
    ap.add_argument("--nm", default="riscv64-elf-nm")
    ap.add_argument("--full-hw", action="store_true",
                    help="model the full 1x2 build (DEPTH 8, forward-edge, last_bad_pc) instead of HW_CONFIG")
    args = ap.parse_args()

    recs = parse_log(args.log, args.min_pc)
    if not recs:
        sys.exit(f"no commit lines found in {args.log} (was Spike run with --log-commits?)")
    small = compact(recs)

    hw = FULL_CONFIG if args.full_hw else HW_CONFIG
    m_full, _ = run_model(recs, args.config, hw)
    m_small, first_bad = run_model(small, args.config, hw)
    if summary(m_full) != summary(m_small):
        sys.exit(f"compaction changed the result!\n full:  {summary(m_full)}\n small: {summary(m_small)}")
    keep = recs if args.full else small

    with open(args.out, "wb") as f:
        for pc, inst, trap in keep:
            f.write(bytes([(CMD_TRACE << 4) | int(trap)]) + trace_payload(pc, inst))

    res = summary(m_small)
    res["first_bad_pc"] = first_bad
    res["hw"] = hw
    res["packets"] = len(keep)
    res["instructions"] = len(recs)
    res["traps"] = sum(t for _, _, t in recs)
    res["config"] = dict(ovf_sat=bool(args.config & 1), unf_fault=bool(args.config & 2),
                         lp_en=bool(args.config & 4))
    if args.elf:
        win = symbol_addr(args.elf, args.nm, "win")
        if win is not None:
            res["win_addr"] = win & 0xFFFF
    with open(os.path.splitext(args.out)[0] + ".json", "w") as f:
        json.dump(res, f, indent=2)

    print(f"{os.path.basename(args.out)}: {len(recs)} instructions -> {len(keep)} packets, "
          f"{res['traps']} trap(s); fault={res['fault']} overflow={res['overflow']} "
          f"underflow={res['underflow']} lp_fault={res['lp_fault']} lost={res['lost']} faults={res['fault_count']} "
          f"last_bad_pc={res['last_bad_pc']:#06x}"
          + (f" (win={res['win_addr']:#06x})" if "win_addr" in res else ""))


if __name__ == "__main__":
    main()
