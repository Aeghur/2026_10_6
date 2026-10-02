create_clock -name clk_50m -period 20.000 [get_ports {clk_50m}]

create_generated_clock -name adc_sample_clk \
    -source [get_ports {clk_50m}] -divide_by 2 \
    [get_registers {dpll_adc_capture:adc_interface|adc_clk}]
create_generated_clock -name dac_latch_clk \
    -source [get_registers {dpll_adc_capture:adc_interface|adc_clk}] \
    -divide_by 1 -invert [get_ports {dac_clk}]

derive_clock_uncertainty

set_input_delay -clock clk_50m -max 7.000 [get_ports {adc_data[*] adc_otr}]
set_input_delay -clock clk_50m -min 3.500 [get_ports {adc_data[*] adc_otr}]
set_output_delay -clock dac_latch_clk -max 1.000 [get_ports {dac_data[*]}]
set_output_delay -clock dac_latch_clk -min -1.500 [get_ports {dac_data[*]}]

set_false_path -to [get_ports {uart_tx}]
set_false_path -from [get_ports {uart_rx}]
set_false_path -to [get_ports {adc_clk}]

