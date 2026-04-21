// =============================================================================
// top_pulse_sequencer.v
// =============================================================================
// Top-level module for the FPGA-Based Quantum Gate Pulse Sequencer.
//
// Instantiates and connects:
//   1. pulse_sequencer_fsm  — main control FSM
//   2. pulse_rom            — DRAG pulse envelope ROM
//   3. nco                  — carrier NCO (cosine + sine)
//   4. iq_modulator         — envelope × carrier SSB modulation
//
// Carrier frequency is set by phase_inc input.
// The gate sequence is loaded via the seq_wr_* ports before asserting start.
//
// Typical usage (testbench / SDK):
//   1. Write gate opcodes to instr RAM via seq_wr_en/addr/data
//   2. Set seq_len to number of gates
//   3. Set phase_inc for desired carrier frequency
//   4. Assert start for one clock cycle
//   5. Monitor pulse_active, rf_i_out, rf_q_out
//   6. Wait for seq_done
//
// Resource estimate (Xilinx Artix-7 xc7a35t):
//   BRAM:  1 (pulse_rom — 1280×16 bits fits in one 18k BRAM)
//   DSP:   4 (four 16×16 multipliers in iq_modulator)
//   FF/LUT: ~400 (FSM + NCO accumulator + control)
//
// Ports:
//   clk           — 100 MHz system clock (or as fast as desired)
//   rst_n         — active-low synchronous reset
//   start         — one-cycle start pulse
//   seq_wr_en     — write enable for instruction RAM
//   seq_wr_addr   — 4-bit write address (0..15)
//   seq_wr_data   — 3-bit gate opcode
//   seq_len       — number of gates in sequence (1..16)
//   phase_inc     — 32-bit NCO phase increment
//   rf_i_out      — 16-bit signed modulated I output (→ DAC)
//   rf_q_out      — 16-bit signed modulated Q output (→ DAC)
//   pulse_active  — high during gate pulse playback
//   seq_done      — one-cycle pulse on sequence completion
//   fsm_state     — 4-bit FSM state (for debug/ILA)
//   current_gate  — 3-bit gate opcode currently playing (for debug/ILA)
// =============================================================================

`timescale 1ns / 1ps

module top_pulse_sequencer #(
    parameter ROM_DEPTH  = 256,
    parameter SEQ_LEN    = 16,
    parameter PHASE_BITS = 32,
    parameter DATA_WIDTH = 16
)(
    input  wire                   clk,
    input  wire                   rst_n,

    // Sequence load interface
    input  wire                   start,
    input  wire                   seq_wr_en,
    input  wire [3:0]             seq_wr_addr,
    input  wire [7:0]             seq_wr_data,   // [7:5]=repeat_cnt, [2:0]=opcode
    input  wire [4:0]             seq_len,

    // NCO control
    input  wire [PHASE_BITS-1:0]  phase_inc,
    input  wire [PHASE_BITS-1:0]  phase_offset,  // real-time carrier phase shift

    // Data outputs (→ DAC)
    output wire [DATA_WIDTH-1:0]  rf_i_out,
    output wire [DATA_WIDTH-1:0]  rf_q_out,

    // Status
    output wire                   pulse_active,
    output wire                   seq_done,
    output wire [3:0]             fsm_state,
    output wire [2:0]             current_gate,
    output wire [2:0]             repeat_remaining  // repeats left for current gate
);

    // -------------------------------------------------------------------------
    // Internal wires
    // -------------------------------------------------------------------------

    // FSM → ROM
    wire [2:0] fsm_gate_sel;
    wire [7:0] fsm_sample_idx;
    wire       fsm_rom_load_en;

    // FSM → NCO / modulator
    wire       fsm_nco_enable;
    wire       fsm_mod_enable;

    // ROM → modulator
    wire [DATA_WIDTH-1:0] rom_data_i;
    wire [DATA_WIDTH-1:0] rom_data_q;
    wire                  rom_valid;

    // NCO → modulator
    wire [DATA_WIDTH-1:0] nco_cos;
    wire [DATA_WIDTH-1:0] nco_sin;

    // -------------------------------------------------------------------------
    // 1. Sequencer FSM
    // -------------------------------------------------------------------------
    pulse_sequencer_fsm #(
        .SEQ_LEN   (SEQ_LEN),
        .ROM_DEPTH (ROM_DEPTH)
    ) u_fsm (
        .clk            (clk),
        .rst_n          (rst_n),
        .start          (start),
        .seq_wr_en      (seq_wr_en),
        .seq_wr_addr    (seq_wr_addr),
        .seq_wr_data    (seq_wr_data),
        .seq_len        (seq_len),
        .gate_sel_out   (fsm_gate_sel),
        .sample_idx_out (fsm_sample_idx),
        .rom_load_en    (fsm_rom_load_en),
        .nco_enable     (fsm_nco_enable),
        .mod_enable     (fsm_mod_enable),
        .pulse_active   (pulse_active),
        .seq_done       (seq_done),
        .current_gate   (current_gate),
        .repeat_remaining_out (repeat_remaining),
        .fsm_state      (fsm_state)
    );

    // -------------------------------------------------------------------------
    // 2. Pulse ROM
    // -------------------------------------------------------------------------
    pulse_rom #(
        .DATA_WIDTH (DATA_WIDTH)
    ) u_rom (
        .clk        (clk),
        .gate_sel   (fsm_gate_sel),
        .sample_idx (fsm_sample_idx),
        .load_en    (fsm_rom_load_en),
        .data_i     (rom_data_i),
        .data_q     (rom_data_q),
        .valid      (rom_valid)
    );

    // -------------------------------------------------------------------------
    // 3. NCO
    // -------------------------------------------------------------------------
    nco #(
        .PHASE_BITS (PHASE_BITS),
        .OUT_BITS   (DATA_WIDTH)
    ) u_nco (
        .clk       (clk),
        .rst_n     (rst_n),
        .enable    (fsm_nco_enable),
        .phase_inc (phase_inc),
        .phase_offset (phase_offset),
        .cos_out   (nco_cos),
        .sin_out   (nco_sin)
    );

    // -------------------------------------------------------------------------
    // 4. IQ Modulator
    // -------------------------------------------------------------------------
    iq_modulator #(
        .DATA_WIDTH (DATA_WIDTH)
    ) u_mod (
        .clk         (clk),
        .enable      (fsm_mod_enable),
        .i_env       (rom_data_i),
        .q_env       (rom_data_q),
        .cos_carrier (nco_cos),
        .sin_carrier (nco_sin),
        .rf_i_out    (rf_i_out),
        .rf_q_out    (rf_q_out),
        .valid_out   (/* unused at top level */)
    );

endmodule
