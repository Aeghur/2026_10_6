create_clock -name clk_50m -period 20.000 [get_ports {clk_50m}]

# Constrain the actual divider register used internally as a clock, then the
# inverted external DAC latch clock derived from it.
create_generated_clock -name adc_sample_clk \
    -source [get_ports {clk_50m}] -divide_by 2 \
    [get_registers {ad9226_capture:adc_capture_inst|adc_clk}]
create_generated_clock -name dac_latch_clk \
    -source [get_registers {ad9226_capture:adc_capture_inst|adc_clk}] \
    -divide_by 1 -invert [get_ports {dac_clk}]

derive_clock_uncertainty

# AD9226 updates adc_data 3.5 ns to 7 ns after its rising sampling edge.
# The RTL captures it on the following falling edge, roughly 20 ns later.
set_input_delay -clock clk_50m -max 7.000 [get_ports {adc_data[*] adc_otr}]
set_input_delay -clock clk_50m -min 3.500 [get_ports {adc_data[*] adc_otr}]

# DAC904 rising-edge latch requirements: 1.0 ns setup, 1.5 ns hold.
set_output_delay -clock dac_latch_clk -max 1.000 [get_ports {dac_data[*]}]
set_output_delay -clock dac_latch_clk -min -1.500 [get_ports {dac_data[*]}]

# UART is asynchronous to the receiver and has no board-synchronous capture
# clock to constrain in TimeQuest.
set_false_path -to [get_ports {uart_tx}]
set_false_path -from [get_ports {uart_rx}]

# T14 is asynchronous; the first two 50 MHz registers synchronize it.
set_false_path -from [get_ports {comparator_in}]

# Multiplexed seven-segment outputs have no board-synchronous receiver clock.
set_false_path -to [get_ports {digitron_out[*] digitron_cs_n[*]}]

# adc_clk is itself the forwarded sampling clock, not data captured by another
# board-level clock. Its period and pulse width are covered by adc_sample_clk.
set_false_path -to [get_ports {adc_clk}]
