// =============================================================================
// tb_2qubit_sequencer.v  —  Testbench for top_2qubit_sequencer
// =============================================================================
// Exercises three test sequences:
//
//  Sequence A — Bell-state preparation: H0 → CNOT
//    Creates |Φ+⟩ = (|00⟩ + |11⟩)/√2 from |00⟩
//
//  Sequence B — Full single-qubit sweep (all 8 single-qubit gates)
//    X0 → Y0 → X0H → H0 → X1 → Y1 → X1H → H1
//
//  Sequence C — CZ gate + phase correction
//    X0H → X1H → CZ → X0H → X1H
//    Implements a controlled-Z from ±π/2 rotations
//
// Carrier frequencies:
//   Q0: 100 MHz at 1 GHz clock  → phase_inc_q0 = 0x1999_999A
//   Q1: 112 MHz at 1 GHz clock  → phase_inc_q1 = 0x1CCC_CCCD (~200 MHz IF apart)
//
// Output: sim_outputs/iq_2qubit.csv
// =============================================================================

`timescale 1ns / 1ps

module tb_2qubit_sequencer;

    parameter CLK_PERIOD   = 1;
    parameter PHASE_Q0     = 32'h1999_999A;   // 100 MHz
    parameter PHASE_Q1     = 32'h1CCC_CCCD;   // ~112 MHz
    parameter PHASE_OFFSET = 32'h0000_0000;

    // ── DUT signals ───────────────────────────────────────────────────────────
    reg         clk, rst_n, start;
    reg         seq_wr_en;
    reg  [3:0]  seq_wr_addr;
    reg  [7:0]  seq_wr_data;
    reg  [4:0]  seq_len;
    reg  [31:0] phase_inc_q0, phase_inc_q1;
    reg  [31:0] phase_offset_q0, phase_offset_q1;

    wire [15:0] rf_i_q0, rf_q_q0;
    wire [15:0] rf_i_q1, rf_q_q1;
    wire        pulse_active, seq_done;
    wire [3:0]  fsm_state, current_gate;
    wire        is_two_qubit_out;

    // ── DUT instantiation ─────────────────────────────────────────────────────
    top_2qubit_sequencer #(
        .ROM_DEPTH (256),
        .SEQ_LEN   (16),
        .PHASE_BITS(32),
        .DATA_WIDTH(16)
    ) dut (
        .clk             (clk),
        .rst_n           (rst_n),
        .start           (start),
        .seq_wr_en       (seq_wr_en),
        .seq_wr_addr     (seq_wr_addr),
        .seq_wr_data     (seq_wr_data),
        .seq_len         (seq_len),
        .phase_inc_q0    (phase_inc_q0),
        .phase_inc_q1    (phase_inc_q1),
        .phase_offset_q0 (phase_offset_q0),
        .phase_offset_q1 (phase_offset_q1),
        .rf_i_q0         (rf_i_q0),
        .rf_q_q0         (rf_q_q0),
        .rf_i_q1         (rf_i_q1),
        .rf_q_q1         (rf_q_q1),
        .pulse_active    (pulse_active),
        .seq_done        (seq_done),
        .fsm_state       (fsm_state),
        .current_gate    (current_gate),
        .is_two_qubit_out(is_two_qubit_out)
    );

    // ── Clock ─────────────────────────────────────────────────────────────────
    initial clk = 0;
    always #(CLK_PERIOD / 2.0) clk = ~clk;

    // ── CSV output ────────────────────────────────────────────────────────────
    integer csv_fd;
    integer cycle_cnt;

    // ── Helper tasks ──────────────────────────────────────────────────────────
    task write_gate;
        input [3:0] addr;
        input [7:0] instr;
        begin
            @(posedge clk); #0.1;
            seq_wr_en   = 1'b1;
            seq_wr_addr = addr;
            seq_wr_data = instr;
            @(posedge clk); #0.1;
            seq_wr_en   = 1'b0;
        end
    endtask

    // Pack: repeat_cnt[7:5], 1 reserved bit[4], opcode[3:0]
    function [7:0] mk_instr_2q;
        input [2:0] rpt;
        input [3:0] opc;
        begin
            mk_instr_2q = {rpt, 1'b0, opc};
        end
    endfunction

    task run_sequence;
        input integer timeout_cycles;
        integer timeout;
        begin
            @(posedge clk); #0.1;
            start = 1'b1;
            @(posedge clk); #0.1;
            start = 1'b0;

            timeout = 0;
            while (!seq_done && timeout < timeout_cycles) begin
                @(posedge clk); #0.1;
                timeout = timeout + 1;
                $fwrite(csv_fd, "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",
                    cycle_cnt, rf_i_q0, rf_q_q0, rf_i_q1, rf_q_q1,
                    pulse_active, is_two_qubit_out, current_gate, fsm_state);
                cycle_cnt = cycle_cnt + 1;
            end
            // Wait one idle cycle
            repeat(4) @(posedge clk);
        end
    endtask

    // ── Main stimulus ─────────────────────────────────────────────────────────
    initial begin
        // Initialise
        rst_n          = 1'b0;
        start          = 1'b0;
        seq_wr_en      = 1'b0;
        seq_wr_addr    = 4'd0;
        seq_wr_data    = 8'd0;
        seq_len        = 5'd1;
        phase_inc_q0   = PHASE_Q0;
        phase_inc_q1   = PHASE_Q1;
        phase_offset_q0= PHASE_OFFSET;
        phase_offset_q1= PHASE_OFFSET;
        cycle_cnt      = 0;

        // Open CSV
        csv_fd = $fopen("../sim_outputs/iq_2qubit.csv", "w");
        $fwrite(csv_fd, "cycle,rf_i_q0,rf_q_q0,rf_i_q1,rf_q_q1,");
        $fwrite(csv_fd, "pulse_active,is_two_qubit,current_gate,fsm_state\n");

        // Reset
        repeat(10) @(posedge clk);
        rst_n = 1'b1;
        repeat(5)  @(posedge clk);

        // ── Sequence A: Bell state — H0 → CNOT ──────────────────────────────
        $display("\n=== Sequence A: Bell-state preparation (H0 → CNOT) ===");
        write_gate(0, mk_instr_2q(3'd0, 4'b0100));  // H0  opcode 4'b0100
        write_gate(1, mk_instr_2q(3'd0, 4'b1001));  // CNOT opcode 4'b1001
        seq_len = 5'd2;
        run_sequence(3000);

        // ── Sequence B: All single-qubit gates ───────────────────────────────
        $display("\n=== Sequence B: Full single-qubit sweep ===");
        write_gate(0, mk_instr_2q(3'd0, 4'b0001));  // X0
        write_gate(1, mk_instr_2q(3'd0, 4'b0010));  // Y0
        write_gate(2, mk_instr_2q(3'd0, 4'b0011));  // X0/2
        write_gate(3, mk_instr_2q(3'd0, 4'b0100));  // H0
        write_gate(4, mk_instr_2q(3'd0, 4'b0101));  // X1
        write_gate(5, mk_instr_2q(3'd0, 4'b0110));  // Y1
        write_gate(6, mk_instr_2q(3'd0, 4'b0111));  // X1/2
        write_gate(7, mk_instr_2q(3'd0, 4'b1000));  // H1
        seq_len = 5'd8;
        run_sequence(10000);

        // ── Sequence C: CZ Bell prep — X0H → X1H → CZ → X0H → X1H ──────────
        $display("\n=== Sequence C: CZ gate sequence ===");
        write_gate(0, mk_instr_2q(3'd0, 4'b0011));  // X0/2
        write_gate(1, mk_instr_2q(3'd0, 4'b0111));  // X1/2
        write_gate(2, mk_instr_2q(3'd0, 4'b1010));  // CZ
        write_gate(3, mk_instr_2q(3'd0, 4'b0011));  // X0/2
        write_gate(4, mk_instr_2q(3'd0, 4'b0111));  // X1/2
        seq_len = 5'd5;
        run_sequence(5000);

        $fclose(csv_fd);
        $display("\nSimulation complete — output: sim_outputs/iq_2qubit.csv");
        $finish;
    end

endmodule
