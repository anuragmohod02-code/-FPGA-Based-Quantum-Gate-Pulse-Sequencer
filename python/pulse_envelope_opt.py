#!/usr/bin/env python3
"""
pulse_envelope_opt.py — GRAPE-lite: Gradient-Based Pulse Optimisation
=======================================================================

Demonstrates GRAPE (GRadient Ascent Pulse Engineering) for single-qubit
X gate optimisation on a 3-level transmon.  Three pulse strategies are
compared:

  1. Gaussian  — naive calibrated pi-pulse; leakage to |2> at short T_gate
  2. DRAG      — first-order Derivative Removal via Adiabatic Gate (Motzoi 2009);
                 analytic Q-channel cancels leading-order leakage
  3. GRAPE     — numerical gradient ascent; fine-tunes I + Q channels jointly

Physics
-------
Rotating frame Hamiltonian (hbar = 1):

    H(t) = -delta|2><2| + Omega_I(t)(b+b†) + i*Omega_Q(t)(b†-b)

    * delta/2pi = 100 MHz  anharmonicity  (|1>->|2> detuned by delta)
    * Omega_max/2pi = 50 MHz  amplitude constraint
    * Convention: H = Omega*sigma_x (no half), so X gate needs int(Omega_I)dt = pi/2

DRAG (first-order):  Omega_Q(t) = -(dOmega_I/dt) / delta

Fidelity (Pedersen):  F = |Tr(U_target† P01 U_sim P01)|^2 / 4

Output
------
    outputs/13_grape_optimisation.png  — 6-panel figure

References
----------
Khaneja N. et al., J. Magn. Reson. 172, 296 (2005) -- GRAPE
Motzoi F. et al., PRL 103, 110501 (2009)           -- DRAG
Jurcevic P. et al., PRL 127, 160501 (2021)         -- CR GRAPE in practice
"""

import sys, os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

import numpy as np
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

# ─── Physical parameters ──────────────────────────────────────────────────────
# Time in ns, frequencies in rad/ns.  1 MHz = 2*pi * 1e-3 rad/ns.

MHz_2pi   = 2 * np.pi * 1e-3        # rad/ns per MHz

DELTA     = 100 * MHz_2pi           # anharmonicity delta/2pi = 100 MHz
OMEGA_MAX =  50 * MHz_2pi           # max drive amplitude

# 3-level ladder operators  (basis: |0>, |1>, |2>)
b    = np.zeros((3, 3), dtype=complex)
b[0, 1] = 1.0
b[1, 2] = np.sqrt(2.0)
bdag = b.conj().T

H_anhar = -DELTA * np.diag([0.0, 0.0, 1.0]).astype(complex)
H_I     = bdag + b                  # in-phase drive  (Hermitian)
H_Q     = 1j * (bdag - b)          # quadrature drive (Hermitian)

P01 = np.diag([1.0, 1.0, 0.0]).astype(complex)   # project onto {|0>,|1>}

# Target X gate in 3-level space  (Rx(pi): |0> <-> -i|1>)
U_target = np.zeros((3, 3), dtype=complex)
U_target[0, 1] = -1j
U_target[1, 0] = -1j
U_target[2, 2] =  1.0


# ─── Core functions ───────────────────────────────────────────────────────────

def hamiltonian(omega_I, omega_Q):
    return H_anhar + omega_I * H_I + omega_Q * H_Q


def forward_props(omegas_I, omegas_Q, dt):
    """
    Cumulative propagators list:
        U_fwd[0] = I
        U_fwd[k] = U_k @ ... @ U_1
    where U_k = exp(-i H_k dt).
    """
    N = len(omegas_I)
    U_fwd = [np.eye(3, dtype=complex)]
    for k in range(N):
        H_k = hamiltonian(omegas_I[k], omegas_Q[k])
        U_k = expm(-1j * H_k * dt)
        U_fwd.append(U_k @ U_fwd[-1])
    return U_fwd


def gate_fidelity(U_sim):
    """F = |Tr(U_target† P01 U_sim P01)|^2 / 4."""
    M = U_target.conj().T @ P01 @ U_sim @ P01
    return float(abs(np.trace(M)) ** 2 / 4.0)


def leakage(U_sim):
    """Average P(|2>) over |0> and |1> initial states."""
    return float((abs(U_sim[2, 0]) ** 2 + abs(U_sim[2, 1]) ** 2) / 2.0)


def drag_q_channel(gauss_I, dt):
    """First-order DRAG: Omega_Q = -(dOmega_I/dt) / delta  (Motzoi 2009)."""
    return -np.gradient(gauss_I, dt) / DELTA


# ─── FD-GRAPE ─────────────────────────────────────────────────────────────────

def grape_grad(omegas_I, omegas_Q, dt, eps=1e-4):
    """Finite-difference gradient of gate_fidelity w.r.t. all PWC amplitudes."""
    N       = len(omegas_I)
    U_fwd   = forward_props(omegas_I, omegas_Q, dt)
    F0      = gate_fidelity(U_fwd[N])
    grad_I  = np.zeros(N)
    grad_Q  = np.zeros(N)
    for k in range(N):
        tmp_I = omegas_I.copy(); tmp_I[k] += eps
        grad_I[k] = (gate_fidelity(forward_props(tmp_I, omegas_Q, dt)[N]) - F0) / eps
        tmp_Q = omegas_Q.copy(); tmp_Q[k] += eps
        grad_Q[k] = (gate_fidelity(forward_props(omegas_I, tmp_Q, dt)[N]) - F0) / eps
    return grad_I, grad_Q, F0


def run_grape(t_gate_ns, n_steps=40, n_iter=200, lr=0.02, seed=42, verbose=True):
    """
    Optimise X gate via GRAPE warm-started from Gaussian + DRAG.

    Normalised gradient update: step <= lr * Omega_max per iteration.
    Returns dict with Gaussian / DRAG / GRAPE results.
    """
    dt  = t_gate_ns / n_steps
    rng = np.random.default_rng(seed)

    # Gaussian pulse calibrated: int(Omega_I) dt = pi/2
    t_arr  = (np.arange(n_steps) + 0.5) * dt
    sigma  = 0.25 * t_gate_ns
    centre = 0.50 * t_gate_ns
    gauss  = np.exp(-0.5 * ((t_arr - centre) / sigma) ** 2)
    gauss *= (np.pi / 2.0) / (np.sum(gauss) * dt)

    # Gaussian baseline
    U_g     = forward_props(gauss, np.zeros(n_steps), dt)
    F_gauss = gate_fidelity(U_g[n_steps])
    L_gauss = leakage(U_g[n_steps])

    # DRAG baseline
    drag_Q  = drag_q_channel(gauss, dt)
    U_d     = forward_props(gauss, drag_Q, dt)
    F_drag  = gate_fidelity(U_d[n_steps])
    L_drag  = leakage(U_d[n_steps])

    # GRAPE: warm-start from Gaussian I + DRAG Q + small noise
    oI = gauss.copy()
    oQ = drag_Q.copy() + rng.normal(0, 5e-3 * OMEGA_MAX, n_steps)

    fid_hist = []
    lkg_hist = []

    for it in range(n_iter):
        oI = np.clip(oI, -OMEGA_MAX, OMEGA_MAX)
        oQ = np.clip(oQ, -OMEGA_MAX, OMEGA_MAX)

        gI, gQ, F = grape_grad(oI, oQ, dt)
        g_max = max(float(np.max(np.abs(gI))), float(np.max(np.abs(gQ))), 1e-30)
        step  = lr * OMEGA_MAX / g_max      # normalised step <= lr * Omega_max
        oI   += step * gI
        oQ   += step * gQ

        U_tmp = forward_props(oI, oQ, dt)
        L_tmp = leakage(U_tmp[n_steps])
        fid_hist.append(F)
        lkg_hist.append(L_tmp)

        if verbose and it % 50 == 0:
            print(f"     iter {it:4d}  F = {F*100:.4f}%  L = {L_tmp*100:.5f}%",
                  flush=True)

    oI = np.clip(oI, -OMEGA_MAX, OMEGA_MAX)
    oQ = np.clip(oQ, -OMEGA_MAX, OMEGA_MAX)
    U_final = forward_props(oI, oQ, dt)
    F_final = gate_fidelity(U_final[n_steps])
    L_final = leakage(U_final[n_steps])

    return dict(
        t_arr     = t_arr,
        dt        = dt,
        gauss_I   = gauss,
        drag_Q    = drag_Q,
        omegas_I  = oI,
        omegas_Q  = oQ,
        fid_hist  = np.array(fid_hist),
        lkg_hist  = np.array(lkg_hist),
        F_gauss   = F_gauss,   L_gauss  = L_gauss,
        F_drag    = F_drag,    L_drag   = L_drag,
        F_final   = F_final,   L_final  = L_final,
        n_steps   = n_steps,
        t_gate_ns = t_gate_ns,
    )


# ─── Robustness ───────────────────────────────────────────────────────────────

def fidelity_vs_detuning(omegas_I, omegas_Q, dt, errs_MHz):
    """Gate fidelity vs qubit frequency offset (adds err/2 * sigma_z)."""
    sig_z = np.diag([1.0, -1.0, 0.0]).astype(complex)
    F_arr = np.zeros(len(errs_MHz))
    N = len(omegas_I)
    for ki, err in enumerate(errs_MHz):
        e = err * MHz_2pi
        U = [np.eye(3, dtype=complex)]
        for k in range(N):
            H_k = hamiltonian(omegas_I[k], omegas_Q[k]) + (e / 2.0) * sig_z
            U.append(expm(-1j * H_k * dt) @ U[-1])
        F_arr[ki] = gate_fidelity(U[N])
    return F_arr


# ─── Main ─────────────────────────────────────────────────────────────────────

print(SEP)
print("  GRAPE-lite Pulse Optimisation  (Gaussian --> DRAG --> GRAPE)")
print(SEP)
print(f"\n  Anharmonicity  delta/2pi = {DELTA/MHz_2pi:.0f} MHz")
print(f"  Max drive   Omega_max/2pi = {OMEGA_MAX/MHz_2pi:.0f} MHz")

T_GATE  = 20.0   # ns  (Omega_max/delta = 0.5 -> leakage regime for Gaussian)
N_STEPS = 40
N_ITER  = 200

print(f"\n[1/3] GRAPE  T={T_GATE:.0f} ns  N={N_STEPS}  iter={N_ITER}  warm-start=Gaussian+DRAG")
res = run_grape(T_GATE, n_steps=N_STEPS, n_iter=N_ITER, lr=0.02, verbose=True)

print(f"\n   Gaussian  F = {res['F_gauss']*100:.4f}%   L = {res['L_gauss']*100:.4f}%")
print(f"   DRAG      F = {res['F_drag'] *100:.4f}%   L = {res['L_drag'] *100:.4f}%")
print(f"   GRAPE     F = {res['F_final']*100:.4f}%   L = {res['L_final']*100:.4f}%")
supp = res['L_gauss'] / max(res['L_final'], 1e-15)
print(f"   Leakage suppression (GRAPE vs Gaussian): {supp:.1f}x")


# ─── T_gate sweep ─────────────────────────────────────────────────────────────

print("\n[2/3] T_gate sweep ...")
T_sweep     = [15, 20, 30, 50]
F_g_arr     = np.zeros(len(T_sweep))
L_g_arr     = np.zeros(len(T_sweep))
F_d_arr     = np.zeros(len(T_sweep))
L_d_arr     = np.zeros(len(T_sweep))
F_grape_arr = np.zeros(len(T_sweep))
L_grape_arr = np.zeros(len(T_sweep))

for ki, Tg in enumerate(T_sweep):
    n_st = max(20, int(Tg * 2))
    r    = run_grape(Tg, n_steps=n_st, n_iter=100, lr=0.02, verbose=False)
    F_g_arr[ki]     = r['F_gauss'];   L_g_arr[ki]     = r['L_gauss']
    F_d_arr[ki]     = r['F_drag'];    L_d_arr[ki]     = r['L_drag']
    F_grape_arr[ki] = r['F_final'];   L_grape_arr[ki] = r['L_final']
    print(f"   T={Tg:4.0f} ns  "
          f"Gauss F={r['F_gauss']*100:.2f}% L={r['L_gauss']*100:.4f}%  "
          f"DRAG  F={r['F_drag']*100:.2f}% L={r['L_drag']*100:.4f}%  "
          f"GRAPE F={r['F_final']*100:.2f}% L={r['L_final']*100:.4f}%",
          flush=True)


# ─── Robustness ───────────────────────────────────────────────────────────────

print("\n[3/3] Robustness vs qubit frequency offset ...")
errs   = np.linspace(-15, 15, 61)
zeros_N = np.zeros(N_STEPS)
F_rob_g  = fidelity_vs_detuning(res['gauss_I'], zeros_N,        res['dt'], errs)
F_rob_d  = fidelity_vs_detuning(res['gauss_I'], res['drag_Q'],  res['dt'], errs)
F_rob_gp = fidelity_vs_detuning(res['omegas_I'], res['omegas_Q'], res['dt'], errs)

def bw_1pct(F_arr):
    mid = len(errs) // 2
    ref = F_arr[mid]
    idx = np.argmin(np.abs(F_arr - ref * 0.99))
    return abs(errs[idx])

print(f"   Gaussian 1% BW: +/-{bw_1pct(F_rob_g):.1f} MHz")
print(f"   DRAG     1% BW: +/-{bw_1pct(F_rob_d):.1f} MHz")
print(f"   GRAPE    1% BW: +/-{bw_1pct(F_rob_gp):.1f} MHz")


# ─── Plotting ─────────────────────────────────────────────────────────────────

CLR_G  = '#E67E22'   # orange  -- Gaussian
CLR_D  = '#27AE60'   # green   -- DRAG
CLR_GP = '#2980B9'   # blue    -- GRAPE
CLR_Q  = '#E74C3C'   # red     -- Q channel

fig, axes = plt.subplots(2, 3, figsize=(14, 9),
                          gridspec_kw={'hspace': 0.48, 'wspace': 0.38})
fig.suptitle(
    f"GRAPE Pulse Optimisation: X Gate on 3-Level Transmon  "
    f"(T={T_GATE:.0f} ns, delta/2pi={DELTA/MHz_2pi:.0f} MHz, "
    f"Omega_max/2pi={OMEGA_MAX/MHz_2pi:.0f} MHz)",
    fontsize=11, fontweight='bold')

ax_conv, ax_pulse, ax_lkg = axes[0]
ax_TF,   ax_TL,   ax_rob  = axes[1]

iters = np.arange(1, N_ITER + 1)

# 1 -- Convergence (infidelity)
ax_conv.plot(iters, (1 - res['fid_hist']) * 100,
             color=CLR_GP, linewidth=2, label='GRAPE 1-F')
ax_conv.axhline((1 - res['F_drag'])  * 100, color=CLR_D,  linestyle='--',
                linewidth=1.5, label=f'DRAG start  1-F={1-res["F_drag"]:.2e}')
ax_conv.axhline((1 - res['F_gauss']) * 100, color=CLR_G,  linestyle=':',
                linewidth=1.5, label=f'Gauss start 1-F={1-res["F_gauss"]:.2e}')
ax_conv.set_yscale('log')
ax_conv.set_xlabel("GRAPE iteration"); ax_conv.set_ylabel("1 - F (%)")
ax_conv.set_title("Convergence (log scale)", fontweight='bold')
ax_conv.legend(fontsize=8); ax_conv.grid(alpha=0.3, which='both')

# 2 -- Pulse shapes
t = res['t_arr']
ax_pulse.plot(t, res['gauss_I']  / MHz_2pi, color=CLR_G,  linewidth=2,
              linestyle='--', label='Gaussian Omega_I')
ax_pulse.plot(t, res['omegas_I'] / MHz_2pi, color=CLR_GP, linewidth=2,
              label='GRAPE Omega_I')
ax_pulse.plot(t, res['omegas_Q'] / MHz_2pi, color=CLR_Q,  linewidth=1.8,
              linestyle=':', label='GRAPE Omega_Q')
ax_pulse.plot(t, res['drag_Q']   / MHz_2pi, color=CLR_D,  linewidth=1.5,
              linestyle='-.', label='DRAG Omega_Q (analytic)')
for lim in [OMEGA_MAX / MHz_2pi, -OMEGA_MAX / MHz_2pi]:
    ax_pulse.axhline(lim, color='gray', linestyle=':', alpha=0.4, linewidth=1)
ax_pulse.set_xlabel("Time (ns)"); ax_pulse.set_ylabel("Drive amplitude (MHz)")
ax_pulse.set_title("Optimised Pulse Envelope", fontweight='bold')
ax_pulse.legend(fontsize=8); ax_pulse.grid(alpha=0.3)

# 3 -- Leakage during GRAPE
lkg_plot = np.maximum(res['lkg_hist'], 1e-8) * 100
ax_lkg.semilogy(iters, lkg_plot, color=CLR_GP, linewidth=2, label='GRAPE P(|2>)')
L_drag_plot  = max(res['L_drag'],  1e-8) * 100
L_gauss_plot = max(res['L_gauss'], 1e-8) * 100
ax_lkg.axhline(L_drag_plot,  color=CLR_D, linestyle='--', linewidth=1.5,
               label=f'DRAG   L={res["L_drag"]:.2e}')
ax_lkg.axhline(L_gauss_plot, color=CLR_G, linestyle=':',  linewidth=1.5,
               label=f'Gauss  L={res["L_gauss"]:.2e}')
ax_lkg.set_xlabel("GRAPE iteration"); ax_lkg.set_ylabel("Leakage P(|2>) (%)")
ax_lkg.set_title("Leakage Suppression (log scale)", fontweight='bold')
ax_lkg.legend(fontsize=8); ax_lkg.grid(alpha=0.3, which='both')

# 4 -- Fidelity vs T_gate
for vals, lbl, clr, mk, ls in [
        (F_g_arr,     'Gaussian', CLR_G,  'o', '--'),
        (F_d_arr,     'DRAG',     CLR_D,  's', '-.'),
        (F_grape_arr, 'GRAPE',    CLR_GP, '^', '-')]:
    ax_TF.plot(T_sweep, vals * 100, mk + ls, color=clr,
               linewidth=2, markersize=7, label=lbl)
ax_TF.axhline(99.0, color='black', linestyle=':', linewidth=1.2, alpha=0.5,
               label='99% target')
ax_TF.set_xlabel("Gate time (ns)"); ax_TF.set_ylabel("Gate fidelity (%)")
ax_TF.set_title("Fidelity vs Gate Duration", fontweight='bold')
ax_TF.set_ylim([50, 101]); ax_TF.legend(fontsize=9); ax_TF.grid(alpha=0.3)

# 5 -- Leakage vs T_gate
for vals, lbl, clr, mk, ls in [
        (L_g_arr,     'Gaussian', CLR_G,  'o', '--'),
        (L_d_arr,     'DRAG',     CLR_D,  's', '-.'),
        (L_grape_arr, 'GRAPE',    CLR_GP, '^', '-')]:
    ax_TL.semilogy(T_sweep, np.maximum(vals, 1e-7) * 100,
                   mk + ls, color=clr, linewidth=2, markersize=7, label=lbl)
ax_TL.set_xlabel("Gate time (ns)"); ax_TL.set_ylabel("Leakage P(|2>) (%)")
ax_TL.set_title("Leakage vs Gate Duration (log)", fontweight='bold')
ax_TL.legend(fontsize=9); ax_TL.grid(alpha=0.3, which='both')

# 6 -- Robustness
ax_rob.plot(errs, F_rob_g  * 100, color=CLR_G,  linewidth=2, label='Gaussian')
ax_rob.plot(errs, F_rob_d  * 100, color=CLR_D,  linewidth=2, linestyle='-.', label='DRAG')
ax_rob.plot(errs, F_rob_gp * 100, color=CLR_GP, linewidth=2, label='GRAPE')
ax_rob.set_xlabel("Qubit frequency offset (MHz)")
ax_rob.set_ylabel("Gate fidelity (%)")
ax_rob.set_title(f"Robustness vs Frequency Drift\n(T={T_GATE:.0f} ns)", fontweight='bold')
ax_rob.legend(fontsize=9); ax_rob.grid(alpha=0.3)
ax_rob.set_ylim([40, 101])

fig.savefig(OUT / '13_grape_optimisation.png', dpi=150, bbox_inches='tight')
print(f"\n   Saved --> {OUT / '13_grape_optimisation.png'}")

print(f"\n{SEP}")
print("  Summary")
print(SEP)
print(f"  delta/2pi = {DELTA/MHz_2pi:.0f} MHz  "
      f"Omega_max/2pi = {OMEGA_MAX/MHz_2pi:.0f} MHz  "
      f"T_gate = {T_GATE:.0f} ns")
print(f"  Gaussian  F = {res['F_gauss']*100:.4f}%   L = {res['L_gauss']*100:.4f}%")
print(f"  DRAG      F = {res['F_drag'] *100:.4f}%   L = {res['L_drag'] *100:.4f}%")
print(f"  GRAPE     F = {res['F_final']*100:.4f}%   L = {res['L_final']*100:.4f}%")
print(f"  Leakage suppression (GRAPE/Gauss): {supp:.1f}x")
print(SEP)
#!/usr/bin/env python3
"""
pulse_envelope_opt.py — GRAPE-lite: Gradient-Based Pulse Optimisation
=======================================================================

Demonstrates GRAPE (GRadient Ascent Pulse Engineering) for single-qubit
X gate optimisation.  GRAPE iteratively updates the piecewise-constant
(PWC) drive amplitudes [Ω_I(t), Ω_Q(t)] to maximise the gate fidelity.

Physics
-------
3-level transmon rotating frame:

    H(t)/ℏ = −δ|2⟩⟨2| + Ω_I(t)(b† + b) + iΩ_Q(t)(b† − b)

Goal: maximise F = |Tr(U†_target U_final)|² / (n²)
      where U_target = Rx(π) = −i·X  (ideal X gate on {|0⟩,|1⟩})

Algorithm (GRAPE, Khaneja et al. 2005)
---------------------------------------
For a PWC pulse with N time steps of duration Δt:

1. Forward propagate: U_k = exp(−i H_k Δt), U_1k = U_k ··· U_1
2. Compute overlap:  Φ = Tr(U†_target P_01 U_1N P_01)
3. Backward propagate: P_k = U†_k P_{k+1} U_k
4. Gradient:  dΦ/dΩ_j(k) = Tr(P_{k+1}† [i Δt dH/dΩ_j] U_k λ_k)
              (simplified: finite-difference for robustness)
5. Update:   Ω_j(k) ← Ω_j(k) + α × dF/dΩ_j(k)

We use finite differences for gradient calculation (ε-step) to keep the
implementation clean and transparent.  This is slower than analytic GRAPE
but identical in the optimal solution, and illustrates the technique cleanly.

Optimisation targets
--------------------
1. Gaussian initial pulse → GRAPE-optimised pulse  (leakage comparison)
2. Sweep T_gate from 10–100 ns: minimum leakage achievable vs gate duration
3. Robustness: fidelity vs δ_error (qubit frequency offset) for Gaussian vs GRAPE

Output
------
    outputs/13_grape_optimisation.png  — convergence + pulse comparison + leakage

References
----------
Khaneja N. et al., J. Magn. Reson. 172, 296 (2005) — original GRAPE
Motzoi F. et al., PRL 103, 110501 (2009)           — DRAG
Jurcevic P. et al., PRL 127, 160501 (2021)         — CR GRAPE in practice
"""

import sys, os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

import numpy as np
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

# ── Physical parameters ───────────────────────────────────────────────────────
# Working in dimensionless units: time normalised to T_gate, frequency in rad/T.
# A more convenient parameterisation:
#   t in ns, Ω in MHz×2π (= rad/µs = rad × 1e-3 / ns)
#   DELTA (anharmonicity) = 200 MHz → δ = 200 × 2π × 1e-3 rad/ns

MHz_2pi = 2 * np.pi * 1e-3   # rad/ns per MHz

DELTA    =  200 * MHz_2pi    # anharmonicity (magnitude), rad/ns
OMEGA_MAX = 50 * MHz_2pi     # max drive amplitude constraint (50 MHz)

# 3-level ladder operators (|0⟩,|1⟩,|2⟩)
b = np.zeros((3, 3), dtype=complex)
b[0, 1] = 1.0
b[1, 2] = np.sqrt(2)
bdag = b.conj().T

# Anharmonicity diagonal
H_anhar = -DELTA * np.array([[0, 0, 0],
                               [0, 0, 0],
                               [0, 0, 1]], dtype=complex)

# Drive operators
H_I = bdag + b         # In-phase (real)
H_Q = 1j*(bdag - b)   # Quadrature (imag)

# Projection operators onto computational subspace {|0⟩,|1⟩}
P01 = np.zeros((3, 3), dtype=complex)
P01[0, 0] = 1.0
P01[1, 1] = 1.0

# Ideal X gate (π rotation about X axis in {|0⟩,|1⟩})
U_target_full = np.zeros((3, 3), dtype=complex)
U_target_full[0, 1] = -1j   # Rx(π): |1⟩ → −i|0⟩
U_target_full[1, 0] = -1j   # Rx(π): |0⟩ → −i|1⟩
U_target_full[2, 2] =  1.0  # |2⟩ unaffected (leakage should stay 0)


# ── Utility functions ─────────────────────────────────────────────────────────

def hamiltonian(omega_I: float, omega_Q: float) -> np.ndarray:
    """Full 3-level Hamiltonian at a single time step."""
    return H_anhar + omega_I * H_I + omega_Q * H_Q


def propagator(omega_I: float, omega_Q: float, dt: float) -> np.ndarray:
    """Single-step propagator U = exp(−i H dt)."""
    H = hamiltonian(omega_I, omega_Q)
    return expm(-1j * H * dt)


def forward_props(omegas_I, omegas_Q, dt):
    """
    Compute all single-step propagators and cumulative propagators.

    Returns
    -------
    U_steps  : list of N (3,3) step propagators
    U_fwd    : list of N+1 cumulative propagators U_fwd[k] = U_k ··· U_1
               U_fwd[0] = I,  U_fwd[N] = U_total
    """
    N = len(omegas_I)
    U_steps = [propagator(omegas_I[k], omegas_Q[k], dt) for k in range(N)]
    U_fwd = [np.eye(3, dtype=complex)]
    for U in U_steps:
        U_fwd.append(U @ U_fwd[-1])
    return U_steps, U_fwd


def gate_fidelity(U_final: np.ndarray) -> float:
    """
    Average gate fidelity in the computational subspace.

    F = |Tr(U†_target P01 U_final P01)|² / (n_c²)
    where n_c = 2 (computational subspace dimension).
    """
    M = U_target_full.conj().T @ P01 @ U_final @ P01
    return float(np.abs(np.trace(M)) ** 2 / 4.0)


def leakage(U_final: np.ndarray) -> float:
    """P(|2⟩) after X gate on |0⟩ and |1⟩, averaged."""
    state_0 = U_final[:, 0]
    state_1 = U_final[:, 1]
    return float((np.abs(state_0[2]) ** 2 + np.abs(state_1[2]) ** 2) / 2.0)


# ── GRAPE algorithm ───────────────────────────────────────────────────────────

def grape_step_fd(omegas_I, omegas_Q, dt, epsilon=1e-5):
    """
    One GRAPE gradient step using finite differences.

    Returns gradient arrays (dF/dI, dF/dQ) of shape (N,).
    """
    N  = len(omegas_I)
    _, U_fwd = forward_props(omegas_I, omegas_Q, dt)
    F0 = gate_fidelity(U_fwd[N])

    grad_I = np.zeros(N)
    grad_Q = np.zeros(N)

    for k in range(N):
        # +ε step for I
        omegas_I_p = omegas_I.copy(); omegas_I_p[k] += epsilon
        _, U_fwd_p = forward_props(omegas_I_p, omegas_Q, dt)
        grad_I[k] = (gate_fidelity(U_fwd_p[N]) - F0) / epsilon

        # +ε step for Q
        omegas_Q_p = omegas_Q.copy(); omegas_Q_p[k] += epsilon
        _, U_fwd_p = forward_props(omegas_I, omegas_Q_p, dt)
        grad_Q[k] = (gate_fidelity(U_fwd_p[N]) - F0) / epsilon

    return grad_I, grad_Q, F0


def run_grape(t_gate_ns: float,
              n_steps:    int   = 50,
              n_iter:     int   = 300,
              lr:         float = 0.5,
              seed:       int   = 42,
              verbose:    bool  = True) -> dict:
    """
    Run GRAPE pulse optimisation for X gate.

    Parameters
    ----------
    t_gate_ns : gate duration (ns)
    n_steps   : number of PWC time steps
    n_iter    : gradient ascent iterations
    lr        : learning rate (rad/ns per gradient unit)
    seed      : random seed for initial pulse

    Returns
    -------
    dict with optimised pulse, fidelity history, Gaussian comparison
    """
    dt = t_gate_ns / n_steps
    rng = np.random.default_rng(seed)

    # Initialise with small Gaussian pulse + small noise
    t_arr = (np.arange(n_steps) + 0.5) * dt
    sigma = 0.2 * t_gate_ns / 2
    centre = t_gate_ns / 2
    gauss  = np.exp(-0.5 * ((t_arr - centre) / sigma) ** 2)

    # Calibrate Gaussian to achieve X gate:
    # H = Ω_I (b†+b) → in qubit subspace H = Ω_I σ_x  (no factor of ½)
    # U = exp(-i ∫Ω dt σ_x), so X gate requires ∫Ω_I dt = π/2
    gauss_area = np.sum(gauss) * dt
    gauss_norm = gauss * (np.pi / 2 / gauss_area)   # calibrated: ∫Ω dt = π/2

    # Gaussian baseline (pure Gaussian, no GRAPE)
    _, U_fwd_g = forward_props(gauss_norm, np.zeros(n_steps), dt)
    F_gauss    = gate_fidelity(U_fwd_g[n_steps])
    L_gauss    = leakage(U_fwd_g[n_steps])

    # GRAPE starts from small random amplitudes so convergence is visible
    omegas_I = rng.normal(0, 0.08 * OMEGA_MAX, n_steps)
    omegas_Q = rng.normal(0, 0.08 * OMEGA_MAX, n_steps)

    # GRAPE loop — use normalised-gradient update so lr is scale-invariant:
    #   step = lr * OMEGA_MAX * grad / max(|grad|)
    # This means each iteration moves amplitudes by at most lr*OMEGA_MAX.
    fidelity_hist = []
    leakage_hist  = []

    for it in range(n_iter):
        # Clip to amplitude constraint
        omegas_I = np.clip(omegas_I, -OMEGA_MAX, OMEGA_MAX)
        omegas_Q = np.clip(omegas_Q, -OMEGA_MAX, OMEGA_MAX)

        grad_I, grad_Q, F = grape_step_fd(omegas_I, omegas_Q, dt)
        g_max = max(np.max(np.abs(grad_I)), np.max(np.abs(grad_Q)), 1e-30)
        step = lr * OMEGA_MAX / g_max
        omegas_I = omegas_I + step * grad_I
        omegas_Q = omegas_Q + step * grad_Q

        _, U_tmp = forward_props(omegas_I, omegas_Q, dt)
        L = leakage(U_tmp[n_steps])

        fidelity_hist.append(F)
        leakage_hist.append(L)

        if verbose and it % 50 == 0:
            print(f"     iter {it:4d}  F = {F*100:.4f}%  L = {L*100:.5f}%",
                  flush=True)

    # Final state
    omegas_I = np.clip(omegas_I, -OMEGA_MAX, OMEGA_MAX)
    omegas_Q = np.clip(omegas_Q, -OMEGA_MAX, OMEGA_MAX)
    _, U_fwd_final = forward_props(omegas_I, omegas_Q, dt)
    F_final = gate_fidelity(U_fwd_final[n_steps])
    L_final = leakage(U_fwd_final[n_steps])

    return {
        't_arr':         t_arr,
        'dt':            dt,
        'omegas_I':      omegas_I,
        'omegas_Q':      omegas_Q,
        'gauss_I':       gauss_norm,
        'fidelity_hist': np.array(fidelity_hist),
        'leakage_hist':  np.array(leakage_hist),
        'F_gauss':       F_gauss,
        'L_gauss':       L_gauss,
        'F_final':       F_final,
        'L_final':       L_final,
        'U_final':       U_fwd_final[n_steps],
        'n_steps':       n_steps,
        't_gate_ns':     t_gate_ns,
    }


# ── Robustness sweep ──────────────────────────────────────────────────────────

def fidelity_vs_detuning(omegas_I, omegas_Q, dt, delta_errors_MHz):
    """
    Compute gate fidelity as qubit frequency drifts by ε.

    Adds H_err = ε/2 · σ_z to Hamiltonian.
    """
    sigma_z3 = np.diag([1.0, -1.0, 0.0]).astype(complex)  # Z in 3-level space
    F_arr = np.zeros(len(delta_errors_MHz))
    for ki, err in enumerate(delta_errors_MHz):
        err_rad_ns = err * MHz_2pi
        N = len(omegas_I)
        U_fwd = [np.eye(3, dtype=complex)]
        for k in range(N):
            H_k = hamiltonian(omegas_I[k], omegas_Q[k]) + (err_rad_ns / 2) * sigma_z3
            U_k = expm(-1j * H_k * dt)
            U_fwd.append(U_k @ U_fwd[-1])
        F_arr[ki] = gate_fidelity(U_fwd[N])
    return F_arr


# ── Main simulation ───────────────────────────────────────────────────────────

print(SEP)
print("  GRAPE-lite Pulse Optimisation")
print(SEP)
print(f"\n  Anharmonicity δ/2π = {DELTA/MHz_2pi:.0f} MHz")
print(f"  Max drive Ω_max/2π = {OMEGA_MAX/MHz_2pi:.0f} MHz")

T_GATE = 50.0    # ns — 50 ns is aggressive (δ/Ω ~ 4×)
N_STEPS = 20     # PWC steps
N_ITER  = 150

print(f"\n[1/3] GRAPE optimisation (T={T_GATE:.0f} ns, N={N_STEPS}, iter={N_ITER})…")
res = run_grape(T_GATE, n_steps=N_STEPS, n_iter=N_ITER, lr=0.5, verbose=True)

print(f"\n   Gaussian (no DRAG):  F = {res['F_gauss']*100:.4f}%  L = {res['L_gauss']*1e4:.2f}×10⁻⁴")
print(f"   GRAPE optimised :   F = {res['F_final']*100:.4f}%  L = {res['L_final']*1e4:.2f}×10⁻⁴")
print(f"   Leakage suppression: {res['L_gauss']/max(res['L_final'], 1e-12):.1f}×")


# T_gate sweep
print("\n[2/3] Gate time sweep: F and L vs T_gate…")

T_sweep = [30, 50, 80]
F_gauss_arr = np.zeros(len(T_sweep))
L_gauss_arr = np.zeros(len(T_sweep))
F_grape_arr = np.zeros(len(T_sweep))
L_grape_arr = np.zeros(len(T_sweep))

for ki, T_g in enumerate(T_sweep):
    n_st  = max(16, int(T_g * 0.5))
    r_tmp = run_grape(T_g, n_steps=n_st, n_iter=100, lr=0.4, verbose=False)
    F_gauss_arr[ki] = r_tmp['F_gauss']
    L_gauss_arr[ki] = r_tmp['L_gauss']
    F_grape_arr[ki] = r_tmp['F_final']
    L_grape_arr[ki] = r_tmp['L_final']
    print(f"   T={T_g:4.0f} ns  Gauss: F={r_tmp['F_gauss']*100:.2f}% L={r_tmp['L_gauss']*1e4:.1f}e-4"
          f"  GRAPE: F={r_tmp['F_final']*100:.2f}% L={r_tmp['L_final']*1e4:.1f}e-4", flush=True)


# Robustness (for T=50 ns pulse)
print("\n[3/3] Robustness: F vs qubit detuning error…")

delta_errors = np.linspace(-10, 10, 60)   # MHz
F_rob_gauss = fidelity_vs_detuning(
    res['gauss_I'], np.zeros(N_STEPS), res['dt'], delta_errors)
F_rob_grape = fidelity_vs_detuning(
    res['omegas_I'], res['omegas_Q'], res['dt'], delta_errors)

print(f"   Gaussian 1% F drop at: ±{delta_errors[np.argmin(np.abs(F_rob_gauss - F_rob_gauss[len(delta_errors)//2]*0.99))]:.1f} MHz")
print(f"   GRAPE    1% F drop at: ±{delta_errors[np.argmin(np.abs(F_rob_grape - F_rob_grape[len(delta_errors)//2]*0.99))]:.1f} MHz")


# ── Plotting ──────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(2, 3, figsize=(14, 9),
                          gridspec_kw={'hspace': 0.45, 'wspace': 0.38})
fig.suptitle(f"GRAPE-lite: Optimal Pulse Envelope for X Gate  "
             f"(T={T_GATE:.0f} ns, δ/2π={DELTA/MHz_2pi:.0f} MHz)",
             fontsize=13, fontweight='bold')

ax_conv   = axes[0, 0]
ax_pulse  = axes[0, 1]
ax_leak_c = axes[0, 2]
ax_Tswp_F = axes[1, 0]
ax_Tswp_L = axes[1, 1]
ax_rob    = axes[1, 2]

# Convergence history
iters = np.arange(1, N_ITER + 1)
ax_conv.plot(iters, (1 - res['fidelity_hist']) * 100, color='#2980B9', linewidth=2,
              label='Gate infidelity 1−F')
ax_conv.set_yscale('log')
ax_conv.axhline((1 - res['F_gauss']) * 100, color='#E67E22', linestyle='--',
                linewidth=1.5, label=f"Gaussian baseline\n1−F={1-res['F_gauss']:.2e}")
ax_conv.set_xlabel("GRAPE iteration"); ax_conv.set_ylabel("1 − F (%)")
ax_conv.set_title("GRAPE Convergence\n(log scale)", fontweight='bold')
ax_conv.legend(fontsize=8.5); ax_conv.grid(alpha=0.3, which='both')

# Pulse shapes
t_arr_us = res['t_arr']
ax_pulse.plot(t_arr_us, res['gauss_I'] / MHz_2pi, color='#E67E22', linewidth=2,
               linestyle='--', label='Gaussian Ω_I')
ax_pulse.plot(t_arr_us, res['omegas_I'] / MHz_2pi, color='#2980B9', linewidth=2,
               label='GRAPE Ω_I')
ax_pulse.plot(t_arr_us, res['omegas_Q'] / MHz_2pi, color='#E74C3C', linewidth=1.8,
               linestyle=':', label='GRAPE Ω_Q')
ax_pulse.axhline( OMEGA_MAX / MHz_2pi, color='gray', linestyle=':', alpha=0.5, linewidth=1)
ax_pulse.axhline(-OMEGA_MAX / MHz_2pi, color='gray', linestyle=':', alpha=0.5, linewidth=1)
ax_pulse.set_xlabel("Time (ns)"); ax_pulse.set_ylabel("Drive amplitude (MHz)")
ax_pulse.set_title("Optimised vs Gaussian\nPulse Envelope", fontweight='bold')
ax_pulse.legend(fontsize=8.5); ax_pulse.grid(alpha=0.3)

# Leakage convergence
ax_leak_c.semilogy(iters, np.maximum(res['leakage_hist'], 1e-8) * 100,
                   color='#9B59B6', linewidth=2, label='Leakage P(|2⟩)')
ax_leak_c.axhline(res['L_gauss'] * 100, color='#E67E22', linestyle='--', linewidth=1.5,
                   label=f"Gaussian baseline\nL={res['L_gauss']:.2e}")
ax_leak_c.set_xlabel("GRAPE iteration"); ax_leak_c.set_ylabel("Leakage (%)")
ax_leak_c.set_title("Leakage Suppression\n(log scale)", fontweight='bold')
ax_leak_c.legend(fontsize=8.5); ax_leak_c.grid(alpha=0.3, which='both')

# T_gate sweep — Fidelity
ax_Tswp_F.plot(T_sweep, F_gauss_arr * 100, 'o--', color='#E67E22', linewidth=2,
                markersize=7, label='Gaussian')
ax_Tswp_F.plot(T_sweep, F_grape_arr * 100, 's-', color='#2980B9', linewidth=2,
                markersize=7, label='GRAPE')
ax_Tswp_F.axhline(99.0, color='black', linestyle=':', linewidth=1.2, alpha=0.4,
                   label='99% target')
# (T_lim lines removed — T_sweep=[30,50,80] is too short to annotate reliably)
ax_Tswp_F.set_xlabel("Gate time (ns)"); ax_Tswp_F.set_ylabel("Gate fidelity (%)")
ax_Tswp_F.set_title("Fidelity vs Gate Duration\n(3-level leakage limited)", fontweight='bold')
ax_Tswp_F.legend(fontsize=9); ax_Tswp_F.grid(alpha=0.3)
ax_Tswp_F.set_ylim([70, 101])

# T_gate sweep — Leakage
ax_Tswp_L.semilogy(T_sweep, np.maximum(L_gauss_arr, 1e-8) * 100, 'o--', color='#E67E22',
                    linewidth=2, markersize=7, label='Gaussian')
ax_Tswp_L.semilogy(T_sweep, np.maximum(L_grape_arr, 1e-8) * 100, 's-', color='#2980B9',
                    linewidth=2, markersize=7, label='GRAPE')
ax_Tswp_L.set_xlabel("Gate time (ns)"); ax_Tswp_L.set_ylabel("Leakage P(|2⟩) (%)")
ax_Tswp_L.set_title("Leakage vs Gate Duration\n(log scale)", fontweight='bold')
ax_Tswp_L.legend(fontsize=9); ax_Tswp_L.grid(alpha=0.3, which='both')

# Robustness
ax_rob.plot(delta_errors, F_rob_gauss * 100, color='#E67E22', linewidth=2,
             label='Gaussian')
ax_rob.plot(delta_errors, F_rob_grape * 100, color='#2980B9', linewidth=2,
             label='GRAPE')
ax_rob.set_xlabel("Qubit frequency offset ε (MHz)")
ax_rob.set_ylabel("Gate fidelity (%)")
ax_rob.set_title(f"Robustness vs Frequency Drift\n(T={T_GATE:.0f} ns)", fontweight='bold')
ax_rob.legend(fontsize=9); ax_rob.grid(alpha=0.3)
ax_rob.set_ylim([50, 101])

fig.savefig(OUT / '13_grape_optimisation.png', dpi=150, bbox_inches='tight')
print(f"\n   Saved → {OUT/'13_grape_optimisation.png'}")

print(f"\n{SEP}")
print("  Summary")
print(SEP)
print(f"  T_gate = {T_GATE:.0f} ns  (δ/Ω_max ≈ {DELTA/OMEGA_MAX:.1f})")
print(f"  Gaussian pulse : F = {res['F_gauss']*100:.4f}%  L = {res['L_gauss']*1e4:.2f}×10⁻⁴")
print(f"  GRAPE pulse    : F = {res['F_final']*100:.4f}%  L = {res['L_final']*1e4:.2f}×10⁻⁴")
print(f"  Infidelity reduction: {(1-res['F_gauss'])/(max(1-res['F_final'], 1e-10)):.1f}×")
print(f"  Leakage suppression : {res['L_gauss']/max(res['L_final'], 1e-12):.1f}×")
print(SEP)
