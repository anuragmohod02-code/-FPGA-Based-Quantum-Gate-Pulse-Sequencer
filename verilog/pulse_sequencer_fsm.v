// =============================================================================
// pulse_sequencer_fsm.v
// =============================================================================
// Main control FSM for the quantum gate pulse sequencer.
//
// Reads a programmed gate sequence from an internal instruction RAM
// (BRAM-style), decodes each gate via gate_decoder, and orchestrates
// pulse_rom + nco + iq_modulator for accurate pulse playback.
//
// State machine:
//
//   IDLE  ──(start)──► FETCH  ──► DECODE  ──► PLAY  ──► WAIT  ──►┐
//     ▲                                                            │
//     └──────────────────────────── (seq_done) ◄──────────────────┘
//
//   IDLE   : waiting for start pulse
//   FETCH  : read next gate opcode from instruction RAM
//   DECODE : one cycle for gate_decoder combinatorial output to settle
//   PLAY   : stream ROM samples; assert pulse_play; increment sample_idx
//            (skipped for NOP — goes directly to WAIT)
//   WAIT   : one dead cycle between gates (inter-gate gap)
//   DONE   : all instructions played; assert seq_done for one cycle
//
// Instruction RAM:
//   16 entries × 4 bits: [3:1]=reserved, [2:0]=gate opcode
//   Written via seq_wr_en / seq_wr_addr / seq_wr_data ports.
//   Max sequence length = SEQ_LEN (default 16 gates).
//
// Ports (summary — full descriptions inline):
//   clk, rst_n       - clock / active-low reset
//   start            - one-cycle pulse to begin sequence
//   seq_wr_en        - write enable for instruction RAM
//   seq_wr_addr      - 4-bit write address
//   seq_wr_data      - 8-bit instruction word ([7:5]=repeat_cnt, [2:0]=opcode)
//   seq_len          - number of unique instructions to execute (1..SEQ_LEN)
//   gate_sel_out     - current gate forwarded to pulse_rom
//   sample_idx_out   - current sample index
//   rom_load_en      - load strobe to pulse_rom
//   nco_enable       - enable signal to NCO
//   mod_enable       - enable to IQ modulator
//   pulse_active     - high during a gate's pulse playback
//   seq_done         - one-cycle pulse when full sequence is complete
//   current_gate     - diagnostic: current gate opcode being played
//   repeat_remaining - diagnostic: repeats left for current gate
//   fsm_state        - diagnostic: current FSM state (4-bit)
// =============================================================================

`timescale 1ns / 1ps

module pulse_sequencer_fsm #(
    parameter SEQ_LEN   = 16,
    parameter ROM_DEPTH = 256
)(
    input  wire        clk,
    input  wire        rst_n,

    // Sequence start
    input  wire        start,

    // Instruction RAM write port (load gate sequence before start)
    input  wire        seq_wr_en,
    input  wire [3:0]  seq_wr_addr,
    input  wire [7:0]  seq_wr_data,    // [7:5]=repeat_cnt, [4:3]=rsvd, [2:0]=opcode

    // Sequence length (how many gates to execute)
    input  wire [4:0]  seq_len,        // 1..16

    // Outputs to pulse_rom
    output reg  [2:0]  gate_sel_out,
    output reg  [7:0]  sample_idx_out,
    output reg         rom_load_en,

    // Outputs to NCO and modulator
    output reg         nco_enable,
    output reg         mod_enable,

    // Status
    output reg         pulse_active,
    output reg         seq_done,
    output wire [2:0]  current_gate,
    output wire [2:0]  repeat_remaining_out,
    output wire [3:0]  fsm_state
);

    // -------------------------------------------------------------------------
    // Instruction RAM  (16 x 8-bit)
    // -------------------------------------------------------------------------
    reg [7:0] instr_ram [0:SEQ_LEN-1];

    integer init_k;
    initial begin
        for (init_k = 0; init_k < SEQ_LEN; init_k = init_k + 1)
            instr_ram[init_k] = 8'h00;  // default NOP, repeat=0
    end

    always @(posedge clk) begin
        if (seq_wr_en)
            instr_ram[seq_wr_addr] <= seq_wr_data;
    end

    // -------------------------------------------------------------------------
    // FSM state encoding
    // -------------------------------------------------------------------------
    localparam [3:0]
        ST_IDLE   = 4'd0,
        ST_FETCH  = 4'd1,
        ST_DECODE = 4'd2,
        ST_PLAY   = 4'd3,
        ST_WAIT   = 4'd4,
        ST_DONE   = 4'd5;

    reg [3:0]  state, next_state;

    // -------------------------------------------------------------------------
    // Internal registers
    // -------------------------------------------------------------------------
    reg [3:0]  pc;             // program counter (instruction index)
    reg [7:0]  sample_cnt;    // current sample within pulse
    reg [2:0]  cur_opcode;    // opcode fetched from RAM ([2:0] of instruction)
    reg [2:0]  repeat_remaining; // repeats still to play for current gate
    reg [4:0]  seq_len_reg;   // latched copy of seq_len at start

    assign current_gate        = cur_opcode;
    assign repeat_remaining_out = repeat_remaining;
    assign fsm_state           = state;
    wire        dec_pulse_en;
    wire        dec_gate_valid;
    wire [2:0]  dec_rom_gate_sel;
    wire [8:0]  dec_pulse_cycles;

    gate_decoder #(.ROM_DEPTH(ROM_DEPTH)) u_decoder (
        .opcode       (cur_opcode),
        .pulse_en     (dec_pulse_en),
        .gate_valid   (dec_gate_valid),
        .rom_gate_sel (dec_rom_gate_sel),
        .pulse_cycles (dec_pulse_cycles)
    );

    assign current_gate         = cur_opcode;
    assign repeat_remaining_out = repeat_remaining;
    assign fsm_state            = state;

    // -------------------------------------------------------------------------
    // State register
    // -------------------------------------------------------------------------
    always @(posedge clk) begin
        if (!rst_n)
            state <= ST_IDLE;
        else
            state <= next_state;
    end

    // -------------------------------------------------------------------------
    // Next-state logic
    // -------------------------------------------------------------------------
    always @(*) begin
        next_state = state;
        case (state)
            ST_IDLE:   if (start)                              next_state = ST_FETCH;
            ST_FETCH:                                          next_state = ST_DECODE;
            ST_DECODE: begin
                if (!dec_pulse_en)                             next_state = ST_WAIT;
                else                                           next_state = ST_PLAY;
            end
            ST_PLAY:   if (sample_cnt == ROM_DEPTH[7:0] - 1)  next_state = ST_WAIT;
            ST_WAIT: begin
                if (repeat_remaining > 3'b0)                   next_state = ST_DECODE;  // replay same gate
                else if (pc >= seq_len_reg - 1)                next_state = ST_DONE;
                else                                           next_state = ST_FETCH;
            end
            ST_DONE:                                           next_state = ST_IDLE;
            default:                                           next_state = ST_IDLE;
        endcase
    end

    // -------------------------------------------------------------------------
    // Datapath / output logic
    // -------------------------------------------------------------------------
    always @(posedge clk) begin
        if (!rst_n) begin
            pc               <= 4'd0;
            sample_cnt       <= 8'd0;
            cur_opcode       <= 3'd0;
            repeat_remaining <= 3'd0;
            seq_len_reg      <= 5'd1;
            gate_sel_out     <= 3'd0;
            sample_idx_out   <= 8'd0;
            rom_load_en      <= 1'b0;
            nco_enable       <= 1'b0;
            mod_enable       <= 1'b0;
            pulse_active     <= 1'b0;
            seq_done         <= 1'b0;
        end else begin
            // Default de-assertions
            rom_load_en  <= 1'b0;
            seq_done     <= 1'b0;
            pulse_active <= 1'b0;

            case (state)
                ST_IDLE: begin
                    pc          <= 4'd0;
                    sample_cnt  <= 8'd0;
                    nco_enable  <= 1'b0;
                    mod_enable  <= 1'b0;
                    if (start)
                        seq_len_reg <= seq_len;
                end

                ST_FETCH: begin
                    // Latch full 8-bit instruction: opcode in [2:0], repeat_cnt in [7:5]
                    cur_opcode       <= instr_ram[pc][2:0];
                    repeat_remaining <= instr_ram[pc][7:5];  // will be used in ST_WAIT
                end

                ST_DECODE: begin
                    gate_sel_out   <= dec_rom_gate_sel;
                    sample_idx_out <= 8'd0;
                    sample_cnt     <= 8'd0;
                    if (dec_pulse_en) begin
                        nco_enable <= 1'b1;
                        mod_enable <= 1'b1;
                        rom_load_en <= 1'b1;  // prime the ROM
                    end else begin
                        nco_enable <= 1'b0;
                        mod_enable <= 1'b0;
                    end
                end

                ST_PLAY: begin
                    pulse_active   <= 1'b1;
                    sample_idx_out <= sample_cnt;
                    rom_load_en    <= 1'b0;
                    sample_cnt     <= sample_cnt + 1;
                end

                ST_WAIT: begin
                    nco_enable <= 1'b0;
                    mod_enable <= 1'b0;
                    if (repeat_remaining > 3'b0) begin
                        // Replay the same gate: decrement counter, go back to DECODE
                        repeat_remaining <= repeat_remaining - 1;
                        // PC is NOT incremented here; DECODE re-uses cur_opcode
                    end else begin
                        pc <= pc + 1;
                    end
                end

                ST_DONE: begin
                    seq_done <= 1'b1;
                    pc       <= 4'd0;
                end

                default: ;
            endcase
        end
    end

endmodule
