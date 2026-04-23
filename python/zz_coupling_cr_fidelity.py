#!/usr/bin/env python3
"""
zz_coupling_cr_fidelity.py — ZZ coupling and Cross-Resonance gate fidelity
===========================================================================

In a two-transmon architecture connected through a coupler (or direct capacitive
coupling), the always-on ZZ interaction causes a qubit-state-dependent frequency
shift.  This contaminates Cross-Resonance (CR) gates, reducing gate fidelity.

Physics
-------
Two transmons (Q0, Q1) with coupling g.  In the dressed basis the static ZZ
coupling constant is (to second order in g):

    ξ_ZZ = −2 g² α / [(Δ)(Δ+α)]

where:
    Δ = ω_q1 − ω_q0   (detuning)
    α = anharmonicity of Q0 (α ≈ −200 MHz, same for both)

The ZZ interaction shifts the |11⟩ energy:

    H_ZZ/ℏ = (ξ_ZZ / 2) |1⟩⟨1|₀ ⊗ |1⟩⟨1|₁

Cross-Resonance (CR) gate
--------------------------
The CR gate is driven at ω_d = ω_q1 (target) while controlling Q0.
In the dressed two-qubit frame, the CR Hamiltonian is approximately:

    H_CR(t)/ℏ = Ω(t)/2 · ZX  +  ξ_ZZ/2 · ZZ  +  εI · IX

where Ω(t) is the CR drive envelope and εI is a parasitic IX term.
The εI term is mitigated by active cancellation (AC) tone.

What we compute
---------------
1. Static ZZ ξ_ZZ vs detuning Δ (for several coupling g values)
2. CR gate unitary (numerical ODE) for a Gaussian-envelope CR pulse
3. Average gate fidelity F_avg = [Tr(U†_ideal · U_sim) · conj + n²] / (n²+n)
4. F_avg vs (Ω_peak, T_gate) 2D sweep at fixed ZZ
5. F_avg vs detuning Δ (with and without AC cancellation tone)
6. Spectator qubit ZZ phase accumulation during single-qubit gates

Output
------
    outputs/11_zz_coupling.png       — ξ_ZZ vs Δ for several g
    outputs/12_cr_gate_fidelity.png  — 2D fidelity heatmap + 1D sweeps

References
----------
Magesan & Gambetta, PRA 101, 052308 (2020)  — CR gate theory
Rigetti & Devoret, PRA 81, 042326 (2010)     — ZZ coupling derivation
Sheldon et al., PRA 93, 060302(R) (2016)     — CR calibration
Mundada et al., PRApplied 12, 054023 (2019)  — ZZ suppression
"""

import sys, os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

import numpy as np
from scipy.integrate import solve_ivp
from scipy.linalg import expm
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

OUT = Path(ROOT) / 'outputs'
OUT.mkdir(exist_ok=True)
SEP = "=" * 60

plt.rcParams.update({
    'figure.dpi':        150,
    'font.size':         10,
    'axes.titlesize':    11,
    'axes.labelsize':    10,
    'legend.fontsize':   9,
    'axes.spines.top':   False,
    'axes.spines.right': False,
    'lines.linewidth':   2.0,
})

# ── Physical constants (all in units of 2π × MHz) ────────────────────────────
# Work in the rotating frame; times are in ns, frequencies in 2π×MHz = rad/µs
# Here 1 unit of Ω = 2π × 1 MHz = 1 rad/µs if t is in µs.
# Choose t in ns → Ω in rad/ns, frequencies in rad/ns.
# 2π × 1 MHz = 2π × 1e-3 GHz = 2π × 1 rad/µs = 2π/1000 rad/ns
MHz_per_rad_ns = 2 * np.pi * 1e-3   # 1 MHz → rad/ns

ALPHA    = -200  * MHz_per_rad_ns   # anharmonicity (rad/ns), ≈−200 MHz
OMEGA_Q0 =    5.0e3 * MHz_per_rad_ns  # Q0 = 5.0 GHz (not needed, we work in detuning frame)
DELTA_vals = np.linspace(100, 700, 300) * MHz_per_rad_ns   # detuning Δ/2π: 100–700 MHz
G_vals     = [10, 20, 50, 80, 120]  # coupling g/2π in MHz

# ── 1. ZZ coupling ξ_ZZ vs detuning ─────────────────────────────────────────

def zz_coupling(g_MHz, delta_MHz, alpha_MHz=-200.0):
    """
    Second-order perturbation theory ZZ coupling (MHz).

    ξ_ZZ / 2π = -2 g² α / [(Δ)(Δ + α)]

    Returns ξ_ZZ in MHz.
    """
    return -2 * g_MHz**2 * alpha_MHz / (delta_MHz * (delta_MHz + alpha_MHz))


print(SEP)
print("  ZZ Coupling + CR Gate Fidelity Simulation")
print(SEP)

# ── 2. CR gate simulation ─────────────────────────────────────────────────────
# Two-qubit Hilbert space |q0 q1⟩, basis: {|00⟩,|01⟩,|10⟩,|11⟩}
# H_ZZ/ℏ = ξ_ZZ/4 · ZZ  (phase convention matching dressed-basis ZZ)
# H_CR(t)/ℏ = Ω(t)/2 · ZX
# H_IX     = εI/2 · IX  (parasitic, mitigated by AC tone)
# Ideal CX unitary: U_CX = exp(-i π/4 ZX)

I2 = np.eye(2, dtype=complex)
X  = np.array([[0, 1], [1, 0]], dtype=complex)
Z  = np.array([[1, 0], [0, -1]], dtype=complex)
Y  = np.array([[0, -1j], [1j, 0]], dtype=complex)

ZX = np.kron(Z, X)   # 4×4
ZZ = np.kron(Z, Z)   # 4×4
IX = np.kron(I2, X)
IZ = np.kron(I2, Z)
ZI = np.kron(Z, I2)


def gaussian_envelope(t_ns, t_gate_ns, omega_peak, sigma_frac=0.2):
    """Gaussian pulse centred at T/2 with σ = sigma_frac × T/2."""
    sigma = sigma_frac * t_gate_ns / 2
    centre = t_gate_ns / 2
    return omega_peak * np.exp(-0.5 * ((t_ns - centre) / sigma) ** 2)


def simulate_cr_gate(t_gate_ns, omega_peak_ns, zz_MHz, include_AC=False,
                     epsilon_I_MHz=0.0, n_steps=500):
    """
    Simulate CR gate unitary numerically.

    Parameters
    ----------
    t_gate_ns    : total gate time (ns)
    omega_peak_ns: peak drive Ω/2 (rad/ns)
    zz_MHz       : ξ_ZZ in MHz (converted to rad/ns internally)
    include_AC   : subtract IX cancellation tone if True
    epsilon_I_MHz: parasitic IX amplitude (MHz)

    Returns
    -------
    U : (4, 4) complex unitary matrix
    F_avg : average gate fidelity vs ideal CX
    """
    zz = zz_MHz * MHz_per_rad_ns        # rad/ns
    eps = epsilon_I_MHz * MHz_per_rad_ns

    t_span = (0.0, t_gate_ns)
    t_eval = np.linspace(0.0, t_gate_ns, n_steps)

    # Flatten (4,4) → (16,) for ODE
    U0 = np.eye(4, dtype=complex).flatten()

    def hamiltonian(t):
        omega_t = gaussian_envelope(t, t_gate_ns, omega_peak_ns)
        H = (omega_t / 2) * ZX
        H += (zz / 4) * ZZ
        if not include_AC:
            H += (eps / 2) * IX
        return H

    def odefunc(t, u_flat):
        H = hamiltonian(t)
        U = u_flat.reshape(4, 4)
        dU = -1j * H @ U
        return dU.flatten()

    sol  = solve_ivp(odefunc, t_span, U0, t_eval=t_eval,
                     method='RK45', rtol=1e-8, atol=1e-10)
    U    = sol.y[:, -1].reshape(4, 4)

    # Ideal CX: exp(-i π/4 · ZX) up to global phase
    U_ideal = expm(-1j * np.pi / 4 * ZX)

    # Average gate fidelity (Pedersen et al., Phys. Lett. A 367, 47)
    n = 4
    M    = U_ideal.conj().T @ U
    F_avg = (np.abs(np.trace(M)) ** 2 + n) / (n * (n + 1))
    return U, float(F_avg.real)


# Calibrate omega_peak → CR rotation angle = π/2
# For a flat pulse: Ω × T/2 = π/2 → Ω = π/T
# For Gaussian (area = Ω_peak × σ × sqrt(2π)): Ω_peak = π/(σ√(2π))
# We just choose omega_peak such that the flat-pulse CX would be exact,
# then let the Gaussian under/overshoot slightly.

def omega_peak_for_cx(t_gate_ns, sigma_frac=0.2):
    """Gaussian peak amplitude to achieve ∫Ω/2 dt = π/4 (ZX rotation of π/4).

    H = Ω(t)/2 ZX  ⇒  U = exp(-i π/4 ZX)  when ∫Ω/2 dt = π/4
    ⇒ Ω_peak such that Ω_peak × (√(2π) σ) / 2 = π/4
    ⇒ Ω_peak = π/2 / (√(2π) σ)
    """
    sigma = sigma_frac * t_gate_ns / 2
    area_half = np.sqrt(2 * np.pi) * sigma / 2   # ∫Ω(t)/2 dt
    return (np.pi / 4) / area_half   # rad/ns


# Parameter sweep: t_gate vs ZZ coupling
print("\n[1/4] ZZ coupling vs detuning…")

delta_MHz_arr = DELTA_vals / MHz_per_rad_ns   # back to MHz for display

fig1, axes1 = plt.subplots(1, 2, figsize=(11, 5))
fig1.suptitle("ZZ Coupling: Always-On Qubit-Qubit Interaction",
               fontsize=13, fontweight='bold')

ax_zz_abs = axes1[0]
ax_zz_log = axes1[1]

colors_g = plt.cm.plasma(np.linspace(0.1, 0.85, len(G_vals)))

for g_MHz, col in zip(G_vals, colors_g):
    zz_arr = [zz_coupling(g_MHz, d, alpha_MHz=-200) for d in delta_MHz_arr]
    ax_zz_abs.plot(delta_MHz_arr, zz_arr, color=col, linewidth=2,
                   label=f"g/2π = {g_MHz} MHz")
    ax_zz_log.semilogy(delta_MHz_arr, np.abs(zz_arr), color=col, linewidth=2,
                        label=f"g/2π = {g_MHz} MHz")

# Annotate typical ZZ values
for delta_marker, txt in [(200, "Δ=200 MHz"), (400, "Δ=400 MHz")]:
    ax_zz_abs.axvline(delta_marker, color='black', linestyle=':', alpha=0.4, linewidth=1.2)
    ax_zz_log.axvline(delta_marker, color='black', linestyle=':', alpha=0.4, linewidth=1.2)

# Marker for ZZ = 50 kHz (typical tolerable budget for surface code)
ax_zz_log.axhline(0.05, color='#E74C3C', linestyle='--', linewidth=1.5, alpha=0.8,
                   label='50 kHz budget')
ax_zz_log.text(650, 0.055, '50 kHz', color='#E74C3C', fontsize=9)

ax_zz_abs.set_xlabel("Detuning Δ/2π (MHz)"); ax_zz_abs.set_ylabel("ξ_ZZ/2π (MHz)")
ax_zz_abs.set_title("ξ_ZZ vs Detuning\n(2nd-order PT: ξ_ZZ=−2g²α/[Δ(Δ+α)])",
                     fontweight='bold')
ax_zz_abs.legend(fontsize=8.5); ax_zz_abs.grid(alpha=0.3)
ax_zz_abs.set_ylim(-3, 0)

ax_zz_log.set_xlabel("Detuning Δ/2π (MHz)"); ax_zz_log.set_ylabel("|ξ_ZZ|/2π (MHz, log)")
ax_zz_log.set_title("|ξ_ZZ| vs Detuning (log scale)", fontweight='bold')
ax_zz_log.legend(fontsize=8.5); ax_zz_log.grid(alpha=0.3, which='both')

plt.tight_layout()
fig1.savefig(OUT / '11_zz_coupling.png', dpi=150, bbox_inches='tight')
print(f"   ξ_ZZ(g=50, Δ=300) = {zz_coupling(50, 300):.3f} MHz")
print(f"   ξ_ZZ(g=50, Δ=500) = {zz_coupling(50, 500):.3f} MHz")
print(f"   Saved → {OUT/'11_zz_coupling.png'}")


# ── 3. CR gate fidelity sweep ─────────────────────────────────────────────────
print("\n[2/4] CR gate fidelity vs gate time and ZZ coupling…")

# ZZ values to sweep over (MHz): representative range
ZZ_sweep_MHz = np.array([0.0, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0])
# Gate times (ns)
T_gate_sweep = np.linspace(80, 600, 20)
# Use calibrated omega_peak per gate time

F_vs_T_per_ZZ = np.zeros((len(ZZ_sweep_MHz), len(T_gate_sweep)))

for j, zz in enumerate(ZZ_sweep_MHz):
    for k, T_g in enumerate(T_gate_sweep):
        om = omega_peak_for_cx(T_g)
        _, F = simulate_cr_gate(T_g, om, zz)
        F_vs_T_per_ZZ[j, k] = F
    print(f"   ZZ={zz:.2f} MHz done", flush=True)


print("\n[3/4] CR fidelity vs detuning (g=20 MHz)…")
G_DEMO = 20.0   # MHz — realistic bare coupling for state-of-the-art transmons
DETUNING_sweep = np.linspace(150, 700, 40)   # MHz

T_GATE_DEMO = 200.0  # ns — reasonable CR gate duration

F_vs_delta_no_AC = np.zeros(len(DETUNING_sweep))
F_vs_delta_AC    = np.zeros(len(DETUNING_sweep))

for k, d_MHz in enumerate(DETUNING_sweep):
    zz_val = zz_coupling(G_DEMO, d_MHz)
    eps_val = abs(zz_val) * 0.15    # parasitic IX ~ 15% of ZZ (rough estimate)
    om = omega_peak_for_cx(T_GATE_DEMO)
    _, F_no_ac = simulate_cr_gate(T_GATE_DEMO, om, zz_val,
                                   include_AC=False, epsilon_I_MHz=eps_val)
    _, F_ac    = simulate_cr_gate(T_GATE_DEMO, om, zz_val,
                                   include_AC=True,  epsilon_I_MHz=eps_val)
    F_vs_delta_no_AC[k] = F_no_ac
    F_vs_delta_AC[k]    = F_ac
    if k % 8 == 0:
        print(f"   Δ/2π={d_MHz:.0f} MHz  ξ_ZZ={zz_val:.3f} MHz  "
              f"F_no_AC={F_no_ac:.4f}  F_AC={F_ac:.4f}", flush=True)


# ── 4. Plot CR gate results ───────────────────────────────────────────────────
print("\n[4/4] Plotting CR gate fidelity figures…")

fig2, axes2 = plt.subplots(2, 2, figsize=(12, 10),
                            gridspec_kw={'hspace': 0.40, 'wspace': 0.35})
fig2.suptitle("Cross-Resonance Gate Fidelity: ZZ Coupling Limits",
               fontsize=13, fontweight='bold')

ax_heat  = axes2[0, 0]
ax_1d_T  = axes2[0, 1]
ax_delta = axes2[1, 0]
ax_spec  = axes2[1, 1]

# 2D heatmap: F vs (T_gate, ZZ)
F_pct = F_vs_T_per_ZZ * 100
im = ax_heat.pcolormesh(T_gate_sweep, ZZ_sweep_MHz, F_pct,
                         cmap='RdYlGn', vmin=50, vmax=100, shading='auto')
plt.colorbar(im, ax=ax_heat, label='Avg gate fidelity F (%)')

# 99% contour
cs = ax_heat.contour(T_gate_sweep, ZZ_sweep_MHz, F_pct,
                      levels=[99.0], colors='white', linewidths=1.8, linestyles='--')
ax_heat.clabel(cs, fmt='99%%', fontsize=9)

ax_heat.set_xlabel("Gate time T_gate (ns)")
ax_heat.set_ylabel("ZZ coupling |ξ_ZZ|/2π (MHz)")
ax_heat.set_title("CR Gate Fidelity vs T_gate and ξ_ZZ\n"
                   "(Gaussian envelope, calibrated Ω_peak)", fontweight='bold')
ax_heat.grid(alpha=0.15, color='white')

# 1D slices: F vs T_gate for several ZZ values
colors_zz = plt.cm.RdYlGn_r(np.linspace(0.1, 0.85, len(ZZ_sweep_MHz)))
for j, (zz, col) in enumerate(zip(ZZ_sweep_MHz, colors_zz)):
    lbl = f"ξ_ZZ=0" if zz == 0 else f"ξ_ZZ={zz:.2f} MHz"
    ax_1d_T.plot(T_gate_sweep, F_vs_T_per_ZZ[j] * 100, color=col,
                  linewidth=2, label=lbl)
ax_1d_T.axhline(99.0, color='black', linestyle=':', linewidth=1.5, alpha=0.5,
                label='F = 99%')
ax_1d_T.set_xlabel("Gate time T_gate (ns)")
ax_1d_T.set_ylabel("Avg gate fidelity (%)")
ax_1d_T.set_title("F vs Gate Duration\n(ZZ is always-on during gate)", fontweight='bold')
ax_1d_T.legend(fontsize=8, ncol=2); ax_1d_T.grid(alpha=0.3)

# F vs detuning (with and without AC cancellation)
ax_delta.plot(DETUNING_sweep,
              [zz_coupling(G_DEMO, d) for d in DETUNING_sweep],
              color='#9B59B6', linewidth=2, linestyle='--',
              label='ξ_ZZ/2π (right axis)')
ax_d2 = ax_delta.twinx()
ax_d2.plot(DETUNING_sweep, F_vs_delta_no_AC * 100, 'o-', color='#E74C3C',
            linewidth=2, markersize=5, label='No AC cancellation')
ax_d2.plot(DETUNING_sweep, F_vs_delta_AC * 100, 's-', color='#2ECC71',
            linewidth=2, markersize=5, label='With AC cancellation')
ax_d2.axhline(99.0, color='black', linestyle=':', linewidth=1.2, alpha=0.4)
ax_delta.set_xlabel("Detuning Δ/2π (MHz)")
ax_delta.set_ylabel("ξ_ZZ/2π (MHz)", color='#9B59B6')
ax_delta.tick_params(axis='y', labelcolor='#9B59B6')
ax_d2.set_ylabel("Avg gate fidelity (%)")
ax_delta.set_title(f"F vs Detuning (g={G_DEMO:.0f} MHz, T={T_GATE_DEMO:.0f} ns)\n"
                    f"Effect of AC cancellation tone", fontweight='bold')
lines1, labels1 = ax_delta.get_legend_handles_labels()
lines2, labels2 = ax_d2.get_legend_handles_labels()
ax_delta.legend(lines1 + lines2, labels1 + labels2, fontsize=8.5, loc='lower right')
ax_delta.grid(alpha=0.3)

# Spectator ZZ phase panel: during a single-qubit gate (X gate, T_1Q ns)
# a spectator qubit at Δ accumulates phase φ = ξ_ZZ × T_1Q
T_1Q_sweep_ns = np.linspace(10, 100, 200)   # single-qubit gate duration
ZZ_spectator = [0.05, 0.1, 0.2, 0.5, 1.0]  # MHz
colors_sp = plt.cm.plasma(np.linspace(0.1, 0.85, len(ZZ_spectator)))
for zz, col in zip(ZZ_spectator, colors_sp):
    zz_rad_ns = zz * MHz_per_rad_ns
    phi = zz_rad_ns * T_1Q_sweep_ns / 2  # rad (factor ½ for ZZ convention)
    phi_deg = np.degrees(phi)
    ax_spec.plot(T_1Q_sweep_ns, phi_deg, color=col, linewidth=2,
                  label=f"ξ_ZZ={zz:.2f} MHz")
ax_spec.axhline(0.5, color='#E74C3C', linestyle='--', linewidth=1.5, alpha=0.8,
                label='0.5° error budget')
ax_spec.set_xlabel("Single-qubit gate duration (ns)")
ax_spec.set_ylabel("Spectator ZZ phase φ (°)")
ax_spec.set_title("Spectator Error: ZZ Phase During\nSingle-Qubit Gate", fontweight='bold')
ax_spec.legend(fontsize=8.5, ncol=2); ax_spec.grid(alpha=0.3)

fig2.savefig(OUT / '12_cr_gate_fidelity.png', dpi=150, bbox_inches='tight')
print(f"   Saved → {OUT/'12_cr_gate_fidelity.png'}")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  Summary")
print(SEP)
print(f"  ξ_ZZ(g=50 MHz, Δ=300 MHz) = {zz_coupling(50, 300):.3f} MHz")
print(f"  ξ_ZZ(g=50 MHz, Δ=500 MHz) = {zz_coupling(50, 500):.3f} MHz")
print(f"  ξ_ZZ(g=20 MHz, Δ=300 MHz) = {zz_coupling(20, 300):.3f} MHz")
print(f"  ξ_ZZ(g=20 MHz, Δ=500 MHz) = {zz_coupling(20, 500):.3f} MHz")
print(f"\n  CR gate fidelity @ T_gate=200 ns:")
for j, zz in enumerate(ZZ_sweep_MHz):
    idx = np.searchsorted(T_gate_sweep, 200.0)
    print(f"    ξ_ZZ = {zz:.2f} MHz → F = {F_vs_T_per_ZZ[j, idx]*100:.2f}%")
print(f"\n  Fidelity vs detuning (g=20 MHz, T=200 ns, best Δ):")
best_no_ac = DETUNING_sweep[np.argmax(F_vs_delta_no_AC)]
best_ac    = DETUNING_sweep[np.argmax(F_vs_delta_AC)]
print(f"    Optimal Δ (no AC) = {best_no_ac:.0f} MHz  F = {np.max(F_vs_delta_no_AC)*100:.2f}%")
print(f"    Optimal Δ (with AC)= {best_ac:.0f} MHz  F = {np.max(F_vs_delta_AC)*100:.2f}%")
print(SEP)
