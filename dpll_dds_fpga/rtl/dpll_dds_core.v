// Standalone automatic 1..100 kHz DPLL/DDS signal path.
module dpll_dds_core #(
    parameter integer ADC_ZERO_CODE = 2104,
    parameter integer DAC_ZERO_CODE = 8279,
    parameter integer LPF_SHIFT = 16,
    parameter integer LOOP_DECIMATION_LOG2 = 10,
    parameter ROM_FILE = "../rtl/sine_1024x16.hex"
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        sample_valid,
    input  wire [11:0] adc_sample,
    input  wire        adc_otr,
    input  wire [31:0] phase_lag_word,
    input  wire [31:0] calibration_phase_word,
    input  wire [13:0] amplitude_code,
    output wire        output_valid,
    output wire [13:0] dac_code,
    output wire        dac_clipped,
    output wire        locked,
    output wire        signal_present,
    output wire [31:0] phase_increment,
    output wire signed [23:0] phase_error,
    output wire signed [23:0] signal_magnitude,
    output wire        coarse_frequency_valid
);
    wire signed [12:0] centered_sample =
        $signed({1'b0, adc_sample}) - $signed(ADC_ZERO_CODE);

    wire coarse_crossing;
    wire [31:0] coarse_phase_increment;
    coarse_frequency_estimator coarse_estimator (
        .clk(clk), .rst(rst), .sample_valid(sample_valid && !adc_otr),
        .sample_centered(centered_sample), .crossing(coarse_crossing),
        .frequency_valid(coarse_frequency_valid),
        .phase_increment(coarse_phase_increment)
    );

    reg [31:0] phase_accumulator = 32'd0;
    wire detector_valid;
    wire signed [23:0] i_filtered;
    wire signed [23:0] q_filtered;
    iq_phase_detector #(
        .LPF_SHIFT(LPF_SHIFT), .ROM_FILE(ROM_FILE)
    ) detector (
        .clk(clk), .rst(rst), .sample_valid(sample_valid && !adc_otr),
        .sample_centered(centered_sample), .local_phase(phase_accumulator),
        .phase_valid(detector_valid), .phase_error(phase_error),
        .signal_magnitude(signal_magnitude),
        .i_filtered(i_filtered), .q_filtered(q_filtered)
    );

    wire reacquired;
    dpll_controller #(
        .LOOP_DECIMATION_LOG2(LOOP_DECIMATION_LOG2)
    ) controller (
        .clk(clk), .rst(rst),
        .phase_valid(detector_valid), .phase_error(phase_error),
        .signal_magnitude(signal_magnitude),
        .coarse_valid(coarse_frequency_valid),
        .coarse_phase_increment(coarse_phase_increment),
        .phase_increment(phase_increment), .locked(locked),
        .signal_present(signal_present), .reacquired(reacquired)
    );

    always @(posedge clk) begin
        if (rst)
            phase_accumulator <= 32'd0;
        else if (coarse_crossing && coarse_phase_increment != 0 && !locked)
            // During acquisition, hard-align the NCO at each measured
            // positive crossing. Fine phase control takes over after lock.
            phase_accumulator <= 32'd0;
        else if (sample_valid)
            phase_accumulator <= phase_accumulator + phase_increment;
    end

    // Advance by two sample intervals to compensate the two-stage DDS mapper.
    // Remaining converter/analog latency is represented by the configurable
    // calibration phase word. Positive phase_lag_word means output lag.
    wire [31:0] output_phase =
        phase_accumulator - phase_lag_word + calibration_phase_word +
        {phase_increment[30:0], 1'b0};

    dds_output #(.DAC_ZERO_CODE(DAC_ZERO_CODE), .ROM_FILE(ROM_FILE)) output_dds (
        .clk(clk), .rst(rst), .sample_valid(sample_valid),
        .phase_word(output_phase), .amplitude_code(amplitude_code),
        .output_valid(output_valid), .dac_code(dac_code),
        .clipped(dac_clipped)
    );
endmodule

