module dpll_dds_top #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer SAMPLE_HZ = 25_000_000,
    parameter integer UART_BAUD = 2_000_000,
    parameter ROM_FILE = "../rtl/sine_1024x16.hex"
) (
    input  wire        clk_50m,
    input  wire [11:0] adc_data,
    input  wire        adc_otr,
    output wire        adc_clk,
    output wire        dac_clk,
    output wire [13:0] dac_data,
    output wire        uart_tx,
    input  wire        uart_rx
);
    reg [7:0] reset_count = 8'd0;
    wire rst = ~&reset_count;
    always @(posedge clk_50m)
        if (rst)
            reset_count <= reset_count + 1'b1;

    wire [11:0] sample_data;
    wire sample_otr;
    wire sample_valid;
    dpll_adc_capture #(.CLK_HZ(CLK_HZ), .SAMPLE_HZ(SAMPLE_HZ)) adc_interface (
        .clk(clk_50m), .rst(rst), .adc_data(adc_data), .adc_otr(adc_otr),
        .adc_clk(adc_clk), .sample_data(sample_data),
        .sample_otr(sample_otr), .sample_valid(sample_valid)
    );

    wire [31:0] phase_lag_word;
    wire [31:0] calibration_phase_word;
    wire [13:0] configured_amplitude;
    wire output_enable;
    wire config_valid;
    wire locked;
    wire signal_present;
    wire [31:0] phase_increment;
    wire signed [23:0] phase_error;
    wire signed [23:0] signal_magnitude;
    wire coarse_valid;
    wire mapped_valid;
    wire [13:0] mapped_code;
    wire dac_clipped;

    dpll_dds_core #(.ROM_FILE(ROM_FILE)) signal_core (
        .clk(clk_50m), .rst(rst), .sample_valid(sample_valid),
        .adc_sample(sample_data), .adc_otr(sample_otr),
        .phase_lag_word(phase_lag_word),
        .calibration_phase_word(calibration_phase_word),
        // Hold the physical output at calibrated zero until the loop locks.
        .amplitude_code((output_enable && locked) ? configured_amplitude : 14'd0),
        .output_valid(mapped_valid), .dac_code(mapped_code),
        .dac_clipped(dac_clipped), .locked(locked),
        .signal_present(signal_present), .phase_increment(phase_increment),
        .phase_error(phase_error), .signal_magnitude(signal_magnitude),
        .coarse_frequency_valid(coarse_valid)
    );

    dpll_dac_output #(.RESET_CODE(14'd8279)) dac_interface (
        .clk(clk_50m), .rst(rst), .sample_clk(adc_clk),
        .code_valid(mapped_valid), .dac_code(mapped_code),
        .dac_clk(dac_clk), .dac_data(dac_data)
    );

    dpll_uart_bridge #(.CLK_HZ(CLK_HZ), .BAUD(UART_BAUD)) control_link (
        .clk(clk_50m), .rst(rst), .uart_rx(uart_rx), .uart_tx(uart_tx),
        .phase_lag_word(phase_lag_word),
        .calibration_phase_word(calibration_phase_word),
        .amplitude_code(configured_amplitude), .output_enable(output_enable),
        .config_valid(config_valid), .phase_increment(phase_increment),
        .phase_error(phase_error), .locked(locked),
        .signal_present(signal_present), .dac_clipped(dac_clipped),
        .adc_otr(sample_otr), .dac_code(mapped_code)
    );
endmodule

