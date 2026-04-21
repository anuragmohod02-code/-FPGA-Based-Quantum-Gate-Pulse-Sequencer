// =============================================================================
// seq_2q_fsm.v
// =============================================================================
// Two-qubit pulse sequencer FSM.
//
// Extends the single-qubit FSM with:
//   - 4-bit opcode field (16 gate types vs 8)
//   - Simultaneous dual-channel control (pulse_en_q0 / pulse_en_q1)
//   - CR (cross-resonance) mode flag during CNOT gate
//
// State machine (identical structure to pulse_sequencer_fsm):
//
//   IDLE ──(start)──► FETCH ──► DECODE ──► PLAY ──► WAIT ──►┐
//     ▲                                                      │
//     └────────────────────────── DONE ◄────────────────────┘
//
// Instruction word: [7:5]=repeat_cnt  [4]=reserved  [3:0]=opcode
// Instruction RAM : 16 × 8-bit (same as single-qubit design)
// =============================================================================

`timescale 1ns / 1ps

module seq_2q_fsm #(
    parameter SEQ_LEN   = 16,
    parameter ROM_DEPTH = 256
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,

    // Instruction RAM write port
    input  wire        seq_wr_en,
    input  wire [3:0]  seq_wr_addr,
    input  wire [7:0]  seq_wr_data,   // [7:5]=repeat_cnt, [3:0]=opcode

    input  wire [4:0]  seq_len,

    // From gate_decoder_2q (combinatorial)
    input  wire        pulse_en_q0,
    input  wire        pulse_en_q1,
    input  wire        is_two_qubit,

    // To ROM / NCO / modulator
    output reg  [3:0]  gate_opcode_out,
    output reg  [7:0]  sample_idx_out,
    output reg         rom_load_en,
    output reg         nco_enable,
    output reg         mod_enable,
    output reg         cr_mode,        // HIGH during CNOT cross-resonance tone

    // Status
    output reg         pulse_active,
    output reg         seq_done,
    output wire [3:0]  current_gate,
    output wire [3:0]  fsm_state
);

    // ── Instruction RAM ───────────────────────────────────────────────────────
    reg [7:0] instr_ram [0:SEQ_LEN-1];

    integer init_k;
    initial begin
        for (init_k = 0; init_k < SEQ_LEN; init_k = init_k + 1)
            instr_ram[init_k] = 8'h00;
    end

    always @(posedge clk) begin
        if (seq_wr_en)
            instr_ram[seq_wr_addr] <= seq_wr_data;
    end

    // ── FSM states ────────────────────────────────────────────────────────────
    localparam [3:0]
        ST_IDLE   = 4'd0,
        ST_FETCH  = 4'd1,
        ST_DECODE = 4'd2,
        ST_PLAY   = 4'd3,
        ST_WAIT   = 4'd4,
        ST_DONE   = 4'd5;

    reg [3:0]  state;
    reg [3:0]  pc;
    reg [7:0]  sample_cnt;
    reg [3:0]  cur_opcode;
    reg [2:0]  repeat_remaining;
    reg [7:0]  cur_instr;

    assign fsm_state    = state;
    assign current_gate = cur_opcode;

    // ── Sequential logic ──────────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state            <= ST_IDLE;
            pc               <= 4'd0;
            sample_cnt       <= 8'd0;
            cur_opcode       <= 4'd0;
            repeat_remaining <= 3'd0;
            cur_instr        <= 8'd0;
            gate_opcode_out  <= 4'd0;
            sample_idx_out   <= 8'd0;
            rom_load_en      <= 1'b0;
            nco_enable       <= 1'b0;
            mod_enable       <= 1'b0;
            cr_mode          <= 1'b0;
            pulse_active     <= 1'b0;
            seq_done         <= 1'b0;
        end else begin
            // Default de-assertions
            rom_load_en  <= 1'b0;
            seq_done     <= 1'b0;

            case (state)

                // ── IDLE ────────────────────────────────────────────────────
                ST_IDLE: begin
                    nco_enable  <= 1'b0;
                    mod_enable  <= 1'b0;
                    pulse_active<= 1'b0;
                    cr_mode     <= 1'b0;
                    pc          <= 4'd0;
                    if (start)
                        state <= ST_FETCH;
                end

                // ── FETCH ───────────────────────────────────────────────────
                ST_FETCH: begin
                    cur_instr        <= instr_ram[pc];
                    cur_opcode       <= instr_ram[pc][3:0];
                    repeat_remaining <= instr_ram[pc][7:5];
                    state            <= ST_DECODE;
                end

                // ── DECODE ──────────────────────────────────────────────────
                ST_DECODE: begin
                    gate_opcode_out <= cur_opcode;
                    sample_cnt      <= 8'd0;
                    sample_idx_out  <= 8'd0;

                    // Assert CR mode for CNOT (opcode 4'b1001)
                    cr_mode <= (cur_opcode == 4'b1001);

                    if (cur_opcode == 4'b0000) begin
                        // NOP: skip straight to WAIT
                        nco_enable   <= 1'b0;
                        mod_enable   <= 1'b0;
                        pulse_active <= 1'b0;
                        state        <= ST_WAIT;
                    end else begin
                        nco_enable   <= 1'b1;
                        mod_enable   <= 1'b1;
                        pulse_active <= 1'b1;
                        rom_load_en  <= 1'b1;
                        state        <= ST_PLAY;
                    end
                end

                // ── PLAY ────────────────────────────────────────────────────
                ST_PLAY: begin
                    rom_load_en    <= 1'b1;
                    sample_idx_out <= sample_cnt;

                    if (sample_cnt == ROM_DEPTH[7:0] - 1) begin
                        // Last sample
                        if (repeat_remaining > 3'd0) begin
                            // Replay same gate
                            repeat_remaining <= repeat_remaining - 3'd1;
                            sample_cnt       <= 8'd0;
                        end else begin
                            // Gate complete
                            rom_load_en  <= 1'b0;
                            mod_enable   <= 1'b0;
                            pulse_active <= 1'b0;
                            cr_mode      <= 1'b0;
                            state        <= ST_WAIT;
                        end
                    end else begin
                        sample_cnt <= sample_cnt + 8'd1;
                    end
                end

                // ── WAIT ────────────────────────────────────────────────────
                ST_WAIT: begin
                    nco_enable <= 1'b0;
                    if (pc == seq_len - 1) begin
                        state <= ST_DONE;
                    end else begin
                        pc    <= pc + 4'd1;
                        state <= ST_FETCH;
                    end
                end

                // ── DONE ────────────────────────────────────────────────────
                ST_DONE: begin
                    seq_done <= 1'b1;
                    state    <= ST_IDLE;
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule
