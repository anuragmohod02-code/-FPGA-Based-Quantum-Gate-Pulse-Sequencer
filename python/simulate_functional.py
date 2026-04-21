"""
simulate_functional.py
=======================
Pure-Python behavioral simulator for top_pulse_sequencer.v.

Reproduces the full design in software — no Vivado, no licence, no hardware.
Models NCO, DRAG pulse ROM, FSM (all 6 states + repeat counter), and the
3-stage pipelined IQ modulator at the register-transfer level.

Generates:
  sim_outputs/iq_output.csv    — identical column format to tb_pulse_sequencer.v
  sim_outputs/sim_functional_overview.png  — annotated I/Q overview plot
  sim_outputs/sim_iq_scatter.png           — IQ constellation per gate
  sim_outputs/sim_per_gate.png             — per-gate zoomed waveforms

Usage:
  python simulate_functional.py               # runs default sequences
  python simulate_functional.py --cpmg        # CPMG-4 sequence only

This is the fastest way to iterate on DRAG parameters and gate sequences
before committing time to Vivado XSim runs.
"""

from __future__ import annotations
import os
import sys
import argparse
import csv
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
MEM_DIR     = os.path.join(PROJECT_DIR, "mem_files")
OUT_DIR     = os.path.join(PROJECT_DIR, "sim_outputs")

# Gate constants (match gate_decoder.v)
GATE_NOP  = 0
GATE_X    = 1
GATE_Y    = 2
GATE_XH   = 3
GATE_H    = 4

GATE_NAMES  = {GATE_NOP: "NOP", GATE_X: "X (π)", GATE_Y: "Y (π)",
               GATE_XH: "X/2 (π/2)", GATE_H: "H"}
GATE_COLORS = {GATE_NOP: "gray", GATE_X: "steelblue", GATE_Y: "darkorange",
               GATE_XH: "seagreen", GATE_H: "mediumpurple"}

ROM_DEPTH    = 256
PHASE_BITS   = 32
LUT_BITS     = 10
DATA_WIDTH   = 16
SAT_POS      = (1 << (DATA_WIDTH - 1)) - 1   # +32767
SAT_NEG      = -(1 << (DATA_WIDTH - 1))       # -32768


# ===========================================================================
# NCO Model
# ===========================================================================
class NCO:
    """32-bit phase accumulator NCO with 1024-entry sin/cos LUT."""

    def __init__(self, phase_bits: int = PHASE_BITS, lut_bits: int = LUT_BITS):
        self.phase_bits  = phase_bits
        self.lut_bits    = lut_bits
        self.phase_mask  = (1 << phase_bits) - 1
        lut_size         = 1 << lut_bits
        angles           = np.arange(lut_size) * 2.0 * np.pi / lut_size
        self._lut_sin    = np.round(np.sin(angles) * 32767.0).astype(np.int32)
        self._lut_cos    = np.round(np.cos(angles) * 32767.0).astype(np.int32)
        self.phase_acc   = 0
        self.cos_out: int = 0
        self.sin_out: int = 0

    def reset(self) -> None:
        self.phase_acc = 0
        self.cos_out   = 0
        self.sin_out   = 0

    def clock(self, phase_inc: int, phase_offset: int, enable: bool) -> None:
        """Advance one clock cycle (models posedge clk behaviour)."""
        if enable:
            self.phase_acc = (self.phase_acc + phase_inc) & self.phase_mask
            lut_phase      = (self.phase_acc + phase_offset) & self.phase_mask
            lut_addr       = lut_phase >> (self.phase_bits - self.lut_bits)
            self.cos_out   = int(self._lut_cos[lut_addr])
            self.sin_out   = int(self._lut_sin[lut_addr])
        else:
            self.cos_out = 0
            self.sin_out = 0


# ===========================================================================
# Pulse ROM Model
# ===========================================================================
class PulseROM:
    """
    2048-entry DRAG pulse ROM.
    Address = {gate_sel[2:0], sample_idx[7:0]} (concatenation, not multiply).
    1-cycle read latency (matches Verilog registered output).
    """

    def __init__(self, mem_dir: str):
        total_depth    = 1 << (3 + 8)  # 2048
        self._rom_i    = np.zeros(total_depth, dtype=np.int16)
        self._rom_q    = np.zeros(total_depth, dtype=np.int16)
        self._data_i: int = 0
        self._data_q: int = 0
        self.valid: bool  = False
        self._load_mem(mem_dir)

    def _load_mem(self, mem_dir: str) -> None:
        """Load .mem hex files into ROM arrays (matching $readmemh addresses)."""
        gate_files = {
            GATE_X:  ("drag_x_i.mem",  "drag_x_q.mem"),
            GATE_Y:  ("drag_y_i.mem",  "drag_y_q.mem"),
            GATE_XH: ("drag_xh_i.mem", "drag_xh_q.mem"),
            GATE_H:  ("drag_h_i.mem",  "drag_h_q.mem"),
        }
        for gate_id, (fi, fq) in gate_files.items():
            base = gate_id * ROM_DEPTH   # {gate_id, 8'b0} = gate_id << 8
            path_i = os.path.join(mem_dir, fi)
            path_q = os.path.join(mem_dir, fq)
            if not os.path.isfile(path_i):
                raise FileNotFoundError(
                    f"Missing mem file: {path_i}\n"
                    f"Run generate_pulse_rom.py first.")
            for idx, line in enumerate(open(path_i)):
                val = int(line.strip(), 16)
                if val > 0x7FFF:          # interpret as signed 16-bit
                    val -= 0x10000
                self._rom_i[base + idx] = np.int16(val)
            for idx, line in enumerate(open(path_q)):
                val = int(line.strip(), 16)
                if val > 0x7FFF:
                    val -= 0x10000
                self._rom_q[base + idx] = np.int16(val)

    def clock(self, gate_sel: int, sample_idx: int, load_en: bool) -> None:
        """Advance one clock cycle."""
        if load_en:
            self.valid = False
        else:
            addr         = (gate_sel << 8) | (sample_idx & 0xFF)
            self._data_i = int(self._rom_i[addr])
            self._data_q = int(self._rom_q[addr])
            self.valid   = True

    @property
    def data_i(self) -> int:
        return self._data_i

    @property
    def data_q(self) -> int:
        return self._data_q


# ===========================================================================
# IQ Modulator Model (3-stage pipeline, matches iq_modulator.v exactly)
# ===========================================================================
class IQModulator:
    """
    3-stage pipelined SSB IQ modulator:
      RF_I = I_env * cos - Q_env * sin
      RF_Q = I_env * sin + Q_env * cos
    All stages registered; 3-cycle latency from input to rf_i/rf_q.
    """

    def __init__(self):
        # Stage 1 registers
        self._s1_p_i_cos: int = 0
        self._s1_p_q_sin: int = 0
        self._s1_p_i_sin: int = 0
        self._s1_p_q_cos: int = 0
        self._s1_valid:   bool = False
        # Stage 2 registers
        self._s2_sum_i:  int  = 0
        self._s2_sum_q:  int  = 0
        self._s2_valid:  bool = False
        # Stage 3 outputs
        self.rf_i:    int  = 0
        self.rf_q:    int  = 0
        self.valid:   bool = False

    @staticmethod
    def _sat16(v: int) -> int:
        return max(SAT_NEG, min(SAT_POS, v >> 15))

    def clock(self, i_env: int, q_env: int, cos_c: int, sin_c: int,
              enable: bool) -> None:
        """Advance all pipeline stages simultaneously (like posedge clk)."""
        # Capture previous stage values before overwriting (all clocked together)
        s2_sum_i_prev = self._s2_sum_i
        s2_sum_q_prev = self._s2_sum_q
        s2_valid_prev = self._s2_valid

        # Stage 3 (reads stage-2 registers from previous cycle)
        self.rf_i   = self._sat16(self._s2_sum_i)
        self.rf_q   = self._sat16(self._s2_sum_q)
        self.valid  = self._s2_valid

        # Stage 2 (reads stage-1 registers)
        self._s2_sum_i = self._s1_p_i_cos - self._s1_p_q_sin
        self._s2_sum_q = self._s1_p_i_sin + self._s1_p_q_cos
        self._s2_valid = self._s1_valid

        # Stage 1 (reads inputs)
        if enable:
            self._s1_p_i_cos = i_env * cos_c
            self._s1_p_q_sin = q_env * sin_c
            self._s1_p_i_sin = i_env * sin_c
            self._s1_p_q_cos = q_env * cos_c
            self._s1_valid   = True
        else:
            self._s1_p_i_cos = self._s1_p_q_sin = 0
            self._s1_p_i_sin = self._s1_p_q_cos = 0
            self._s1_valid   = False


# ===========================================================================
# FSM Model
# ===========================================================================
class FSMState:
    IDLE   = 0
    FETCH  = 1
    DECODE = 2
    PLAY   = 3
    WAIT   = 4
    DONE   = 5


@dataclass
class Instruction:
    """8-bit instruction word: {repeat_cnt[2:0], 2'b00, opcode[2:0]}"""
    opcode:     int = GATE_NOP
    repeat_cnt: int = 0            # total plays = repeat_cnt + 1

    @classmethod
    def from_byte(cls, b: int) -> "Instruction":
        return cls(opcode=b & 0x07, repeat_cnt=(b >> 5) & 0x07)

    def to_byte(self) -> int:
        return ((self.repeat_cnt & 0x07) << 5) | (self.opcode & 0x07)


class SequencerFSM:
    """
    Behavioral model of pulse_sequencer_fsm.v.
    All updates are synchronous (call .clock() each cycle).
    """

    def __init__(self, rom: PulseROM, rom_depth: int = ROM_DEPTH, seq_len: int = 16):
        self._rom         = rom
        self._rom_depth   = rom_depth
        self._seq_len     = seq_len

        # Instruction RAM
        self._instr_ram: list[Instruction] = [Instruction() for _ in range(seq_len)]

        # Registered state
        self._state:            int  = FSMState.IDLE
        self._pc:               int  = 0
        self._sample_cnt:       int  = 0
        self._cur_opcode:       int  = GATE_NOP
        self._repeat_remaining: int  = 0
        self._seq_len_reg:      int  = 1

        # Outputs (registered)
        self.gate_sel_out:    int  = 0
        self.sample_idx_out:  int  = 0
        self.rom_load_en:     bool = False
        self.nco_enable:      bool = False
        self.mod_enable:      bool = False
        self.pulse_active:    bool = False
        self.seq_done:        bool = False

    def load_sequence(self, instructions: list[Instruction], seq_len: int) -> None:
        for i, instr in enumerate(instructions):
            if i < len(self._instr_ram):
                self._instr_ram[i] = instr
        self._seq_len = seq_len

    def reset(self) -> None:
        self._state            = FSMState.IDLE
        self._pc               = 0
        self._sample_cnt       = 0
        self._cur_opcode       = 0
        self._repeat_remaining = 0
        self.gate_sel_out      = 0
        self.sample_idx_out    = 0
        self.rom_load_en       = False
        self.nco_enable        = False
        self.mod_enable        = False
        self.pulse_active      = False
        self.seq_done          = False

    def _is_nop(self, opcode: int) -> bool:
        return opcode == GATE_NOP

    def clock(self, start: bool) -> None:
        """Advance one clock cycle."""
        # Default de-assertions
        self.rom_load_en  = False
        self.seq_done     = False
        self.pulse_active = False

        cur = self._state

        # --- Next state ---
        if cur == FSMState.IDLE:
            if start:
                self._seq_len_reg = self._seq_len
                self._state = FSMState.FETCH
                self._pc    = 0

        elif cur == FSMState.FETCH:
            instr                    = self._instr_ram[self._pc]
            self._cur_opcode         = instr.opcode
            self._repeat_remaining   = instr.repeat_cnt
            self._state              = FSMState.DECODE

        elif cur == FSMState.DECODE:
            self.gate_sel_out    = self._cur_opcode
            self._sample_cnt     = 0
            self.sample_idx_out  = 0
            if not self._is_nop(self._cur_opcode):
                self.nco_enable  = True
                self.mod_enable  = True
                self.rom_load_en = True
                self._state      = FSMState.PLAY
            else:
                self.nco_enable  = False
                self.mod_enable  = False
                self._state      = FSMState.WAIT

        elif cur == FSMState.PLAY:
            self.pulse_active    = True
            self.sample_idx_out  = self._sample_cnt
            self._sample_cnt    += 1
            if self._sample_cnt >= self._rom_depth:
                self._state = FSMState.WAIT

        elif cur == FSMState.WAIT:
            self.nco_enable = False
            self.mod_enable = False
            if self._repeat_remaining > 0:
                self._repeat_remaining -= 1
                self._sample_cnt        = 0
                self._state             = FSMState.DECODE
            elif self._pc >= self._seq_len_reg - 1:
                self._state = FSMState.DONE
            else:
                self._pc   += 1
                self._state = FSMState.FETCH

        elif cur == FSMState.DONE:
            self.seq_done = True
            self._pc      = 0
            self._state   = FSMState.IDLE

    @property
    def state(self) -> int:
        return self._state

    @property
    def current_gate(self) -> int:
        return self._cur_opcode

    @property
    def repeat_remaining(self) -> int:
        return self._repeat_remaining


# ===========================================================================
# Top-level simulation runner
# ===========================================================================
@dataclass
class SimRecord:
    cycle:            int
    rf_i:             int
    rf_q:             int
    pulse_active:     int
    fsm_state:        int
    current_gate:     int
    repeat_remaining: int
    phase_offset_en:  int


def run_simulation(
    instructions: list[Instruction],
    seq_len:      int,
    phase_inc:    int,
    phase_offset: int = 0,
    max_cycles:   int = 25000,
) -> list[SimRecord]:
    """
    Run one full sequence and return per-cycle records.
    Stops when seq_done fires or max_cycles is reached.
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    rom  = PulseROM(MEM_DIR)
    nco  = NCO()
    mod  = IQModulator()
    fsm  = SequencerFSM(rom, seq_len=seq_len)
    fsm.load_sequence(instructions, seq_len)

    records: list[SimRecord] = []
    started      = False
    finished     = False

    for cycle in range(max_cycles):
        start_pulse = (cycle == 15)   # assert start at cycle 15 (after reset)

        # Advance FSM
        fsm.clock(start=start_pulse)

        # Advance ROM (driven by FSM outputs)
        rom.clock(
            gate_sel   = fsm.gate_sel_out,
            sample_idx = fsm.sample_idx_out,
            load_en    = fsm.rom_load_en,
        )

        # Advance NCO
        nco.clock(
            phase_inc    = phase_inc,
            phase_offset = phase_offset,
            enable       = fsm.nco_enable,
        )

        # Advance IQ Modulator
        mod.clock(
            i_env  = rom.data_i,
            q_env  = rom.data_q,
            cos_c  = nco.cos_out,
            sin_c  = nco.sin_out,
            enable = fsm.mod_enable,
        )

        records.append(SimRecord(
            cycle            = cycle,
            rf_i             = mod.rf_i,
            rf_q             = mod.rf_q,
            pulse_active     = int(fsm.pulse_active),
            fsm_state        = fsm.state,
            current_gate     = fsm.current_gate,
            repeat_remaining = fsm.repeat_remaining,
            phase_offset_en  = int(phase_offset != 0),
        ))

        if fsm.seq_done:
            # Capture 100 more cycles of tail then stop
            for tail in range(100):
                fsm.clock(start=False)
                rom.clock(fsm.gate_sel_out, fsm.sample_idx_out, fsm.rom_load_en)
                nco.clock(phase_inc, phase_offset, fsm.nco_enable)
                mod.clock(rom.data_i, rom.data_q, nco.cos_out, nco.sin_out, fsm.mod_enable)
                records.append(SimRecord(
                    cycle            = cycle + tail + 1,
                    rf_i             = mod.rf_i,
                    rf_q             = mod.rf_q,
                    pulse_active     = int(fsm.pulse_active),
                    fsm_state        = fsm.state,
                    current_gate     = fsm.current_gate,
                    repeat_remaining = fsm.repeat_remaining,
                    phase_offset_en  = int(phase_offset != 0),
                ))
            break

    print(f"  Simulated {len(records)} cycles | seq_done at cycle {len(records)-100}")
    return records


def write_csv(records: list[SimRecord], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "cycle", "rf_i", "rf_q", "pulse_active",
            "fsm_state", "current_gate", "repeat_remaining", "phase_offset_en"
        ])
        for r in records:
            writer.writerow([
                r.cycle, r.rf_i, r.rf_q, r.pulse_active,
                r.fsm_state, r.current_gate, r.repeat_remaining, r.phase_offset_en
            ])
    print(f"  CSV  → {path}")


# ===========================================================================
# Plotting
# ===========================================================================

def plot_overview(all_records: list[SimRecord], out_dir: str, label: str = "") -> None:
    """Full I/Q waveform with gate shading."""
    cycles  = np.array([r.cycle       for r in all_records])
    rf_i    = np.array([r.rf_i        for r in all_records])
    rf_q    = np.array([r.rf_q        for r in all_records])
    p_act   = np.array([r.pulse_active for r in all_records])
    gates   = np.array([r.current_gate for r in all_records])

    fig, (ax_i, ax_q) = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
    title = "Python Functional Simulation — RF I/Q Output"
    if label:
        title += f"  ({label})"
    fig.suptitle(title, fontsize=13)

    for gate_id, colour in GATE_COLORS.items():
        mask = (gates == gate_id) & (p_act == 1)
        if not mask.any():
            continue
        cs = cycles[mask]
        gaps = np.where(np.diff(cs) > 2)[0] + 1
        runs = np.split(cs, gaps)
        for run in runs:
            if len(run):
                for ax in (ax_i, ax_q):
                    ax.axvspan(run[0], run[-1], alpha=0.12, color=colour)

    ax_i.plot(cycles, rf_i, linewidth=0.6, color="steelblue")
    ax_i.set_ylabel("RF_I (int16)")
    ax_i.axhline(0, color="black", lw=0.4, ls="--")
    ax_i.grid(True, alpha=0.25)

    ax_q.plot(cycles, rf_q, linewidth=0.6, color="darkorange")
    ax_q.set_ylabel("RF_Q (int16)")
    ax_q.set_xlabel("Clock cycle")
    ax_q.axhline(0, color="black", lw=0.4, ls="--")
    ax_q.grid(True, alpha=0.25)

    patches = [mpatches.Patch(color=GATE_COLORS[g], alpha=0.3,
                               label=GATE_NAMES.get(g, f"Gate {g}"))
               for g in sorted(GATE_COLORS.keys())]
    ax_i.legend(handles=patches, fontsize=7, loc="upper right")

    plt.tight_layout()
    suffix = f"_{label.replace(' ','_').replace('/','')}" if label else ""
    path = os.path.join(out_dir, f"sim_functional_overview{suffix}.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  Plot → {path}")


def plot_iq_scatter(all_records: list[SimRecord], out_dir: str, label: str = "") -> None:
    """IQ constellation coloured by gate."""
    fig, ax = plt.subplots(figsize=(6, 6))
    title = "IQ Constellation — Python Functional Sim"
    if label:
        title += f" ({label})"
    ax.set_title(title, fontsize=11)

    for gate_id in [GATE_X, GATE_Y, GATE_XH, GATE_H]:
        pts = [(r.rf_i, r.rf_q) for r in all_records
               if r.current_gate == gate_id and r.pulse_active]
        if not pts:
            continue
        i_arr = np.array([p[0] for p in pts], dtype=float)
        q_arr = np.array([p[1] for p in pts], dtype=float)
        colour = GATE_COLORS[gate_id]
        ax.plot(i_arr, q_arr, ".", markersize=1.0, color=colour, alpha=0.6,
                label=GATE_NAMES[gate_id])

    ax.set_xlabel("I amplitude")
    ax.set_ylabel("Q amplitude")
    ax.set_xlim(-35000, 35000)
    ax.set_ylim(-35000, 35000)
    ax.axhline(0, color="black", lw=0.4, ls="--")
    ax.axvline(0, color="black", lw=0.4, ls="--")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    ax.set_aspect("equal")
    plt.tight_layout()
    suffix = f"_{label.replace(' ','_').replace('/','')}" if label else ""
    path = os.path.join(out_dir, f"sim_iq_scatter{suffix}.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  Plot → {path}")


def print_stats(records: list[SimRecord], label: str = "") -> None:
    title = f"=== Gate Statistics: {label} ===" if label else "=== Gate Statistics ==="
    print(f"\n{title}")
    print(f"{'Gate':<12} {'Active cyc':>10} {'Peak |I|':>10} {'Peak |Q|':>10}")
    print("-" * 46)
    for gate_id, name in GATE_NAMES.items():
        pts = [(r.rf_i, r.rf_q) for r in records
               if r.current_gate == gate_id and r.pulse_active]
        if not pts:
            continue
        i_arr = np.array([p[0] for p in pts])
        q_arr = np.array([p[1] for p in pts])
        print(f"{name:<12} {len(pts):>10} "
              f"{int(np.abs(i_arr).max()):>10} {int(np.abs(q_arr).max()):>10}")


# ===========================================================================
# Pre-built sequences
# ===========================================================================

def seq_basic() -> tuple[list[Instruction], int]:
    """X → Y → X/2 → H → NOP"""
    instrs = [
        Instruction(GATE_X,   repeat_cnt=0),
        Instruction(GATE_Y,   repeat_cnt=0),
        Instruction(GATE_XH,  repeat_cnt=0),
        Instruction(GATE_H,   repeat_cnt=0),
        Instruction(GATE_NOP, repeat_cnt=0),
    ]
    return instrs, len(instrs)


def seq_cpmg4() -> tuple[list[Instruction], int]:
    """CPMG-4: X/2 → X×4 → X/2  (dynamical decoupling)"""
    instrs = [
        Instruction(GATE_XH, repeat_cnt=0),   # X/2 ×1
        Instruction(GATE_X,  repeat_cnt=3),    # X ×4 (repeat_cnt=3 → 1+3=4 plays)
        Instruction(GATE_XH, repeat_cnt=0),   # X/2 ×1
    ]
    return instrs, len(instrs)


def seq_hahn_echo() -> tuple[list[Instruction], int]:
    """Hahn echo: X/2 → X → X/2"""
    instrs = [
        Instruction(GATE_XH, repeat_cnt=0),
        Instruction(GATE_X,  repeat_cnt=0),
        Instruction(GATE_XH, repeat_cnt=0),
    ]
    return instrs, len(instrs)


# ===========================================================================
# Main
# ===========================================================================

def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Functional simulator for pulse_sequencer")
    parser.add_argument("--cpmg",  action="store_true", help="Run CPMG-4 sequence only")
    parser.add_argument("--hahn",  action="store_true", help="Run Hahn echo sequence only")
    parser.add_argument("--all",   action="store_true", help="Run all sequences (default)")
    args = parser.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)

    # NCO: 100 MHz carrier at 1 GHz clock
    PHASE_INC  = round(100e6 / 1e9 * (1 << PHASE_BITS))
    PHASE_90   = 1 << (PHASE_BITS - 2)  # 0x40000000 → 90°

    run_all = args.all or not (args.cpmg or args.hahn)

    # ── Sequence A: basic gate set ──────────────────────────────────────────
    if run_all or not (args.cpmg or args.hahn):
        print("\n[Sequence A] X → Y → X/2 → H → NOP")
        instrs_a, seq_len_a = seq_basic()
        recs_a = run_simulation(instrs_a, seq_len_a, PHASE_INC, phase_offset=0)
        write_csv(recs_a, os.path.join(OUT_DIR, "iq_output.csv"))
        print_stats(recs_a, "Sequence A")
        plot_overview(recs_a,    OUT_DIR, label="Seq A")
        plot_iq_scatter(recs_a,  OUT_DIR, label="Seq A")

    # ── Sequence B: CPMG-4 ─────────────────────────────────────────────────
    if run_all or args.cpmg:
        print("\n[Sequence B] CPMG-4: X/2 → X×4 → X/2 (phase_offset=90°)")
        instrs_b, seq_len_b = seq_cpmg4()
        recs_b = run_simulation(instrs_b, seq_len_b, PHASE_INC, phase_offset=PHASE_90)
        print_stats(recs_b, "Sequence B CPMG-4")
        plot_overview(recs_b,   OUT_DIR, label="Seq B CPMG-4")
        plot_iq_scatter(recs_b, OUT_DIR, label="Seq B CPMG-4")

    # ── Sequence C: Hahn echo ───────────────────────────────────────────────
    if run_all or args.hahn:
        print("\n[Sequence C] Hahn echo: X/2 → X → X/2")
        instrs_c, seq_len_c = seq_hahn_echo()
        recs_c = run_simulation(instrs_c, seq_len_c, PHASE_INC, phase_offset=0)
        print_stats(recs_c, "Sequence C Hahn Echo")
        plot_overview(recs_c,   OUT_DIR, label="Seq C Hahn")

    print("\nAll done. Outputs in sim_outputs/")


if __name__ == "__main__":
    main()
