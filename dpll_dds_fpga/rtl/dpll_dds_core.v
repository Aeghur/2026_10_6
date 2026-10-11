// Standalone automatic 1..100 kHz DPLL/DDS signal path.
module dpll_dds_core #(
    parameter integer ADC_ZERO_CODE = 2104,
    parameter integer DAC_ZERO_CODE = 8279,
    parameter integer LPF_SHIFT = 14,
    parameter integer LOOP_DECIMATION_LOG2 = 10,
    parameter integer ADC_PIPELINE_SAMPLES = 7,
    parameter integer DDS_PIPELINE_SAMPLES = 2,
    parameter ROM_FILE = "../rtl/sine_1024x16.hex"
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        sample_valid,
    input  wire [11:0] adc_sample,
    input  wire        adc_otr,
    input  wire [1:0]  loop_profile,
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
    output wire signed [23:0] i_filtered_debug,
    output wire signed [23:0] q_filtered_debug,
    output wire signed [31:0] integrator_debug,
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
    assign i_filtered_debug = i_filtered;
    assign q_filtered_debug = q_filtered;
    iq_phase_detector #(
        .LPF_SHIFT(LPF_SHIFT), .ROM_FILE(ROM_FILE)
    ) detector (
        .clk(clk), .rst(rst), .sample_valid(sample_valid && !adc_otr),
        .loop_profile(loop_profile),
        .sample_centered(centered_sample), .local_phase(phase_accumulator),
        .phase_valid(detector_valid), .phase_error(phase_error),
        .signal_magnitude(signal_magnitude),
        .i_filtered(i_filtered), .q_filtered(q_filtered)
    );

    wire reacquired;
    dpll_controller #(
        .LOOP_DECIMATION_LOG2(LOOP_DECIMATION_LOG2)
    ) controller (
        .clk(clk), .rst(rst), .loop_profile(loop_profile),
        .phase_valid(detector_valid), .phase_error(phase_error),
        .signal_magnitude(signal_magnitude),
        .coarse_valid(coarse_frequency_valid),
        .coarse_phase_increment(coarse_phase_increment),
        .phase_increment(phase_increment), .locked(locked),
        .signal_present(signal_present), .reacquired(reacquired),
        .integrator_debug(integrator_debug)
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

    // AD9226 data represents an analog sample from seven sample clocks ago.
    // Advance the synthesized output by those seven intervals, plus the two
    // intervals consumed by the DDS mapper.  The multiplication is by the
    // compile-time constant nine and wraps naturally in modulo-2^32 phase.
    localparam integer PIPELINE_ADVANCE_SAMPLES =
        ADC_PIPELINE_SAMPLES + DDS_PIPELINE_SAMPLES;
    wire [31:0] pipeline_advance_word =
        phase_increment * PIPELINE_ADVANCE_SAMPLES;

    // Remaining fractional converter/analog delay is represented by the
    // configurable calibration phase word. Positive phase_lag_word means
    // output lag.
    wire [31:0] output_phase =
        phase_accumulator - phase_lag_word + calibration_phase_word +
        pipeline_advance_word;

    dds_output #(.DAC_ZERO_CODE(DAC_ZERO_CODE), .ROM_FILE(ROM_FILE)) output_dds (
        .clk(clk), .rst(rst), .sample_valid(sample_valid),
        .phase_word(output_phase), .amplitude_code(amplitude_code),
        .output_valid(output_valid), .dac_code(dac_code),
        .clipped(dac_clipped)
    );
endmodule

