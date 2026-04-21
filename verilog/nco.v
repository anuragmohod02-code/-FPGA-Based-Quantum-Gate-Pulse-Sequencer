// =============================================================================
// nco.v  —  Numerically Controlled Oscillator
// =============================================================================
// Generates a continuous-wave carrier at a programmable frequency using a
// 32-bit phase accumulator.  Phase-to-amplitude conversion uses a 1024-entry
// sine/cosine LUT, providing ~60 dB SFDR suitable for qubit drive simulation.
//
// Carrier frequency:
//   f_out = (phase_inc / 2^32) * f_clk
//   Example: f_clk=1 GHz, f_out=100 MHz → phase_inc = round(100e6/1e9 * 2^32)
//                                                     = 429_496_730
//
// Phase offset:
//   A static 32-bit additive offset is applied to the accumulated phase before
//   the LUT lookup.  This shifts the carrier phase without changing frequency:
//     phase_offset = 0x00000000 →  0°
//     phase_offset = 0x40000000 → 90°
//     phase_offset = 0x80000000 → 180°
//   Applications: SSB sideband selection, IQ calibration, Ramsey experiment.
//
// Ports:
//   clk          - system clock
//   rst_n        - active-low synchronous reset (zeros phase accumulator)
//   enable       - when high, accumulator runs; when low, outputs held at zero
//   phase_inc    - 32-bit phase increment word (determines output frequency)
//   phase_offset - 32-bit static phase offset (added before LUT lookup)
//   cos_out      - 16-bit signed cosine (I carrier)
//   sin_out      - 16-bit signed sine   (Q carrier)
// =============================================================================

`timescale 1ns / 1ps

module nco #(
    parameter PHASE_BITS = 32,     // accumulator width
    parameter LUT_BITS   = 10,     // log2 of LUT size → 1024 entries
    parameter OUT_BITS   = 16      // output amplitude width (signed)
)(
    input  wire                  clk,
    input  wire                  rst_n,
    input  wire                  enable,
    input  wire [PHASE_BITS-1:0] phase_inc,
    input  wire [PHASE_BITS-1:0] phase_offset,   // static phase shift before LUT
    output reg  [OUT_BITS-1:0]   cos_out,
    output reg  [OUT_BITS-1:0]   sin_out
);

    // -------------------------------------------------------------------------
    // Sine/cosine LUT (1024 entries, values in [-32767, +32767])
    // Populated at elaboration via $sin/$cos system functions.
    // -------------------------------------------------------------------------
    localparam LUT_DEPTH = 1 << LUT_BITS;   // 1024
    localparam signed [OUT_BITS-1:0] AMP = 32767;

    reg signed [OUT_BITS-1:0] lut_sin [0:LUT_DEPTH-1];
    reg signed [OUT_BITS-1:0] lut_cos [0:LUT_DEPTH-1];

    integer i;
    real    two_pi;
    initial begin
        two_pi = 2.0 * 3.14159265358979323846;
        for (i = 0; i < LUT_DEPTH; i = i + 1) begin
            lut_sin[i] = $rtoi($sin(two_pi * i / LUT_DEPTH) * 32767.0);
            lut_cos[i] = $rtoi($cos(two_pi * i / LUT_DEPTH) * 32767.0);
        end
    end

    // -------------------------------------------------------------------------
    // Phase accumulator
    // -------------------------------------------------------------------------
    reg [PHASE_BITS-1:0] phase_acc;

    always @(posedge clk) begin
        if (!rst_n) begin
            phase_acc <= {PHASE_BITS{1'b0}};
        end else if (enable) begin
            phase_acc <= phase_acc + phase_inc;
        end
    end

    // -------------------------------------------------------------------------
    // LUT address = top LUT_BITS of (phase_acc + phase_offset)
    // Adding phase_offset rotates the carrier without changing frequency.
    // -------------------------------------------------------------------------
    wire [PHASE_BITS-1:0] lut_phase = phase_acc + phase_offset;
    wire [LUT_BITS-1:0]   lut_addr  = lut_phase[PHASE_BITS-1 : PHASE_BITS-LUT_BITS];

    always @(posedge clk) begin
        if (!rst_n || !enable) begin
            cos_out <= {OUT_BITS{1'b0}};
            sin_out <= {OUT_BITS{1'b0}};
        end else begin
            cos_out <= lut_cos[lut_addr];
            sin_out <= lut_sin[lut_addr];
        end
    end

endmodule
