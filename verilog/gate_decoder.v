// =============================================================================
// gate_decoder.v
// =============================================================================
// Decodes a 3-bit gate opcode into control signals consumed by the sequencer.
//
// Gate encoding (matches pulse_rom.v):
//   3'b000 = NOP   — no operation, sequencer advances without playing
//   3'b001 = X     — pi pulse, I axis (0°)
//   3'b010 = Y     — pi pulse, Q axis (90°)
//   3'b011 = XH    — X/2 pi/2 pulse (I axis, half rotation)
//   3'b100 = H     — Hadamard approximation (45° mixed axis)
//   3'b101..111    — reserved → treated as NOP
//
// Outputs:
//   pulse_en       — 1 if the gate requires a pulse to be played
//   gate_valid     — 1 if opcode is a known gate (not reserved)
//   rom_gate_sel   — gate index forwarded to pulse_rom (same 3-bit value)
//   pulse_cycles   — number of samples to play (all gates use ROM_DEPTH=256)
// =============================================================================

`timescale 1ns / 1ps

module gate_decoder #(
    parameter ROM_DEPTH = 256
)(
    input  wire [2:0]  opcode,
    output reg         pulse_en,
    output reg         gate_valid,
    output reg  [2:0]  rom_gate_sel,
    output reg  [8:0]  pulse_cycles    // 9 bits: 256 = 9'b100000000
);

    always @(*) begin
        // Defaults
        pulse_en     = 1'b0;
        gate_valid   = 1'b1;
        rom_gate_sel = opcode;
        pulse_cycles = ROM_DEPTH[8:0];  // 256

        case (opcode)
            3'b000: begin  // NOP
                pulse_en   = 1'b0;
                gate_valid = 1'b1;
            end
            3'b001: begin  // X
                pulse_en   = 1'b1;
            end
            3'b010: begin  // Y
                pulse_en   = 1'b1;
            end
            3'b011: begin  // X/2
                pulse_en   = 1'b1;
            end
            3'b100: begin  // H
                pulse_en   = 1'b1;
            end
            default: begin  // Reserved
                pulse_en     = 1'b0;
                gate_valid   = 1'b0;
                rom_gate_sel = 3'b000;
            end
        endcase
    end

endmodule
