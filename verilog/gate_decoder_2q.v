// =============================================================================
// gate_decoder_2q.v
// =============================================================================
// Decodes 4-bit two-qubit gate opcodes.
//
// Opcode map (4 bits → two-channel control):
//
//  Single-qubit gates (qubit 0 only, Q1 idles):
//    4'b0000 = NOP          — no operation
//    4'b0001 = X0           — pi pulse on Q0 (I axis)
//    4'b0010 = Y0           — pi pulse on Q0 (Q axis)
//    4'b0011 = X0H          — pi/2 pulse on Q0
//    4'b0100 = H0           — Hadamard on Q0
//
//  Single-qubit gates (qubit 1 only, Q0 idles):
//    4'b0101 = X1           — pi pulse on Q1
//    4'b0110 = Y1           — pi pulse on Q1
//    4'b0111 = X1H          — pi/2 pulse on Q1
//    4'b1000 = H1           — Hadamard on Q1
//
//  Two-qubit gates (both channels + coupling tone):
//    4'b1001 = CNOT         — CNOT: Q0 control, Q1 target
//                             Implemented as: X1H → CZ → X1H (cross-resonance style)
//                             Q0 plays cross-resonance tone (gate_sel0 = CNOT_CR = 3'b101)
//                             Q1 plays target pi/2 corrections (gate_sel1 = X1H = 3'b011)
//    4'b1010 = CZ           — controlled-Z: iSWAP-like parametric coupling
//                             Q0 plays CZ flux pulse (gate_sel0 = CZ0 = 3'b110)
//                             Q1 plays CZ target correction (gate_sel1 = CZ1 = 3'b111)
//    4'b1011..1111 = reserved
//
// ROM gate_sel encoding (3 bits, matches pulse_rom.v base addresses):
//   3'b000 = NOP (zeros)
//   3'b001 = X / X0 / X1
//   3'b010 = Y / Y0 / Y1
//   3'b011 = XH (pi/2)
//   3'b100 = H
//   3'b101 = CNOT_CR  (cross-resonance tone on Q0)
//   3'b110 = CZ_Q0    (CZ flux/parametric pulse on Q0)
//   3'b111 = CZ_Q1    (CZ correction on Q1)
//
// Outputs:
//   pulse_en_q0    — enable pulse playback on channel 0
//   pulse_en_q1    — enable pulse playback on channel 1
//   gate_sel_q0    — 3-bit ROM selector for channel 0
//   gate_sel_q1    — 3-bit ROM selector for channel 1
//   is_two_qubit   — asserted for CNOT/CZ (both channels active simultaneously)
//   gate_valid     — opcode is a defined gate
// =============================================================================

`timescale 1ns / 1ps

module gate_decoder_2q #(
    parameter ROM_DEPTH = 256
)(
    input  wire [3:0]  opcode,
    output reg         pulse_en_q0,
    output reg         pulse_en_q1,
    output reg  [2:0]  gate_sel_q0,
    output reg  [2:0]  gate_sel_q1,
    output reg         is_two_qubit,
    output reg         gate_valid
);

    always @(*) begin
        // Defaults: both channels idle, NOP ROM slot
        pulse_en_q0  = 1'b0;
        pulse_en_q1  = 1'b0;
        gate_sel_q0  = 3'b000;   // NOP
        gate_sel_q1  = 3'b000;   // NOP
        is_two_qubit = 1'b0;
        gate_valid   = 1'b1;

        case (opcode)
            // ── Single-qubit on Q0 ──────────────────────────────────────────
            4'b0000: begin  // NOP
                pulse_en_q0 = 1'b0;
                pulse_en_q1 = 1'b0;
            end
            4'b0001: begin  // X0
                pulse_en_q0 = 1'b1;
                gate_sel_q0 = 3'b001;
            end
            4'b0010: begin  // Y0
                pulse_en_q0 = 1'b1;
                gate_sel_q0 = 3'b010;
            end
            4'b0011: begin  // X0/2
                pulse_en_q0 = 1'b1;
                gate_sel_q0 = 3'b011;
            end
            4'b0100: begin  // H0
                pulse_en_q0 = 1'b1;
                gate_sel_q0 = 3'b100;
            end
            // ── Single-qubit on Q1 ──────────────────────────────────────────
            4'b0101: begin  // X1
                pulse_en_q1 = 1'b1;
                gate_sel_q1 = 3'b001;
            end
            4'b0110: begin  // Y1
                pulse_en_q1 = 1'b1;
                gate_sel_q1 = 3'b010;
            end
            4'b0111: begin  // X1/2
                pulse_en_q1 = 1'b1;
                gate_sel_q1 = 3'b011;
            end
            4'b1000: begin  // H1
                pulse_en_q1 = 1'b1;
                gate_sel_q1 = 3'b100;
            end
            // ── Two-qubit gates ─────────────────────────────────────────────
            4'b1001: begin  // CNOT (cross-resonance)
                pulse_en_q0  = 1'b1;
                pulse_en_q1  = 1'b1;
                gate_sel_q0  = 3'b101;  // CNOT_CR on Q0 (drive Q0 at Q1 frequency)
                gate_sel_q1  = 3'b011;  // X1H correction on Q1
                is_two_qubit = 1'b1;
            end
            4'b1010: begin  // CZ (parametric / flux-activated)
                pulse_en_q0  = 1'b1;
                pulse_en_q1  = 1'b1;
                gate_sel_q0  = 3'b110;  // CZ flux pulse on Q0
                gate_sel_q1  = 3'b111;  // CZ phase correction on Q1
                is_two_qubit = 1'b1;
            end
            default: begin
                gate_valid   = 1'b0;
                pulse_en_q0  = 1'b0;
                pulse_en_q1  = 1'b0;
                gate_sel_q0  = 3'b000;
                gate_sel_q1  = 3'b000;
            end
        endcase
    end

endmodule
