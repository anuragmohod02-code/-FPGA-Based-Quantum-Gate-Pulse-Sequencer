## =============================================================================
## pulse_sequencer.xdc
## Xilinx Vivado constraints for top_pulse_sequencer.v
## Target: Nexys A7-100T (xc7a35t-csg324-1)
## =============================================================================
##
## Pin assignments use the Digilent Nexys A7-100T board (the most common
## Artix-7 university dev board).  Adjust net names if using a different board.
##
## To use:
##   Vivado → Add Sources → Constraint Files → add this file
##   (Ensure project part matches xc7a35t-csg324-1)
## =============================================================================

## ---------------------------------------------------------------------------
## Clock: 100 MHz on-board oscillator (W5)
## ---------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN W5   IOSTANDARD LVCMOS33 } [get_ports clk]
create_clock -add -name sys_clk_pin -period 10.000 -waveform {0 5} [get_ports clk]

## ---------------------------------------------------------------------------
## Reset: CPU RESET button (active-low, maps to BTN0)
## ---------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN U18  IOSTANDARD LVCMOS33 } [get_ports rst_n]

## ---------------------------------------------------------------------------
## Sequence control: mapped to on-board push-buttons
## ---------------------------------------------------------------------------
## start        → BTNC (centre button, T18)
## seq_wr_en    → BTNR (right  button, U17)  -- used during programming
set_property -dict { PACKAGE_PIN T18  IOSTANDARD LVCMOS33 } [get_ports start]
set_property -dict { PACKAGE_PIN U17  IOSTANDARD LVCMOS33 } [get_ports seq_wr_en]

## ---------------------------------------------------------------------------
## Instruction bus: seq_wr_addr[3:0] + seq_wr_data[7:0] → SW[11:0]
## ---------------------------------------------------------------------------
## SW[3:0]  = seq_wr_addr[3:0]
set_property -dict { PACKAGE_PIN V17  IOSTANDARD LVCMOS33 } [get_ports {seq_wr_addr[0]}]
set_property -dict { PACKAGE_PIN V16  IOSTANDARD LVCMOS33 } [get_ports {seq_wr_addr[1]}]
set_property -dict { PACKAGE_PIN W16  IOSTANDARD LVCMOS33 } [get_ports {seq_wr_addr[2]}]
set_property -dict { PACKAGE_PIN W17  IOSTANDARD LVCMOS33 } [get_ports {seq_wr_addr[3]}]

## SW[7:4]  = seq_wr_data[2:0] (gate opcode) + seq_wr_data[4] (reserved)
set_property -dict { PACKAGE_PIN W15  IOSTANDARD LVCMOS33 } [get_ports {seq_wr_data[0]}]
set_property -dict { PACKAGE_PIN V15  IOSTANDARD LVCMOS33 } [get_ports {seq_wr_data[1]}]
set_property -dict { PACKAGE_PIN W14  IOSTANDARD LVCMOS33 } [get_ports {seq_wr_data[2]}]
set_property -dict { PACKAGE_PIN W13  IOSTANDARD LVCMOS33 } [get_ports {seq_wr_data[3]}]

## SW[10:8] = seq_wr_data[7:5] (repeat_cnt)
set_property -dict { PACKAGE_PIN V2   IOSTANDARD LVCMOS33 } [get_ports {seq_wr_data[4]}]
set_property -dict { PACKAGE_PIN T3   IOSTANDARD LVCMOS33 } [get_ports {seq_wr_data[5]}]
set_property -dict { PACKAGE_PIN T2   IOSTANDARD LVCMOS33 } [get_ports {seq_wr_data[6]}]
set_property -dict { PACKAGE_PIN R3   IOSTANDARD LVCMOS33 } [get_ports {seq_wr_data[7]}]

## SW[11:12] = seq_len[1:0]  (lower 2 bits; tie upper bits to GND in RTL or use more SWs)
set_property -dict { PACKAGE_PIN W2   IOSTANDARD LVCMOS33 } [get_ports {seq_len[0]}]
set_property -dict { PACKAGE_PIN U1   IOSTANDARD LVCMOS33 } [get_ports {seq_len[1]}]
set_property -dict { PACKAGE_PIN T1   IOSTANDARD LVCMOS33 } [get_ports {seq_len[2]}]
set_property -dict { PACKAGE_PIN R2   IOSTANDARD LVCMOS33 } [get_ports {seq_len[3]}]
set_property -dict { PACKAGE_PIN R1   IOSTANDARD LVCMOS33 } [get_ports {seq_len[4]}]

## ---------------------------------------------------------------------------
## Status outputs → on-board LEDs
## ---------------------------------------------------------------------------
## pulse_active → LD0
## seq_done     → LD1
## fsm_state[0] → LD2   (LSB of state vector for debug)
## fsm_state[1] → LD3
## fsm_state[2] → LD4
## fsm_state[3] → LD5
set_property -dict { PACKAGE_PIN U16  IOSTANDARD LVCMOS33 } [get_ports pulse_active]
set_property -dict { PACKAGE_PIN E19  IOSTANDARD LVCMOS33 } [get_ports seq_done]
set_property -dict { PACKAGE_PIN U19  IOSTANDARD LVCMOS33 } [get_ports {fsm_state[0]}]
set_property -dict { PACKAGE_PIN V19  IOSTANDARD LVCMOS33 } [get_ports {fsm_state[1]}]
set_property -dict { PACKAGE_PIN W18  IOSTANDARD LVCMOS33 } [get_ports {fsm_state[2]}]
set_property -dict { PACKAGE_PIN U15  IOSTANDARD LVCMOS33 } [get_ports {fsm_state[3]}]

## ---------------------------------------------------------------------------
## IQ output: rf_i_out[15:0] and rf_q_out[15:0] → PMOD JA + PMOD JB
## (Useful for connecting an external PMOD R2R DAC, e.g., Digilent Pmod DA4)
## ---------------------------------------------------------------------------
## PMOD JA (pins 1-4, 7-10) → rf_i_out[7:0]  (lower byte)
set_property -dict { PACKAGE_PIN J1   IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[0]}]
set_property -dict { PACKAGE_PIN L2   IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[1]}]
set_property -dict { PACKAGE_PIN J2   IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[2]}]
set_property -dict { PACKAGE_PIN G2   IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[3]}]
set_property -dict { PACKAGE_PIN H1   IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[4]}]
set_property -dict { PACKAGE_PIN K2   IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[5]}]
set_property -dict { PACKAGE_PIN H2   IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[6]}]
set_property -dict { PACKAGE_PIN G3   IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[7]}]

## PMOD JB (pins 1-4, 7-10) → rf_q_out[7:0]  (lower byte, upper 8 bits omitted for PMOD)
set_property -dict { PACKAGE_PIN A14  IOSTANDARD LVCMOS33 } [get_ports {rf_q_out[0]}]
set_property -dict { PACKAGE_PIN A16  IOSTANDARD LVCMOS33 } [get_ports {rf_q_out[1]}]
set_property -dict { PACKAGE_PIN B15  IOSTANDARD LVCMOS33 } [get_ports {rf_q_out[2]}]
set_property -dict { PACKAGE_PIN B16  IOSTANDARD LVCMOS33 } [get_ports {rf_q_out[3]}]
set_property -dict { PACKAGE_PIN A15  IOSTANDARD LVCMOS33 } [get_ports {rf_q_out[4]}]
set_property -dict { PACKAGE_PIN A17  IOSTANDARD LVCMOS33 } [get_ports {rf_q_out[5]}]
set_property -dict { PACKAGE_PIN C15  IOSTANDARD LVCMOS33 } [get_ports {rf_q_out[6]}]
set_property -dict { PACKAGE_PIN C16  IOSTANDARD LVCMOS33 } [get_ports {rf_q_out[7]}]

## Upper bytes of rf_i/rf_q (bits 15:8) — connect to remaining PMOD pins or leave unconnected
## Uncomment and remap if using a 16-bit PMOD DAC:
# set_property -dict { PACKAGE_PIN ... IOSTANDARD LVCMOS33 } [get_ports {rf_i_out[8]}]
# ... (add remaining 8 pins per available PMOD header)

## ---------------------------------------------------------------------------
## Timing constraints
## ---------------------------------------------------------------------------
## Set all I/O paths as false paths (no external timing reference)
## In a real deployment, replace with set_input_delay / set_output_delay
## relative to a system reference clock.
set_false_path -from [get_ports rst_n]
set_false_path -from [get_ports start]
set_false_path -from [get_ports seq_wr_en]
set_false_path -from [get_ports {seq_wr_addr[*]}]
set_false_path -from [get_ports {seq_wr_data[*]}]
set_false_path -from [get_ports {seq_len[*]}]
set_false_path -from [get_ports {phase_inc[*]}]
set_false_path -from [get_ports {phase_offset[*]}]
set_false_path -to   [get_ports {rf_i_out[*]}]
set_false_path -to   [get_ports {rf_q_out[*]}]
set_false_path -to   [get_ports pulse_active]
set_false_path -to   [get_ports seq_done]
set_false_path -to   [get_ports {fsm_state[*]}]

## ---------------------------------------------------------------------------
## Configuration voltage (suppresses DRC warning)
## ---------------------------------------------------------------------------
set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
