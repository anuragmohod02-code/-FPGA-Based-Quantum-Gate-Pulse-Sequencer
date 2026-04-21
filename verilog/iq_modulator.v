// =============================================================================
// iq_modulator.v
// =============================================================================
// Multiplies the DRAG pulse envelope samples (I_env, Q_env from pulse_rom)
// against the NCO carrier (cos_carrier, sin_carrier) and produces the final
// up-converted I/Q output streams.
//
// Modulation equations:
//   RF_I(t) = I_env(t) * cos(ωt) − Q_env(t) * sin(ωt)
//   RF_Q(t) = I_env(t) * sin(ωt) + Q_env(t) * cos(ωt)
//
// This implements single-sideband (SSB) IQ modulation, which is the standard
// method for generating microwave drive pulses in superconducting qubit control.
//
// Arithmetic:
//   Inputs: 16-bit signed integers (envelope and carrier both in [-32767, 32767])
//   Products: 32-bit signed (16×16 = 32 bits full precision)
//   Output: upper 16 bits after arithmetic right shift by 15
//           (equivalent to dividing by 2^15, keeping result in [-32767, 32767])
//
// Ports:
//   clk         - system clock
//   enable      - modulation active; when low outputs are zeroed
//   i_env       - 16-bit signed DRAG I envelope (from pulse_rom)
//   q_env       - 16-bit signed DRAG Q envelope (from pulse_rom)
//   cos_carrier - 16-bit signed cosine from NCO
//   sin_carrier - 16-bit signed sine  from NCO
//   rf_i_out    - 16-bit signed modulated I output
//   rf_q_out    - 16-bit signed modulated Q output
//   valid_out   - registered valid flag (one cycle latency)
// =============================================================================

`timescale 1ns / 1ps

module iq_modulator #(
    parameter DATA_WIDTH = 16
)(
    input  wire                          clk,
    input  wire                          enable,
    input  wire signed [DATA_WIDTH-1:0]  i_env,
    input  wire signed [DATA_WIDTH-1:0]  q_env,
    input  wire signed [DATA_WIDTH-1:0]  cos_carrier,
    input  wire signed [DATA_WIDTH-1:0]  sin_carrier,
    output reg  signed [DATA_WIDTH-1:0]  rf_i_out,
    output reg  signed [DATA_WIDTH-1:0]  rf_q_out,
    output reg                           valid_out
);

    localparam PROD_WIDTH = DATA_WIDTH * 2;  // 32

    // -------------------------------------------------------------------------
    // Stage 1: four multiplications (registered)
    // -------------------------------------------------------------------------
    reg signed [PROD_WIDTH-1:0] p_i_cos;   // I_env  * cos
    reg signed [PROD_WIDTH-1:0] p_q_sin;   // Q_env  * sin
    reg signed [PROD_WIDTH-1:0] p_i_sin;   // I_env  * sin
    reg signed [PROD_WIDTH-1:0] p_q_cos;   // Q_env  * cos
    reg                         valid_s1;

    always @(posedge clk) begin
        if (!enable) begin
            p_i_cos  <= {PROD_WIDTH{1'b0}};
            p_q_sin  <= {PROD_WIDTH{1'b0}};
            p_i_sin  <= {PROD_WIDTH{1'b0}};
            p_q_cos  <= {PROD_WIDTH{1'b0}};
            valid_s1 <= 1'b0;
        end else begin
            p_i_cos  <= i_env * cos_carrier;
            p_q_sin  <= q_env * sin_carrier;
            p_i_sin  <= i_env * sin_carrier;
            p_q_cos  <= q_env * cos_carrier;
            valid_s1 <= 1'b1;
        end
    end

    // -------------------------------------------------------------------------
    // Stage 2: sum/difference and truncate to DATA_WIDTH
    //   RF_I = I_env*cos − Q_env*sin
    //   RF_Q = I_env*sin + Q_env*cos
    // Arithmetic right-shift by 15 to normalise back to 16-bit range
    // -------------------------------------------------------------------------
    reg signed [PROD_WIDTH-1:0] sum_i;
    reg signed [PROD_WIDTH-1:0] sum_q;

    always @(posedge clk) begin
        sum_i     <= p_i_cos - p_q_sin;
        sum_q     <= p_i_sin + p_q_cos;
        valid_out <= valid_s1;
    end

    // -------------------------------------------------------------------------
    // Stage 3: truncation (combinatorial slice after stage 2 register)
    // Saturate to avoid wrap-around on corner cases
    // -------------------------------------------------------------------------
    wire signed [PROD_WIDTH-1:0] trunc_i = sum_i >>> 15;
    wire signed [PROD_WIDTH-1:0] trunc_q = sum_q >>> 15;

    localparam signed [DATA_WIDTH-1:0] SAT_POS =  32767;
    localparam signed [DATA_WIDTH-1:0] SAT_NEG = -32768;

    always @(posedge clk) begin
        // Saturating truncation for I
        if      (trunc_i > {{(PROD_WIDTH-DATA_WIDTH){1'b0}}, SAT_POS})
            rf_i_out <= SAT_POS;
        else if (trunc_i < {{(PROD_WIDTH-DATA_WIDTH){1'b1}}, SAT_NEG})
            rf_i_out <= SAT_NEG;
        else
            rf_i_out <= trunc_i[DATA_WIDTH-1:0];

        // Saturating truncation for Q
        if      (trunc_q > {{(PROD_WIDTH-DATA_WIDTH){1'b0}}, SAT_POS})
            rf_q_out <= SAT_POS;
        else if (trunc_q < {{(PROD_WIDTH-DATA_WIDTH){1'b1}}, SAT_NEG})
            rf_q_out <= SAT_NEG;
        else
            rf_q_out <= trunc_q[DATA_WIDTH-1:0];
    end

endmodule
