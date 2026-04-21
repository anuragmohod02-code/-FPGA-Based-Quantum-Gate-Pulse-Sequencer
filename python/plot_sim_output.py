"""
plot_sim_output.py
==================
Reads the XSim simulation CSV output (sim_outputs/iq_output.csv) and
produces publication-quality plots for the project portfolio.

Generated figures (saved to sim_outputs/):
  1. iq_full_sequence.png  — full I and Q waveforms across the entire gate sequence
  2. iq_per_gate.png       — zoomed subplot per gate (X, Y, X/2, H, NOP)
  3. iq_phase_portrait.png — IQ phase portrait (Q vs I) coloured by gate

Usage:
  python plot_sim_output.py
  (Run from the Project1_FPGA_PulseSequencer/ root, or the python/ subfolder)
"""

import os
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import LineCollection

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CSV_PATH    = os.path.join(PROJECT_DIR, "sim_outputs", "iq_output.csv")
OUT_DIR     = os.path.join(PROJECT_DIR, "sim_outputs")

GATE_NAMES  = {0: "NOP", 1: "X (π)", 2: "Y (π)", 3: "X/2 (π/2)", 4: "H"}
GATE_COLORS = {0: "gray", 1: "steelblue", 2: "darkorange", 3: "seagreen", 4: "mediumpurple"}

# ---------------------------------------------------------------------------
# Load CSV
# ---------------------------------------------------------------------------

def load_csv(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        print(f"ERROR: CSV file not found at:\n  {path}")
        print("Run the Vivado XSim simulation first to generate iq_output.csv")
        sys.exit(1)

    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    print(f"Loaded {len(df):,} rows from {path}")
    print(f"Columns : {list(df.columns)}")
    print(f"Cycles  : {df['cycle'].min()} → {df['cycle'].max()}")
    print(f"Gates   : {sorted(df['current_gate'].unique())}")
    return df


# ---------------------------------------------------------------------------
# Plot 1: Full sequence I/Q waveforms
# ---------------------------------------------------------------------------

def plot_full_sequence(df: pd.DataFrame, out_dir: str) -> None:
    fig, (ax_i, ax_q) = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
    fig.suptitle("Full Gate Sequence — RF I/Q Output (XSim Simulation)", fontsize=13)

    # Shade active-pulse regions per gate
    for gate_id, colour in GATE_COLORS.items():
        mask = (df["current_gate"] == gate_id) & (df["pulse_active"] == 1)
        if not mask.any():
            continue
        cycles = df.loc[mask, "cycle"].values
        # Find contiguous runs
        gaps   = np.where(np.diff(cycles) > 2)[0] + 1
        runs   = np.split(cycles, gaps)
        for run in runs:
            if len(run) == 0:
                continue
            for ax in (ax_i, ax_q):
                ax.axvspan(run[0], run[-1], alpha=0.12, color=colour)

    ax_i.plot(df["cycle"], df["rf_i"], linewidth=0.6, color="steelblue", label="RF_I")
    ax_i.set_ylabel("Amplitude (int16)")
    ax_i.legend(loc="upper right", fontsize=8)
    ax_i.axhline(0, color="black", linewidth=0.4, linestyle="--")
    ax_i.grid(True, alpha=0.25)

    ax_q.plot(df["cycle"], df["rf_q"], linewidth=0.6, color="darkorange", label="RF_Q")
    ax_q.set_ylabel("Amplitude (int16)")
    ax_q.set_xlabel("Clock cycle")
    ax_q.legend(loc="upper right", fontsize=8)
    ax_q.axhline(0, color="black", linewidth=0.4, linestyle="--")
    ax_q.grid(True, alpha=0.25)

    # Legend patches for gate shading
    patches = [mpatches.Patch(color=GATE_COLORS[g], alpha=0.3,
                               label=GATE_NAMES.get(g, f"Gate {g}"))
               for g in sorted(GATE_COLORS.keys())]
    ax_i.legend(handles=patches + [plt.Line2D([0], [0], color="steelblue", lw=1, label="RF_I")],
                fontsize=7, loc="upper left")

    plt.tight_layout()
    path = os.path.join(out_dir, "iq_full_sequence.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved → {path}")


# ---------------------------------------------------------------------------
# Plot 2: Per-gate zoomed subplots
# ---------------------------------------------------------------------------

def plot_per_gate(df: pd.DataFrame, out_dir: str) -> None:
    active_gates = [g for g in [1, 2, 3, 4, 0]
                    if g in df["current_gate"].values]

    n_gates = len(active_gates)
    fig, axes = plt.subplots(n_gates, 2, figsize=(12, 3 * n_gates))
    if n_gates == 1:
        axes = axes[np.newaxis, :]
    fig.suptitle("Per-Gate DRAG Pulse I/Q Waveforms (Zoomed)", fontsize=13)

    for row, gate_id in enumerate(active_gates):
        gate_df  = df[df["current_gate"] == gate_id].copy()
        gate_df  = gate_df[gate_df["pulse_active"] == 1]

        if gate_df.empty:
            # NOP — just show flat zero
            gate_df = df[df["current_gate"] == gate_id].head(300)

        colour = GATE_COLORS.get(gate_id, "black")
        label  = GATE_NAMES.get(gate_id, f"Gate {gate_id}")
        cycles = gate_df["cycle"].values - gate_df["cycle"].values[0]  # zero-based

        ax_i = axes[row, 0]
        ax_q = axes[row, 1]

        ax_i.plot(cycles, gate_df["rf_i"].values, linewidth=1.0, color=colour)
        ax_i.set_title(f"{label} — I channel", fontsize=9)
        ax_i.set_ylabel("Amplitude", fontsize=8)
        ax_i.axhline(0, color="gray", linewidth=0.4, linestyle="--")
        ax_i.grid(True, alpha=0.25)

        ax_q.plot(cycles, gate_df["rf_q"].values, linewidth=1.0, color=colour)
        ax_q.set_title(f"{label} — Q channel", fontsize=9)
        ax_q.axhline(0, color="gray", linewidth=0.4, linestyle="--")
        ax_q.grid(True, alpha=0.25)

    axes[-1, 0].set_xlabel("Sample within gate")
    axes[-1, 1].set_xlabel("Sample within gate")

    plt.tight_layout()
    path = os.path.join(out_dir, "iq_per_gate.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved → {path}")


# ---------------------------------------------------------------------------
# Plot 3: IQ phase portrait
# ---------------------------------------------------------------------------

def plot_phase_portrait(df: pd.DataFrame, out_dir: str) -> None:
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.set_title("IQ Phase Portrait — Gate Trajectories", fontsize=12)

    for gate_id in [1, 2, 3, 4]:
        gate_df = df[(df["current_gate"] == gate_id) & (df["pulse_active"] == 1)]
        if gate_df.empty:
            continue
        i_vals = gate_df["rf_i"].values.astype(float)
        q_vals = gate_df["rf_q"].values.astype(float)
        colour = GATE_COLORS.get(gate_id, "black")
        label  = GATE_NAMES.get(gate_id, f"Gate {gate_id}")

        # Colour gradient along trajectory (time progression)
        points = np.array([i_vals, q_vals]).T.reshape(-1, 1, 2)
        segs   = np.concatenate([points[:-1], points[1:]], axis=1)
        lc     = LineCollection(segs, colors=[colour], linewidths=0.8, alpha=0.7)
        ax.add_collection(lc)
        ax.plot(i_vals[0],  q_vals[0],  "o", color=colour, markersize=5)
        ax.plot(i_vals[-1], q_vals[-1], "s", color=colour, markersize=5,
                label=f"{label} (start=○ end=■)")

    ax.set_xlabel("I amplitude")
    ax.set_ylabel("Q amplitude")
    ax.set_xlim(-35000, 35000)
    ax.set_ylim(-35000, 35000)
    ax.axhline(0, color="black", linewidth=0.4, linestyle="--")
    ax.axvline(0, color="black", linewidth=0.4, linestyle="--")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8, loc="upper right")
    ax.set_aspect("equal")

    plt.tight_layout()
    path = os.path.join(out_dir, "iq_phase_portrait.png")
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved → {path}")


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def print_summary(df: pd.DataFrame) -> None:
    print("\n=== Gate Summary ===")
    print(f"{'Gate':<12} {'Cycles':>8} {'Max |I|':>10} {'Max |Q|':>10} {'RMS I':>10} {'RMS Q':>10}")
    print("-" * 62)
    for gate_id, name in GATE_NAMES.items():
        g = df[(df["current_gate"] == gate_id) & (df["pulse_active"] == 1)]
        if g.empty:
            continue
        i_vals = g["rf_i"].values.astype(float)
        q_vals = g["rf_q"].values.astype(float)
        print(f"{name:<12} {len(g):>8} {np.abs(i_vals).max():>10.0f} "
              f"{np.abs(q_vals).max():>10.0f} "
              f"{np.sqrt(np.mean(i_vals**2)):>10.1f} "
              f"{np.sqrt(np.mean(q_vals**2)):>10.1f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    df = load_csv(CSV_PATH)

    print_summary(df)
    plot_full_sequence(df, OUT_DIR)
    plot_per_gate(df, OUT_DIR)
    plot_phase_portrait(df, OUT_DIR)

    print("\nAll plots saved to sim_outputs/")


if __name__ == "__main__":
    main()
