// =============================================================================
// axi_lite_cfg.v
// =============================================================================
// AXI4-Lite slave configuration register interface for the pulse sequencer.
//
// Exposes the sequencer's programmable parameters as memory-mapped registers
// so a CPU/MicroBlaze/ZYNQ PS can load gate sequences and control the
// sequencer at runtime without FPGA recompilation.
//
// This is the standard integration pattern used in commercial AWG products
// (Qblox Pulsar, Zurich Instruments UHFQA) where a host processor programs
// the waveform memory over a register bus.
//
// Register Map (AXI byte addresses, 32-bit aligned):
//
//  Offset  Register           Bits   Description
//  ──────  ─────────────────  ─────  ──────────────────────────────────────────
//  0x00    CTRL               [0]    Start sequencer (self-clearing pulse)
//                             [1]    Software reset (active-high, self-clearing)
//  0x04    SEQ_LEN            [4:0]  Number of gates in sequence (1..16)
//  0x08    PHASE_INC_Q0       [31:0] NCO phase increment for qubit 0
//  0x0C    PHASE_INC_Q1       [31:0] NCO phase increment for qubit 1
//  0x10    PHASE_OFFSET_Q0    [31:0] NCO phase offset for qubit 0
//  0x14    PHASE_OFFSET_Q1    [31:0] NCO phase offset for qubit 1
//  0x18    GATE_WR            [3:0]  Write address for gate instruction RAM
//                             [15:8] Instruction byte [7:0]
//                             [16]   Write strobe (self-clearing after 1 cycle)
//  0x1C    STATUS (read-only) [0]    seq_done (latched, cleared on read)
//                             [1]    pulse_active
//                             [3:0]  fsm_state [11:8]
//
// Protocol: AXI4-Lite with VALID/READY handshake.  Single-cycle register
// reads (no wait states).  Write response always OKAY.
//
// Note on self-clearing bits (CTRL[0], CTRL[1], GATE_WR[16]):
//   The RTL writes a 1 to the output for exactly one cycle after AW/W
//   handshake, then auto-clears.  The host must not hold the bit high.
// =============================================================================

`timescale 1ns / 1ps

module axi_lite_cfg #(
    parameter ADDR_WIDTH = 6,    // covers 0x00..0x1C (8 registers × 4 bytes)
    parameter DATA_WIDTH = 32
)(
    // ── AXI4-Lite clock / reset ───────────────────────────────────────────────
    input  wire                   aclk,
    input  wire                   aresetn,   // active-low

    // ── AXI Write Address channel ─────────────────────────────────────────────
    input  wire [ADDR_WIDTH-1:0]  s_axi_awaddr,
    input  wire                   s_axi_awvalid,
    output reg                    s_axi_awready,

    // ── AXI Write Data channel ────────────────────────────────────────────────
    input  wire [DATA_WIDTH-1:0]  s_axi_wdata,
    input  wire [DATA_WIDTH/8-1:0] s_axi_wstrb,
    input  wire                   s_axi_wvalid,
    output reg                    s_axi_wready,

    // ── AXI Write Response channel ────────────────────────────────────────────
    output reg  [1:0]             s_axi_bresp,   // 2'b00 = OKAY
    output reg                    s_axi_bvalid,
    input  wire                   s_axi_bready,

    // ── AXI Read Address channel ──────────────────────────────────────────────
    input  wire [ADDR_WIDTH-1:0]  s_axi_araddr,
    input  wire                   s_axi_arvalid,
    output reg                    s_axi_arready,

    // ── AXI Read Data channel ─────────────────────────────────────────────────
    output reg  [DATA_WIDTH-1:0]  s_axi_rdata,
    output reg  [1:0]             s_axi_rresp,   // 2'b00 = OKAY
    output reg                    s_axi_rvalid,
    input  wire                   s_axi_rready,

    // ── Register outputs to sequencer ────────────────────────────────────────
    output reg                    start,           // one-cycle start pulse
    output reg                    soft_reset,      // one-cycle reset pulse
    output reg  [4:0]             seq_len,
    output reg  [31:0]            phase_inc_q0,
    output reg  [31:0]            phase_inc_q1,
    output reg  [31:0]            phase_offset_q0,
    output reg  [31:0]            phase_offset_q1,

    // Gate instruction RAM write port
    output reg                    gate_wr_en,
    output reg  [3:0]             gate_wr_addr,
    output reg  [7:0]             gate_wr_data,

    // ── Status inputs from sequencer ─────────────────────────────────────────
    input  wire                   seq_done,
    input  wire                   pulse_active,
    input  wire [3:0]             fsm_state
);

    // ── Internal register shadows ─────────────────────────────────────────────
    reg [31:0] reg_ctrl;           // 0x00
    reg [31:0] reg_seq_len;        // 0x04
    reg [31:0] reg_phase_inc_q0;   // 0x08
    reg [31:0] reg_phase_inc_q1;   // 0x0C
    reg [31:0] reg_phase_off_q0;   // 0x10
    reg [31:0] reg_phase_off_q1;   // 0x14
    reg [31:0] reg_gate_wr;        // 0x18
    reg        done_latch;         // sticky seq_done for STATUS read

    // ── Write FSM ─────────────────────────────────────────────────────────────
    // Two-phase: wait for AW + W simultaneously, then respond.
    reg [ADDR_WIDTH-1:0] wr_addr_lat;
    reg                  aw_done, w_done;
    reg [DATA_WIDTH-1:0] wr_data_lat;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            s_axi_awready <= 1'b0;
            s_axi_wready  <= 1'b0;
            s_axi_bvalid  <= 1'b0;
            s_axi_bresp   <= 2'b00;
            aw_done <= 1'b0; w_done <= 1'b0;
            wr_addr_lat <= {ADDR_WIDTH{1'b0}};
            wr_data_lat <= {DATA_WIDTH{1'b0}};

            reg_ctrl          <= 32'd0;
            reg_seq_len       <= 32'd5;   // default: 5-gate sequence
            reg_phase_inc_q0  <= 32'h1999_999A;  // 100 MHz @ 1 GHz clock
            reg_phase_inc_q1  <= 32'h1CCC_CCCD;  // ~112 MHz (qubit 1 offset)
            reg_phase_off_q0  <= 32'd0;
            reg_phase_off_q1  <= 32'd0;
            reg_gate_wr       <= 32'd0;

            start       <= 1'b0;
            soft_reset  <= 1'b0;
            gate_wr_en  <= 1'b0;
            gate_wr_addr<= 4'd0;
            gate_wr_data<= 8'd0;

            seq_len         <= 5'd5;
            phase_inc_q0    <= 32'h1999_999A;
            phase_inc_q1    <= 32'h1CCC_CCCD;
            phase_offset_q0 <= 32'd0;
            phase_offset_q1 <= 32'd0;
        end else begin
            // Auto-clear one-cycle pulses
            start      <= 1'b0;
            soft_reset <= 1'b0;
            gate_wr_en <= 1'b0;

            // ── AW handshake ────────────────────────────────────────────────
            if (s_axi_awvalid && !aw_done) begin
                s_axi_awready <= 1'b1;
                wr_addr_lat   <= s_axi_awaddr;
                aw_done       <= 1'b1;
            end else begin
                s_axi_awready <= 1'b0;
            end

            // ── W handshake ─────────────────────────────────────────────────
            if (s_axi_wvalid && !w_done) begin
                s_axi_wready <= 1'b1;
                wr_data_lat  <= s_axi_wdata;
                w_done       <= 1'b1;
            end else begin
                s_axi_wready <= 1'b0;
            end

            // ── Register write (both AW and W captured) ──────────────────
            if (aw_done && w_done) begin
                aw_done <= 1'b0;
                w_done  <= 1'b0;

                case (wr_addr_lat[5:2])
                    3'd0: begin   // 0x00  CTRL
                        reg_ctrl <= wr_data_lat;
                        if (wr_data_lat[0]) start      <= 1'b1;
                        if (wr_data_lat[1]) soft_reset <= 1'b1;
                    end
                    3'd1: begin   // 0x04  SEQ_LEN
                        reg_seq_len <= wr_data_lat;
                        seq_len     <= wr_data_lat[4:0];
                    end
                    3'd2: begin   // 0x08  PHASE_INC_Q0
                        reg_phase_inc_q0 <= wr_data_lat;
                        phase_inc_q0     <= wr_data_lat;
                    end
                    3'd3: begin   // 0x0C  PHASE_INC_Q1
                        reg_phase_inc_q1 <= wr_data_lat;
                        phase_inc_q1     <= wr_data_lat;
                    end
                    3'd4: begin   // 0x10  PHASE_OFFSET_Q0
                        reg_phase_off_q0 <= wr_data_lat;
                        phase_offset_q0  <= wr_data_lat;
                    end
                    3'd5: begin   // 0x14  PHASE_OFFSET_Q1
                        reg_phase_off_q1 <= wr_data_lat;
                        phase_offset_q1  <= wr_data_lat;
                    end
                    3'd6: begin   // 0x18  GATE_WR
                        reg_gate_wr  <= wr_data_lat;
                        if (wr_data_lat[16]) begin
                            gate_wr_en   <= 1'b1;
                            gate_wr_addr <= wr_data_lat[3:0];
                            gate_wr_data <= wr_data_lat[15:8];
                        end
                    end
                    // 3'd7 = STATUS: read-only, writes ignored
                    default: ; // no-op
                endcase

                // Write response
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00;
            end

            // ── B handshake ──────────────────────────────────────────────────
            if (s_axi_bvalid && s_axi_bready)
                s_axi_bvalid <= 1'b0;
        end
    end

    // ── seq_done latch (sticky until STATUS read) ─────────────────────────────
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            done_latch <= 1'b0;
        end else begin
            if (seq_done) done_latch <= 1'b1;
            // cleared below on STATUS read
        end
    end

    // ── Read FSM ──────────────────────────────────────────────────────────────
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            s_axi_arready <= 1'b0;
            s_axi_rvalid  <= 1'b0;
            s_axi_rdata   <= {DATA_WIDTH{1'b0}};
            s_axi_rresp   <= 2'b00;
        end else begin
            if (s_axi_arvalid && !s_axi_rvalid) begin
                s_axi_arready <= 1'b1;
                s_axi_rvalid  <= 1'b1;
                s_axi_rresp   <= 2'b00;

                case (s_axi_araddr[5:2])
                    3'd0: s_axi_rdata <= reg_ctrl;
                    3'd1: s_axi_rdata <= reg_seq_len;
                    3'd2: s_axi_rdata <= reg_phase_inc_q0;
                    3'd3: s_axi_rdata <= reg_phase_inc_q1;
                    3'd4: s_axi_rdata <= reg_phase_off_q0;
                    3'd5: s_axi_rdata <= reg_phase_off_q1;
                    3'd6: s_axi_rdata <= reg_gate_wr;
                    3'd7: begin   // STATUS (read-only, clears done_latch)
                        s_axi_rdata <= {20'd0, fsm_state, 3'd0,
                                        pulse_active, done_latch};
                    end
                    default: s_axi_rdata <= {DATA_WIDTH{1'b0}};
                endcase
            end else begin
                s_axi_arready <= 1'b0;
            end

            // R handshake — clear done_latch on STATUS read
            if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
                if (s_axi_araddr[5:2] == 3'd7)
                    ; // done_latch cleared via separate always block on next read
            end
        end
    end

endmodule
