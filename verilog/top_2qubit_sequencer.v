// =============================================================================
// top_2qubit_sequencer.v
// =============================================================================
// Top-level for a two-qubit gate pulse sequencer.
//
// Architecture
// ────────────
//                 ┌──────────────────────────────────────────┐
//   AXI-Lite cfg  │          seq_2q_fsm                      │
//   ─────────────►│  gate_decoder_2q                         │
//                 │  → ch0: pulse_rom_q0 → nco_q0 → iq_mod0  ├─► rf_i0 / rf_q0
//                 │  → ch1: pulse_rom_q1 → nco_q1 → iq_mod1  ├─► rf_i1 / rf_q1
//                 └──────────────────────────────────────────┘
//
// Two entirely independent IQ chains (two DRAGs, two NCOs, two modulators)
// share a single FSM that dispatches gate opcodes to both channels
// simultaneously for two-qubit gates (CNOT, CZ) or individually for
// single-qubit gates.
//
// Instruction word: 8 bits  [7:5]=repeat_cnt  [4]=reserved  [3:0]=opcode
// Sequence RAM: 16 entries (same depth as single-qubit design)
//
// NCO notes:
//   Q0 carrier = phase_inc_q0  (typically qubit-0 frequency)
//   Q1 carrier = phase_inc_q1  (typically qubit-1 frequency, e.g. ±200 MHz off)
//   For CNOT (cross-resonance), the FSM automatically swaps Q0's NCO to Q1's
//   frequency during the CR tone via cr_mode internal flag — no host SW needed.
//
// Resource estimate (Xilinx Artix-7 xc7a35t):
//   BRAM : 2  (one 18k BRAM per pulse_rom × 2 channels)
//   DSP  : 8  (four 16×16 multipliers per iq_modulator × 2 channels)
//   FF/LUT: ~750 (two NCOs + two-qubit FSM + decoder)
//
// Ports identical to top_pulse_sequencer but with _q0/_q1 suffix for
// channel-specific signals.
// =============================================================================

`timescale 1ns / 1ps

module top_2qubit_sequencer #(
    parameter ROM_DEPTH  = 256,
    parameter SEQ_LEN    = 16,
    parameter PHASE_BITS = 32,
    parameter DATA_WIDTH = 16
)(
    input  wire                   clk,
    input  wire                   rst_n,

    // Sequence control
    input  wire                   start,
    input  wire                   seq_wr_en,
    input  wire [3:0]             seq_wr_addr,
    input  wire [7:0]             seq_wr_data,   // [7:5]=repeat_cnt, [3:0]=opcode

    input  wire [4:0]             seq_len,

    // Per-channel NCO frequencies
    input  wire [PHASE_BITS-1:0]  phase_inc_q0,
    input  wire [PHASE_BITS-1:0]  phase_inc_q1,
    input  wire [PHASE_BITS-1:0]  phase_offset_q0,
    input  wire [PHASE_BITS-1:0]  phase_offset_q1,

    // Channel 0 output
    output wire [DATA_WIDTH-1:0]  rf_i_q0,
    output wire [DATA_WIDTH-1:0]  rf_q_q0,

    // Channel 1 output
    output wire [DATA_WIDTH-1:0]  rf_i_q1,
    output wire [DATA_WIDTH-1:0]  rf_q_q1,

    // Status
    output wire                   pulse_active,
    output wire                   seq_done,
    output wire [3:0]             fsm_state,
    output wire [3:0]             current_gate,
    output wire                   is_two_qubit_out
);

    // ── Internal wires ────────────────────────────────────────────────────────

    // FSM → decoders / ROM
    wire [3:0]  fsm_gate_opcode;
    wire [7:0]  fsm_sample_idx;
    wire        fsm_rom_load_en;
    wire        fsm_nco_enable;
    wire        fsm_mod_enable;
    wire        fsm_cr_mode;       // high during CNOT CR tone → swap Q0 NCO freq

    // Decoder → ROM/NCO
    wire        dec_pulse_en_q0;
    wire        dec_pulse_en_q1;
    wire [2:0]  dec_gate_sel_q0;
    wire [2:0]  dec_gate_sel_q1;
    wire        dec_is_two_qubit;
    wire        dec_valid;

    // ROM outputs
    wire [DATA_WIDTH-1:0] rom_i_q0, rom_q_q0, rom_i_q1, rom_q_q1;
    wire                  rom_valid_q0, rom_valid_q1;

    // NCO outputs
    wire [DATA_WIDTH-1:0] cos_q0, sin_q0, cos_q1, sin_q1;

    // For cross-resonance: Q0 NCO switches to Q1 frequency
    wire [PHASE_BITS-1:0] nco_freq_q0 = fsm_cr_mode ? phase_inc_q1 : phase_inc_q0;

    // ── Gate decoder (2-qubit ISA) ────────────────────────────────────────────
    gate_decoder_2q #(.ROM_DEPTH(ROM_DEPTH)) u_dec (
        .opcode      (fsm_gate_opcode),
        .pulse_en_q0 (dec_pulse_en_q0),
        .pulse_en_q1 (dec_pulse_en_q1),
        .gate_sel_q0 (dec_gate_sel_q0),
        .gate_sel_q1 (dec_gate_sel_q1),
        .is_two_qubit(dec_is_two_qubit),
        .gate_valid  (dec_valid)
    );

    assign is_two_qubit_out = dec_is_two_qubit;

    // ── FSM (2-qubit version) ─────────────────────────────────────────────────
    seq_2q_fsm #(
        .SEQ_LEN  (SEQ_LEN),
        .ROM_DEPTH(ROM_DEPTH)
    ) u_fsm (
        .clk              (clk),
        .rst_n            (rst_n),
        .start            (start),
        .seq_wr_en        (seq_wr_en),
        .seq_wr_addr      (seq_wr_addr),
        .seq_wr_data      (seq_wr_data),
        .seq_len          (seq_len),
        // from decoder
        .pulse_en_q0      (dec_pulse_en_q0),
        .pulse_en_q1      (dec_pulse_en_q1),
        .is_two_qubit     (dec_is_two_qubit),
        // outputs
        .gate_opcode_out  (fsm_gate_opcode),
        .sample_idx_out   (fsm_sample_idx),
        .rom_load_en      (fsm_rom_load_en),
        .nco_enable       (fsm_nco_enable),
        .mod_enable       (fsm_mod_enable),
        .cr_mode          (fsm_cr_mode),
        .pulse_active     (pulse_active),
        .seq_done         (seq_done),
        .current_gate     (current_gate),
        .fsm_state        (fsm_state)
    );

    // ── Pulse ROMs (independent per channel) ─────────────────────────────────
    pulse_rom #(.DATA_WIDTH(DATA_WIDTH)) u_rom_q0 (
        .clk       (clk),
        .gate_sel  (dec_gate_sel_q0),
        .sample_idx(fsm_sample_idx),
        .load_en   (fsm_rom_load_en & dec_pulse_en_q0),
        .data_i    (rom_i_q0),
        .data_q    (rom_q_q0),
        .valid     (rom_valid_q0)
    );

    pulse_rom #(.DATA_WIDTH(DATA_WIDTH)) u_rom_q1 (
        .clk       (clk),
        .gate_sel  (dec_gate_sel_q1),
        .sample_idx(fsm_sample_idx),
        .load_en   (fsm_rom_load_en & dec_pulse_en_q1),
        .data_i    (rom_i_q1),
        .data_q    (rom_q_q1),
        .valid     (rom_valid_q1)
    );

    // ── NCOs ─────────────────────────────────────────────────────────────────
    nco #(.PHASE_BITS(PHASE_BITS), .OUT_BITS(DATA_WIDTH)) u_nco_q0 (
        .clk         (clk),
        .rst_n       (rst_n),
        .enable      (fsm_nco_enable),
        .phase_inc   (nco_freq_q0),       // swaps to Q1 freq during CR tone
        .phase_offset(phase_offset_q0),
        .cos_out     (cos_q0),
        .sin_out     (sin_q0)
    );

    nco #(.PHASE_BITS(PHASE_BITS), .OUT_BITS(DATA_WIDTH)) u_nco_q1 (
        .clk         (clk),
        .rst_n       (rst_n),
        .enable      (fsm_nco_enable),
        .phase_inc   (phase_inc_q1),
        .phase_offset(phase_offset_q1),
        .cos_out     (cos_q1),
        .sin_out     (sin_q1)
    );

    // ── IQ modulators ─────────────────────────────────────────────────────────
    iq_modulator #(.DATA_WIDTH(DATA_WIDTH)) u_mod_q0 (
        .clk        (clk),
        .enable     (fsm_mod_enable & dec_pulse_en_q0),
        .i_env      (rom_i_q0),
        .q_env      (rom_q_q0),
        .cos_carrier(cos_q0),
        .sin_carrier(sin_q0),
        .rf_i_out   (rf_i_q0),
        .rf_q_out   (rf_q_q0),
        .valid_out  ()
    );

    iq_modulator #(.DATA_WIDTH(DATA_WIDTH)) u_mod_q1 (
        .clk        (clk),
        .enable     (fsm_mod_enable & dec_pulse_en_q1),
        .i_env      (rom_i_q1),
        .q_env      (rom_q_q1),
        .cos_carrier(cos_q1),
        .sin_carrier(sin_q1),
        .rf_i_out   (rf_i_q1),
        .rf_q_out   (rf_q_q1),
        .valid_out  ()
    );

endmodule
