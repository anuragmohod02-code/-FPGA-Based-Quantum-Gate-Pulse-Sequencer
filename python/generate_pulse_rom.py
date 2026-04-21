"""
generate_pulse_rom.py
=====================
Generates DRAG (Derivative Removal via Adiabatic Gate) pulse coefficients
for four quantum gate types: X, Y, X/2 (half-pi), and H (Hadamard approximation).

Reference:
  Motzoi et al., PRL 103, 110501 (2009)
  "Simple Pulses for Elimination of Leakage in Weakly Nonlinear Qubits"

DRAG pulse definition:
  I(t) = Omega(t)                           (in-phase envelope)
  Q(t) = -lambda * d(Omega)/dt / delta      (quadrature, derivative term)

  where:
    Omega(t) = Gaussian envelope
    lambda   = DRAG scaling parameter (typically 0.5)
    delta    = anharmonicity of the transmon (~-200 MHz, here normalized)

Output:
  mem_files/drag_<gate>_i.mem   -- hex encoded I samples (16-bit signed, 2's complement)
  mem_files/drag_<gate>_q.mem   -- hex encoded Q samples (16-bit signed, 2's complement)
  outputs/drag_pulses.png        -- plot of all pulses

Usage:
  python generate_pulse_rom.py
"""

import numpy as np
import matplotlib.pyplot as plt
import os

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
N_SAMPLES    = 256        # ROM depth (must match Verilog parameter ROM_DEPTH)
SAMPLE_BITS  = 16         # signed integer width
MAX_AMP      = 32767      # 2^15 - 1  (full-scale for 16-bit signed)

# DRAG parameters (dimensionless, normalised to sampling period)
LAMBDA       = 0.5        # DRAG coefficient
DELTA        = 0.2        # anharmonicity / bandwidth ratio (normalised)

# Gate-specific amplitude scale factors (relative to full-scale X pi-pulse)
GATE_PARAMS = {
    "x":   {"amp_scale": 1.0,  "phase_deg": 0.0},    # X  (pi pulse,   I axis)
    "y":   {"amp_scale": 1.0,  "phase_deg": 90.0},   # Y  (pi pulse,   Q axis)
    "xh":  {"amp_scale": 0.5,  "phase_deg": 0.0},    # X/2 (pi/2 pulse, I axis)
    "h":   {"amp_scale": 0.75, "phase_deg": 45.0},   # H  (approx, mixed axis)
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def gaussian_envelope(n_samples: int, sigma_ratio: float = 0.2) -> np.ndarray:
    """
    Returns a Gaussian pulse centred in the window.
    sigma_ratio: sigma as a fraction of n_samples.
    Truncated so end points are effectively zero.
    """
    t   = np.linspace(-1, 1, n_samples)
    sig = sigma_ratio * 2          # sigma in the [-1, 1] normalised domain
    env = np.exp(-t**2 / (2 * sig**2))
    # Zero-phase correction: subtract DC offset so pulse starts/ends at 0
    env -= env[0]
    env  = np.clip(env, 0, None)
    # Normalise to [0, 1]
    env /= env.max()
    return env


def drag_pulse(n_samples: int,
               amp_scale: float,
               phase_deg: float,
               lambda_: float = LAMBDA,
               delta: float = DELTA) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (I, Q) DRAG pulse arrays in float range [-1, 1].

    phase_deg rotates the pulse in IQ space, allowing X (0°), Y (90°), etc.
    """
    env   = gaussian_envelope(n_samples) * amp_scale

    # DRAG quadrature: Q = -(lambda / delta) * d(Omega)/dt
    # Derivative via finite difference (central differences)
    d_env = np.gradient(env)
    q_env = -(lambda_ / delta) * d_env

    # Rotate by gate phase
    phi   = np.deg2rad(phase_deg)
    i_out = env  * np.cos(phi) - q_env * np.sin(phi)
    q_out = env  * np.sin(phi) + q_env * np.cos(phi)

    # Clip to [-1, 1]
    scale = max(np.abs(i_out).max(), np.abs(q_out).max(), 1e-9)
    i_out /= scale
    q_out /= scale

    return i_out * amp_scale, q_out * amp_scale


def to_signed16(arr: np.ndarray) -> np.ndarray:
    """Scale float [-1, 1] array to 16-bit signed integers."""
    clipped = np.clip(arr, -1.0, 1.0)
    return np.round(clipped * MAX_AMP).astype(np.int16)


def to_hex_mem(arr_int16: np.ndarray) -> list[str]:
    """Convert int16 array to list of 4-digit uppercase hex strings (Verilog $readmemh)."""
    return [f"{int(v) & 0xFFFF:04X}" for v in arr_int16]


def write_mem_file(path: str, hex_list: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for line in hex_list:
            fh.write(line + "\n")
    print(f"  Wrote {len(hex_list)} entries → {path}")


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def main() -> None:
    base_dir  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mem_dir   = os.path.join(base_dir, "mem_files")
    out_dir   = os.path.join(base_dir, "outputs")
    os.makedirs(mem_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    fig, axes = plt.subplots(4, 2, figsize=(12, 10))
    fig.suptitle("DRAG Pulse Waveforms — I and Q Channels per Gate", fontsize=14)

    gate_labels = {"x": "X (π)", "y": "Y (π)", "xh": "X/2 (π/2)", "h": "H (Hadamard)"}

    for row_idx, (gate_name, params) in enumerate(GATE_PARAMS.items()):
        i_float, q_float = drag_pulse(
            n_samples  = N_SAMPLES,
            amp_scale  = params["amp_scale"],
            phase_deg  = params["phase_deg"],
        )
        i_int = to_signed16(i_float)
        q_int = to_signed16(q_float)

        # Write .mem files
        write_mem_file(os.path.join(mem_dir, f"drag_{gate_name}_i.mem"), to_hex_mem(i_int))
        write_mem_file(os.path.join(mem_dir, f"drag_{gate_name}_q.mem"), to_hex_mem(q_int))

        # Plot
        t = np.arange(N_SAMPLES)
        label = gate_labels[gate_name]

        ax_i = axes[row_idx, 0]
        ax_i.plot(t, i_int, color="steelblue", linewidth=1.2)
        ax_i.set_title(f"{label} — I channel")
        ax_i.set_ylabel("Amplitude (int16)")
        ax_i.set_xlim(0, N_SAMPLES - 1)
        ax_i.axhline(0, color="gray", linewidth=0.5, linestyle="--")
        ax_i.grid(True, alpha=0.3)

        ax_q = axes[row_idx, 1]
        ax_q.plot(t, q_int, color="darkorange", linewidth=1.2)
        ax_q.set_title(f"{label} — Q channel")
        ax_q.set_ylabel("Amplitude (int16)")
        ax_q.set_xlim(0, N_SAMPLES - 1)
        ax_q.axhline(0, color="gray", linewidth=0.5, linestyle="--")
        ax_q.grid(True, alpha=0.3)

    axes[-1, 0].set_xlabel("Sample index")
    axes[-1, 1].set_xlabel("Sample index")
    plt.tight_layout()

    plot_path = os.path.join(out_dir, "drag_pulses.png")
    plt.savefig(plot_path, dpi=150)
    plt.close()
    print(f"\nPulse plot saved → {plot_path}")
    print("\nDone. .mem files ready for Verilog $readmemh.")


if __name__ == "__main__":
    main()
