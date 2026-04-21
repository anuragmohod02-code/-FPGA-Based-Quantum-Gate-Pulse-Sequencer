# FPGA-Based Quantum Gate Pulse Sequencer

A fully synthesisable Verilog design that emulates an Arbitrary Waveform Generator (AWG)
for superconducting qubit control. The sequencer reads a programmed gate list and plays
back pre-computed DRAG-shaped microwave pulses at precise nanosecond timing.

## What This Demonstrates

| Skill area | Evidence |
|---|---|
| Quantum hardware knowledge | DRAG pulse theory (Motzoi et al. 2009), IQ modulation, gate opcode encoding |
| Digital design | FSM-driven control, parameterised Verilog, synchronous design practices |
| FPGA workflow | Vivado XSim behavioural simulation, waveform export, resource estimates |
| Python / DSP | NumPy DRAG coefficient generation, `.mem` hex export, matplotlib waveform analysis |

---

## Hardware Target

- **Device:** Xilinx Artix-7 `xc7a35t-csg324` (free WebPACK licence)
- **Estimated resources:** ~4 DSP48 slices (multipliers), 1× 18k BRAM, ~400 FF/LUT

---

## Repository Structure

```
Project1_FPGA_PulseSequencer/
├── verilog/
│   ├── pulse_rom.v              # DRAG envelope ROM (256 samples × 5 gates)
│   ├── nco.v                    # 32-bit phase accumulator NCO + 1024-pt LUT
│   ├── gate_decoder.v           # 3-bit opcode → control signals
│   ├── pulse_sequencer_fsm.v   # Main FSM (IDLE/FETCH/DECODE/PLAY/WAIT/DONE)
│   ├── iq_modulator.v           # SSB IQ modulation (3-stage pipelined multiplier)
│   ├── top_pulse_sequencer.v   # Top-level integration
│   └── tb_pulse_sequencer.v    # Testbench — runs X→Y→X/2→H→NOP sequence
├── python/
│   ├── generate_pulse_rom.py   # DRAG pulse math → .mem hex files + plots
│   └── plot_sim_output.py      # Parse XSim CSV → waveform/phase-portrait plots
├── mem_files/
│   ├── drag_x_i.mem   drag_x_q.mem
│   ├── drag_y_i.mem   drag_y_q.mem
│   ├── drag_xh_i.mem  drag_xh_q.mem
│   └── drag_h_i.mem   drag_h_q.mem
├── sim_outputs/
│   ├── drag_pulses.png          # Python-generated DRAG envelope preview
│   ├── iq_output.csv            # XSim simulation data
│   ├── iq_full_sequence.png     # Full RF I/Q waveform across all gates
│   ├── iq_per_gate.png          # Zoomed per-gate waveforms
│   └── iq_phase_portrait.png    # IQ trajectory plot
└── README.md
```

---

## Quick Start

### Step 1 — Generate DRAG pulse ROM files

```powershell
cd Project1_FPGA_PulseSequencer
pip install numpy scipy matplotlib
python python/generate_pulse_rom.py
```

This creates `mem_files/drag_*.mem` (loaded by Verilog `$readmemh`) and
`sim_outputs/drag_pulses.png` (visual sanity-check of the pulse shapes).

### Step 2 — Create Vivado Project

1. Open **Vivado 2023.x → Create Project**
2. Project name: `pulse_sequencer`, location: this folder
3. Part: `xc7a35t-csg324-1`
4. **Add Sources → Add Files** → select all `verilog/*.v` files
5. Set `top_pulse_sequencer` as design top (Sources pane → right-click → Set as Top)
6. Set `tb_pulse_sequencer` as simulation top (Sources → Simulation Sources pane)

### Step 3 — Run Behavioural Simulation

In Vivado TCL console:

```tcl
# Create sim_outputs directory first
file mkdir {../sim_outputs}

# Launch and run
launch_simulation
run 12us
close_sim
```

Or via GUI: **Flow → Run Simulation → Run Behavioral Simulation → Run All**

### Step 4 — Plot Results

```powershell
python python/plot_sim_output.py
```

---

## Architecture

```
         seq_wr_en / seq_wr_addr / seq_wr_data
                        │
                        ▼
            ┌───────────────────────┐
            │  Instruction RAM      │  16 × 3-bit gate opcodes
            └──────────┬────────────┘
                       │
                       ▼
            ┌───────────────────────┐        ┌──────────────────┐
   start ──►│  pulse_sequencer_fsm  │──────►│   gate_decoder   │
            │  (IDLE/FETCH/DECODE/  │        │  (combinatorial) │
            │   PLAY/WAIT/DONE)     │        └──────────────────┘
            └──────┬──────┬─────────┘
                   │      │
           gate_sel│  nco_enable
           sample_idx     │
                   │      ▼
                   │  ┌────────┐     cos_out
                   │  │  NCO   │────────────────────────────┐
                   │  └────────┘     sin_out                 │
                   │                                         │
                   ▼                                         ▼
            ┌──────────────┐                      ┌──────────────────┐
            │  pulse_rom   │──── data_i ──────────►  iq_modulator    │──► rf_i_out
            │  (DRAG LUT)  │──── data_q ──────────►  (I×cos − Q×sin) │──► rf_q_out
            └──────────────┘                      └──────────────────┘
```

---

## DRAG Pulse Theory

Standard Gaussian pulses on weakly anharmonic qubits (transmons) cause leakage
to the |2⟩ level. DRAG adds a derivative-based quadrature component to cancel
this leakage:

```
I(t) = Ω(t)                            ← Gaussian envelope
Q(t) = −λ · dΩ/dt / δ                 ← Derivative, scaled by anharmonicity δ
```

where λ ≈ 0.5 (empirically optimised) and δ is the qubit anharmonicity (~−200 MHz).

Different gates are encoded by rotating the IQ frame:

| Gate | Phase rotation | Amplitude scale |
|---|---|---|
| X (π) | 0° | 1.0 |
| Y (π) | 90° | 1.0 |
| X/2 (π/2) | 0° | 0.5 |
| H (Hadamard) | 45° | 0.75 |

---

## Gate Opcodes

```
3'b000 = NOP   (no pulse)
3'b001 = X     (π pulse, I axis)
3'b010 = Y     (π pulse, Q axis)
3'b011 = XH    (π/2 pulse, X/2)
3'b100 = H     (Hadamard approximation)
3'b101..111    = Reserved → treated as NOP
```

---

## Simulation Results

### DRAG Pulse Envelopes (Python-generated)

![DRAG Pulse Envelopes](outputs/drag_pulses.png)

*Pre-computed DRAG I/Q envelopes for all 4 gate types. The Q channel carries the derivative term that cancels leakage to |2⟩.*

### Gate Sequence A: X → Y → X/2 → H → NOP

![Sequence A Waveform](sim_outputs/sim_functional_overview_Seq_A.png)

*Full I/Q output waveform from the Python behavioral simulator. Each gate occupies 256 clock cycles (~64 ns at 4 GHz effective sample rate).*

### Sequence B: CPMG-4 Dynamical Decoupling

![CPMG-4 Waveform](sim_outputs/sim_functional_overview_Seq_B_CPMG-4.png)

*CPMG-4 sequence — X/2 → X×4 → X/2 — demonstrating the FSM repeat counter. The X gate plays 4 consecutive times (1024 active cycles) from a single 8-bit instruction word.*

### IQ Phase Portrait — Sequence A

![IQ Scatter Sequence A](sim_outputs/sim_iq_scatter_Seq_A.png)

*IQ trajectory plot. X gate traces along I axis, Y along Q axis, H at 45° — confirming correct SSB modulator rotation for each gate.*

**Key results at a glance:**
- **X gate:** Gaussian-DRAG envelope on I channel; derivative notch on Q; 0° carrier
- **Y gate:** 90° rotated — appears predominantly on Q channel
- **X/2 gate:** Half amplitude vs X (π/2 pulse)
- **H gate:** Mixed I and Q at 45°
- **CPMG-4:** 4× repeat loop from single instruction → 1024 active cycles confirmed

---

## References

1. Motzoi F. et al., *Simple Pulses for Elimination of Leakage in Weakly Nonlinear Qubits*, PRL **103**, 110501 (2009)
2. Krantz P. et al., *A quantum engineer's guide to superconducting qubits*, Appl. Phys. Rev. **6**, 021318 (2019)
3. Xilinx UG900 — *Vivado Design Suite User Guide: Logic Simulation*
