// =============================================================================
// tb_pulse_sequencer.v  —  Testbench for top_pulse_sequencer
// =============================================================================
// Tests two distinct gate sequences to demonstrate the expanded ISA:
//
//  Sequence A — Basic gate set (original):
//    X → Y → X/2 → H → NOP
//
//  Sequence B — CPMG-4 dynamical decoupling:
//    X/2 → X×4 (repeat_cnt=3) → X/2
//    (Hahn echo generalised to 4 refocusing pulses, T2 protection)
//
// Instruction word format: [7:5]=repeat_cnt, [4:3]=reserved, [2:0]=opcode
// phase_offset = 0x40000000 tested in Sequence B (90° shift for CPMG Y-axis)
//
// NCO settings:
//   f_clk=1 GHz, f_carrier=100 MHz
//   phase_inc = round(100e6/1e9 × 2^32) = 0x1999_999A
//
// Output: sim_outputs/iq_output.csv
// =============================================================================

`timescale 1ns / 1ps

module tb_pulse_sequencer;

    parameter CLK_PERIOD = 1;
    parameter PHASE_INC  = 32'h1999999A;  // 100 MHz at 1 GHz clock
    parameter PHASE_90   = 32'h40000000;  // 90° phase offset

    // -------------------------------------------------------------------------
    // DUT port signals
    // -------------------------------------------------------------------------
    reg         clk;
    reg         rst_n;
    reg         start;
    reg         seq_wr_en;
    reg  [3:0]  seq_wr_addr;
    reg  [7:0]  seq_wr_data;   // 8-bit instruction: [7:5]=repeat_cnt, [2:0]=opcode
    reg  [4:0]  seq_len;
    reg  [31:0] phase_inc;
    reg  [31:0] phase_offset;

    wire [15:0] rf_i_out;
    wire [15:0] rf_q_out;
    wire        pulse_active;
    wire        seq_done;
    wire [3:0]  fsm_state;
    wire [2:0]  current_gate;
    wire [2:0]  repeat_remaining;

    // -------------------------------------------------------------------------
    // DUT instantiation
    // -------------------------------------------------------------------------
    top_pulse_sequencer #(
        .ROM_DEPTH  (256),
        .SEQ_LEN    (16),
        .PHASE_BITS (32),
        .DATA_WIDTH (16)
    ) dut (
        .clk              (clk),
        .rst_n            (rst_n),
        .start            (start),
        .seq_wr_en        (seq_wr_en),
        .seq_wr_addr      (seq_wr_addr),
        .seq_wr_data      (seq_wr_data),
        .seq_len          (seq_len),
        .phase_inc        (phase_inc),
        .phase_offset     (phase_offset),
        .rf_i_out         (rf_i_out),
        .rf_q_out         (rf_q_out),
        .pulse_active     (pulse_active),
        .seq_done         (seq_done),
        .fsm_state        (fsm_state),
        .current_gate     (current_gate),
        .repeat_remaining (repeat_remaining)
    );
        .current_gate (current_gate)
    );

    // -------------------------------------------------------------------------
    // Clock generation
    // -------------------------------------------------------------------------
    initial clk = 0;
    always #(CLK_PERIOD / 2.0) clk = ~clk;

    // -------------------------------------------------------------------------
    // CSV logging file
    // -------------------------------------------------------------------------
    integer csv_fd;
    integer cycle_count;

    // -------------------------------------------------------------------------
    // Task: write one instruction to the instruction RAM
    //   instr_byte = {repeat_cnt[2:0], 2'b00, opcode[2:0]}
    // -------------------------------------------------------------------------
    task write_gate;
        input [3:0] addr;
        input [7:0] instr_byte;   // full 8-bit instruction word
        begin
            @(posedge clk); #0.1;
            seq_wr_en   = 1'b1;
            seq_wr_addr = addr;
            seq_wr_data = instr_byte;
            @(posedge clk); #0.1;
            seq_wr_en   = 1'b0;
        end
    endtask

    // Helper: pack instruction word
    // Usage: write_gate(addr, mk_instr(repeat_cnt, opcode))
    function [7:0] mk_instr;
        input [2:0] rpt;    // repeat count (0=1×, 3=4×, ...)
        input [2:0] opc;    // gate opcode
        begin
            mk_instr = {rpt, 2'b00, opc};
        end
    endfunction

    // -------------------------------------------------------------------------
    // Main stimulus
    // -------------------------------------------------------------------------
    initial begin
        // Initialise
        rst_n        = 1'b0;
        start        = 1'b0;
        seq_wr_en    = 1'b0;
        seq_wr_addr  = 4'd0;
        seq_wr_data  = 8'd0;
        seq_len      = 5'd5;
        phase_inc    = PHASE_INC;
        phase_offset = 32'd0;   // no phase shift for Sequence A
        cycle_count  = 0;

        // Hold reset for 10 cycles
        repeat (10) @(posedge clk);
        rst_n = 1'b1;
        repeat (5)  @(posedge clk);

        // ── Sequence A: Basic gate set ─────────────────────────────────────
        // Format: mk_instr(repeat_cnt, opcode)
        //   repeat_cnt=0 → gate plays 1× total
        write_gate(4'd0, mk_instr(3'd0, 3'b001));  // X     ×1
        write_gate(4'd1, mk_instr(3'd0, 3'b010));  // Y     ×1
        write_gate(4'd2, mk_instr(3'd0, 3'b011));  // X/2   ×1
        write_gate(4'd3, mk_instr(3'd0, 3'b100));  // H     ×1
        write_gate(4'd4, mk_instr(3'd0, 3'b000));  // NOP   ×1

        // ── Open CSV file ─────────────────────────────────────────────────
        csv_fd = $fopen("../sim_outputs/iq_output.csv", "w");
        if (csv_fd == 0) begin
            $display("ERROR: Cannot open sim_outputs/iq_output.csv");
            $finish;
        end
        $fdisplay(csv_fd, "cycle,rf_i,rf_q,pulse_active,fsm_state,current_gate,repeat_remaining,phase_offset_en");

        $display("=== Sequence A: X -> Y -> X/2 -> H -> NOP ===");
        phase_offset = 32'd0;
        @(posedge clk); #0.1;
        start = 1'b1;
        @(posedge clk); #0.1;
        start = 1'b0;

        // ── Run and log ───────────────────────────────────────────────────
        // Log until seq_done or 12,000 cycles (whichever first)
        fork
            begin : log_loop
                repeat (20000) begin
                    @(posedge clk);
                    $fdisplay(csv_fd, "%0d,%0d,%0d,%b,%0d,%0d,%0d,%0d",
                        cycle_count,
                        $signed(rf_i_out),
                        $signed(rf_q_out),
                        pulse_active,
                        fsm_state,
                        current_gate,
                        repeat_remaining,
                        (phase_offset != 32'd0) ? 1 : 0);
                        $signed(rf_q_out),
                        pulse_active,
                        fsm_state,
                        current_gate);
                    cycle_count = cycle_count + 1;
                end
            end
            begin : wait_done
                @(posedge seq_done);
                $display("=== Sequence A complete at t=%0t ns ===", $time);

                // ── Sequence B: CPMG-4 (X/2 — X×4 — X/2) ────────────────
                // Reload instruction RAM with new sequence
                repeat (10) @(posedge clk);
                write_gate(4'd0, mk_instr(3'd0, 3'b011));  // X/2 ×1
                write_gate(4'd1, mk_instr(3'd3, 3'b001));  // X   ×4 (repeat_cnt=3 → 1+3=4 plays)
                write_gate(4'd2, mk_instr(3'd0, 3'b011));  // X/2 ×1
                seq_len      = 5'd3;
                phase_offset = PHASE_90;  // shift carrier 90° for Y-axis refocusing

                $display("=== Sequence B: CPMG-4 (X/2 - X*4 - X/2) ===");
                @(posedge clk); #0.1;
                start = 1'b1;
                @(posedge clk); #0.1;
                start = 1'b0;

                @(posedge seq_done);
                $display("=== Sequence B complete at t=%0t ns ===", $time);

                // Extra tail cycles
                repeat (200) @(posedge clk);
                disable log_loop;
            end
        join

        $fclose(csv_fd);
        $display("Simulation complete. %0d cycles logged.", cycle_count);

        // ── Console waveform summary ──────────────────────────────────────
        $display("Final FSM state   : %0d", fsm_state);
        $display("Last gate played  : %0d", current_gate);
        $display("seq_done received : %b", seq_done);

        #100;
        $finish;
    end

    // -------------------------------------------------------------------------
    // Timeout watchdog (20 000 cycles)
    // -------------------------------------------------------------------------
    initial begin
        #20000;
        $display("TIMEOUT: simulation exceeded 20 000 ns without finishing.");
        $finish;
    end

    // -------------------------------------------------------------------------
    // seq_done monitor
    // -------------------------------------------------------------------------
    always @(posedge clk) begin
        if (seq_done)
            $display("[%0t ns] seq_done asserted — all gates played.", $time);
        if (pulse_active)
            $display("[%0t ns] PLAY gate=%0d repeat_rem=%0d sample=%0d  I=%0d  Q=%0d",
                $time, current_gate, repeat_remaining,
                dut.u_fsm.sample_idx_out,
                $signed(rf_i_out), $signed(rf_q_out));
    end

endmodule
