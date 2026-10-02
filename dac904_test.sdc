create_clock -name clk_50m -period 20.000 [get_ports {clk_50m}]
create_generated_clock -name dac_latch_clk \
    -source [get_ports {clk_50m}] -divide_by 2 [get_ports {dac_clk}]
derive_clock_uncertainty

# DAC data changes at the falling edge of the generated clock and has 20 ns
# to settle before the next rising latch edge.  Device requirements are
# 1.0 ns setup and 1.5 ns hold.
set_output_delay -clock dac_latch_clk -max 1.000 [get_ports {dac_data[*]}]
set_output_delay -clock dac_latch_clk -min -1.500 [get_ports {dac_data[*]}]
