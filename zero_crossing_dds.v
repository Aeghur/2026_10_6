// Lightweight phase-anchored DDS for a single sine input.
//
// T14 measurement supplies the frequency-derived phase step. The MSPM0
// supplies the requested lag. A clean comparator rising edge keeps the NCO
// phase referenced to the live input. The accumulator is never stepped after
// acquisition: a lightweight type-II loop instead adjusts its increment so
// phase corrections remain continuous at the DAC.  The input peak envelope
// is tracked locally so the generated waveform follows the input amplitude.
module zero_crossing_dds #(
    parameter integer ADC_ZERO_CODE = 2104,
    parameter integer SAMPLE_HZ = 25_000_000,
    parameter integer SIGNAL_THRESHOLD_CODES = 16,
    parameter integer LOOP_KP_EXTRA_SHIFT = 2,
    parameter integer LOOP_KI_EXTRA_SHIFT = 6,
    parameter integer FREQUENCY_TRIM_LIMIT_SHIFT = 2,
    parameter integer RAMP_INCREMENT = 4,
    parameter integer PIPELINE_ADVANCE_SAMPLES = 10,
    parameter integer PHASE_CALIBRATION_LAG_CDEG = 0,
    parameter integer PHASE_CALIBRATION_DELAY_SAMPLES = 0,
    parameter ROM_FILE = "dpll_dds_fpga/rtl/sine_1024x16.hex"
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        enable,
    input  wire        config_valid,
    input  wire [31:0] config_phase_step,
    input  wire [31:0] config_phase_lag,
    input  wire        reference_edge,
    input  wire        sample_valid,
    input  wire [11:0] sample_data,
    output reg         output_valid = 1'b0,
    output reg  [11:0] output_sample = ADC_ZERO_CODE[11:0],
    output reg         locked = 1'b0,
    output wire        signal_present
);
    localparam integer LOST_CROSSING_SAMPLES = SAMPLE_HZ / 700;

    reg [31:0] phase_step = 32'd0;
    reg [31:0] phase_lag = 32'd0;
    reg [31:0] applied_phase_lag = 32'd0;
    reg [31:0] carrier_phase = 32'd0;
    reg signed [32:0] frequency_trim = 33'sd0;
    reg signed [32:0] proportional_trim = 33'sd0;
    reg signed [32:0] loop_error_shift = 33'sd0;
    reg [4:0] loop_shift_count = 5'd0;
    reg loop_update_pending = 1'b0;
    reg reference_pending = 1'b0;
    reg [15:0] samples_since_crossing = 16'hffff;

    localparam signed [12:0] ADC_ZERO_SIGNED = ADC_ZERO_CODE[12:0];
    wire signed [12:0] centered_sample =
        $signed({1'b0, sample_data}) - ADC_ZERO_SIGNED;
    wire [12:0] absolute_sample_wide = centered_sample[12] ?
        -centered_sample : centered_sample;
    wire [11:0] absolute_sample = absolute_sample_wide[11:0];

    reg [11:0] envelope = 12'd0;
    reg [11:0] decay_count = 12'd0;
    assign signal_present = envelope >= SIGNAL_THRESHOLD_CODES;

    // Normalize loop gain with the measured samples/cycle.  This priority
    // encoder is much smaller than a divider and keeps the response similar
    // across the complete 0.9...110 kHz input range.
    function [4:0] period_log2_ceil;
        input [15:0] period_samples;
        begin
            casex (period_samples)
                16'b1xxxxxxxxxxxxxxx: period_log2_ceil = 5'd16;
                16'b01xxxxxxxxxxxxxx: period_log2_ceil = 5'd15;
                16'b001xxxxxxxxxxxxx: period_log2_ceil = 5'd14;
                16'b0001xxxxxxxxxxxx: period_log2_ceil = 5'd13;
                16'b00001xxxxxxxxxxx: period_log2_ceil = 5'd12;
                16'b000001xxxxxxxxxx: period_log2_ceil = 5'd11;
                16'b0000001xxxxxxxxx: period_log2_ceil = 5'd10;
                16'b00000001xxxxxxxx: period_log2_ceil = 5'd9;
                16'b000000001xxxxxxx: period_log2_ceil = 5'd8;
                16'b0000000001xxxxxx: period_log2_ceil = 5'd7;
                16'b00000000001xxxxx: period_log2_ceil = 5'd6;
                16'b000000000001xxxx: period_log2_ceil = 5'd5;
                16'b0000000000001xxx: period_log2_ceil = 5'd4;
                16'b00000000000001xx: period_log2_ceil = 5'd3;
                16'b000000000000001x: period_log2_ceil = 5'd2;
                default:             period_log2_ceil = 5'd1;
            endcase
        end
    endfunction

    wire [4:0] period_shift = period_log2_ceil(samples_since_crossing);
    wire signed [31:0] crossing_phase_error = -$signed(carrier_phase);
    wire reference_crossing = reference_pending || reference_edge;
    wire [31:0] phase_step_change =
        (config_phase_step >= phase_step) ?
        (config_phase_step - phase_step) : (phase_step - config_phase_step);
    wire reacquire = config_valid &&
        (config_phase_step == 0 ||
         phase_step_change > (phase_step >> 4));
    wire signed [32:0] extended_phase_error =
        {crossing_phase_error[31], crossing_phase_error};
    wire signed [32:0] proportional_adjust =
        loop_error_shift >>> LOOP_KP_EXTRA_SHIFT;
    wire signed [32:0] integral_adjust =
        loop_error_shift >>> LOOP_KI_EXTRA_SHIFT;
    wire signed [32:0] frequency_trim_candidate =
        frequency_trim + integral_adjust;
    wire signed [32:0] frequency_trim_limit =
        $signed({1'b0, phase_step >> FREQUENCY_TRIM_LIMIT_SHIFT});
    wire signed [33:0] phase_increment_wide =
        $signed({2'b00, phase_step}) + frequency_trim + proportional_trim;
    wire signed [31:0] phase_lag_error =
        $signed(phase_lag - applied_phase_lag);
    wire signed [31:0] phase_lag_slew =
        $signed(phase_step >> 5);

    // Keep the physical pipeline advance separate from the board calibration.
    // The latter removes the measured analog/comparator lead without changing
    // the phase lag requested by the MSPM0.
    wire [63:0] phase_advance_product =
        phase_step * PIPELINE_ADVANCE_SAMPLES;
    wire [31:0] phase_advance = phase_advance_product[31:0];
    wire [63:0] calibration_delay_product =
        phase_step * PHASE_CALIBRATION_DELAY_SAMPLES;
    localparam [31:0] CALIBRATION_LAG_WORD =
        (64'd4294967296 * PHASE_CALIBRATION_LAG_CDEG + 64'd18000) /
        64'd36000;
    wire [31:0] output_phase =
        carrier_phase + phase_advance - applied_phase_lag -
        calibration_delay_product[31:0] - CALIBRATION_LAG_WORD;

    wire signed [15:0] sine_sample;
    reg [11:0] envelope_pipe = 12'd0;
    reg [11:0] amplitude_ramp = 12'd0;
    localparam [11:0] RAMP_INCREMENT_CODES = RAMP_INCREMENT[11:0];
    reg rom_valid = 1'b0;
    reg signed [28:0] scaled_sample = 29'sd0;
    reg scale_valid = 1'b0;
    wire signed [28:0] reconstructed_wide =
        ADC_ZERO_SIGNED + (scaled_sample >>> 15);

    sine_rom_1024 #(.ROM_FILE(ROM_FILE)) sine_rom_inst (
        .clk(clk), .address(output_phase[31:22]), .value(sine_sample)
    );

    always @(posedge clk) begin
        if (rst) begin
            phase_step <= 32'd0;
            phase_lag <= 32'd0;
            applied_phase_lag <= 32'd0;
            carrier_phase <= 32'd0;
            frequency_trim <= 33'sd0;
            proportional_trim <= 33'sd0;
            loop_error_shift <= 33'sd0;
            loop_shift_count <= 5'd0;
            loop_update_pending <= 1'b0;
            reference_pending <= 1'b0;
            samples_since_crossing <= 16'hffff;
            envelope <= 12'd0;
            decay_count <= 12'd0;
            locked <= 1'b0;
        end else begin
            if (reference_edge)
                reference_pending <= 1'b1;
            // Period normalization is performed serially because loop
            // updates occur only once per input cycle.  Even at 110 kHz
            // there are hundreds of 50 MHz clocks available, avoiding a
            // large 33-bit barrel shifter in the sample path.
            if (loop_update_pending) begin
                if (loop_shift_count != 0) begin
                    loop_error_shift <= loop_error_shift >>> 1;
                    loop_shift_count <= loop_shift_count - 1'b1;
                end else begin
                    proportional_trim <= proportional_adjust;
                    if (frequency_trim_candidate > frequency_trim_limit)
                        frequency_trim <= frequency_trim_limit;
                    else if (frequency_trim_candidate < -frequency_trim_limit)
                        frequency_trim <= -frequency_trim_limit;
                    else
                        frequency_trim <= frequency_trim_candidate;
                    loop_update_pending <= 1'b0;
                end
            end

            if (config_valid) begin
                phase_step <= config_phase_step;
                phase_lag <= config_phase_lag;
                if (reacquire) begin
                    locked <= 1'b0;
                    frequency_trim <= 33'sd0;
                    proportional_trim <= 33'sd0;
                    loop_update_pending <= 1'b0;
                end
            end

            if (sample_valid) begin
                // Slew a live phase command over about eight input periods.
                // Command changes therefore cannot jump the ROM address.
                if (!locked || phase_lag_slew == 0)
                    applied_phase_lag <= phase_lag;
                else if (phase_lag_error > phase_lag_slew)
                    applied_phase_lag <= applied_phase_lag + phase_lag_slew;
                else if (phase_lag_error < -phase_lag_slew)
                    applied_phase_lag <= applied_phase_lag - phase_lag_slew;
                else
                    applied_phase_lag <= phase_lag;

                // Fast attack and slow release preserve the input sine peak
                // without requiring an RMS multiplier or divider.
                if (absolute_sample > envelope) begin
                    envelope <= absolute_sample;
                    decay_count <= 12'd0;
                end else if (&decay_count) begin
                    decay_count <= 12'd0;
                    if (envelope != 0)
                        envelope <= envelope - 1'b1;
                end else begin
                    decay_count <= decay_count + 1'b1;
                end

                if (samples_since_crossing != 16'hffff) begin
                    samples_since_crossing <= samples_since_crossing + 1'b1;
                    if (samples_since_crossing > LOST_CROSSING_SAMPLES) begin
                        // Mark the timeout as handled.  Leaving the saturated
                        // age above the threshold would repeat the timeout
                        // on every sample and prevent low-frequency reacquire.
                        samples_since_crossing <= 16'hffff;
                        locked <= 1'b0;
                        frequency_trim <= 33'sd0;
                        proportional_trim <= 33'sd0;
                        loop_update_pending <= 1'b0;
                    end
                end

                if (reference_crossing) begin
                    reference_pending <= 1'b0;
                    samples_since_crossing <= 16'd0;
                    if (signal_present && phase_step != 0 && !reacquire) begin
                        if (!locked) begin
                            // This one-time acquisition alignment is hidden
                            // by the output amplitude ramp.  After lock, the
                            // accumulator itself is never corrected.
                            carrier_phase <= phase_step;
                            frequency_trim <= 33'sd0;
                            proportional_trim <= 33'sd0;
                            loop_update_pending <= 1'b0;
                            locked <= 1'b1;
                        end else begin
                            loop_error_shift <= extended_phase_error;
                            loop_shift_count <= period_shift;
                            loop_update_pending <= 1'b1;
                        end
                    end
                end

                // Exactly one normal accumulator update per sample.  Loop
                // corrections alter phase slope rather than phase value, so
                // the sine-ROM address remains continuous at every crossing.
                if (!(reference_crossing && signal_present &&
                      phase_step != 0 && !locked && !reacquire))
                    carrier_phase <= carrier_phase + phase_increment_wide[31:0];
            end
        end
    end

    // Synchronous ROM followed by one DSP-friendly amplitude multiply.
    always @(posedge clk) begin
        if (rst) begin
            envelope_pipe <= 12'd0;
            amplitude_ramp <= 12'd0;
            rom_valid <= 1'b0;
            scaled_sample <= 29'sd0;
            scale_valid <= 1'b0;
            output_valid <= 1'b0;
            output_sample <= ADC_ZERO_CODE[11:0];
        end else begin
            if (!enable || !locked) begin
                amplitude_ramp <= 12'd0;
            end else if (sample_valid && amplitude_ramp < envelope) begin
                if ((envelope - amplitude_ramp) <= RAMP_INCREMENT_CODES)
                    amplitude_ramp <= envelope;
                else
                    amplitude_ramp <= amplitude_ramp + RAMP_INCREMENT_CODES;
            end

            rom_valid <= sample_valid && enable && locked;
            if (sample_valid && enable && locked) begin
                envelope_pipe <= (amplitude_ramp < envelope) ?
                                 amplitude_ramp : envelope;
            end

            scale_valid <= rom_valid;
            if (rom_valid)
                scaled_sample <=
                    $signed(sine_sample) * $signed({1'b0, envelope_pipe});

            output_valid <= scale_valid;
            if (scale_valid) begin
                if (reconstructed_wide < 0)
                    output_sample <= 12'd0;
                else if (reconstructed_wide > 4095)
                    output_sample <= 12'd4095;
                else
                    output_sample <= reconstructed_wide[11:0];
            end
        end
    end

    initial begin
        if (ADC_ZERO_CODE < 0 || ADC_ZERO_CODE > 4095)
            $display("ERROR: invalid ADC zero code");
        if (LOST_CROSSING_SAMPLES > 65534)
            $display("ERROR: lost-crossing counter is too small");
    end
endmodule
