; Vivado XSim simulation run script for tb_pulse_sequencer
; Run from the Vivado TCL console after launching simulation:
;
;   source {path/to/Project1_FPGA_PulseSequencer/vivado_sim_run.tcl}

# Create output directory if it doesn't exist
file mkdir [file join [get_property DIRECTORY [current_project]] ../../sim_outputs]

# Run simulation for 12 microseconds
run 12us

# Print completion message
puts "=== Simulation complete ==="
puts "CSV written to sim_outputs/iq_output.csv"
puts "Open it in plot_sim_output.py to generate plots."
