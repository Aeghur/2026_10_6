`timescale 1ns/1ps
module tb_zero_crossing_dds #(
    parameter integer SAMPLE_HZ = 1_000_000,
    parameter integer TONE_HZ = 10_000,
    parameter integer STEP_ERROR_PPM = 0
);
    localparam integer TEST_SAMPLES = 5 * SAMPLE_HZ / TONE_HZ;
    localparam [63:0] EXACT_PHASE_STEP =
        (64'd4294967296 * TONE_HZ) / SAMPLE_HZ;
    localparam [31:0] PHASE_STEP =
        (EXACT_PHASE_STEP * (1_000_000 + STEP_ERROR_PPM)) / 1_000_000;
    reg clk = 0;
    reg rst = 1;
    reg enable = 1;
    reg config_valid = 0;
    reg [31:0] config_phase_step = PHASE_STEP;
    reg [31:0] config_phase_lag = 0;
    reg sample_valid = 0;
    reg reference_edge = 0;
    reg [11:0] sample_data = 12'd2104;
    wire output_valid;
    wire [11:0] output_sample;
    wire locked;
    wire signal_present;
    wire output_valid_180;
    wire [11:0] output_sample_180;
    wire locked_180;
    wire signal_present_180;
    wire output_valid_x2;
    wire [11:0] output_sample_x2;
    wire locked_x2;
    wire signal_present_x2;
    integer sample_index = 0;
    integer valid_count = 0;
    integer minimum_output = 4095;
    integer maximum_output = 0;
    integer inversion_errors = 0;
    integer previous_output = 2104;
    integer maximum_step = 0;
    integer output_step = 0;
    integer crossing_samples = 0;
    integer x2_positive_crossings = 0;
    integer x2_minimum_output = 4095;
    integer x2_maximum_output = 0;
    reg x2_was_above = 1'b0;
    real angle;
    real sample_real;

    always #10 clk = ~clk;

    zero_crossing_dds #(
        .ADC_ZERO_CODE(2104),
        .SAMPLE_HZ(SAMPLE_HZ),
        .PIPELINE_ADVANCE_SAMPLES(10)
    ) dut (
        .clk(clk), .rst(rst), .enable(enable),
        .config_valid(config_valid),
        .config_phase_step(config_phase_step),
        .config_phase_lag(config_phase_lag),
        .frequency_x2(1'b0),
        .amplitude_scale_code(2'd0),
        .reference_edge(reference_edge),
        .sample_valid(sample_valid), .sample_data(sample_data),
        .output_valid(output_valid), .output_sample(output_sample),
        .locked(locked), .signal_present(signal_present)
    );

    zero_crossing_dds #(
        .ADC_ZERO_CODE(2104),
        .SAMPLE_HZ(SAMPLE_HZ),
        .PIPELINE_ADVANCE_SAMPLES(10)
    ) dut_180 (
        .clk(clk), .rst(rst), .enable(enable),
        .config_valid(config_valid),
        .config_phase_step(config_phase_step),
        .config_phase_lag(32'h80000000),
        .frequency_x2(1'b0),
        .amplitude_scale_code(2'd0),
        .reference_edge(reference_edge),
        .sample_valid(sample_valid), .sample_data(sample_data),
        .output_valid(output_valid_180),
        .output_sample(output_sample_180),
        .locked(locked_180), .signal_present(signal_present_180)
    );

    zero_crossing_dds #(
        .ADC_ZERO_CODE(2104),
        .SAMPLE_HZ(SAMPLE_HZ),
        .PIPELINE_ADVANCE_SAMPLES(10)
    ) dut_x2 (
        .clk(clk), .rst(rst), .enable(enable),
        .config_valid(config_valid),
        .config_phase_step(config_phase_step << 1),
        .config_phase_lag(config_phase_lag),
        .frequency_x2(1'b1),
        .amplitude_scale_code(2'd1),
        .reference_edge(reference_edge),
        .sample_valid(sample_valid), .sample_data(sample_data),
        .output_valid(output_valid_x2), .output_sample(output_sample_x2),
        .locked(locked_x2), .signal_present(signal_present_x2)
    );

    always @(posedge clk) begin
        sample_valid <= ~sample_valid;
        reference_edge <= 1'b0;
        if (!sample_valid) begin
            angle = 6.283185307179586 * TONE_HZ * sample_index / SAMPLE_HZ;
            sample_real = 2104.0 + 500.0 * $sin(angle);
            sample_data <= $rtoi(sample_real);
            if (sample_index % (SAMPLE_HZ / TONE_HZ) == 0)
                reference_edge <= 1'b1;
            sample_index <= sample_index + 1;
        end
        if (output_valid) begin
            valid_count <= valid_count + 1;
            output_step = (output_sample >= previous_output) ?
                          output_sample - previous_output :
                          previous_output - output_sample;
            if (dut.amplitude_ramp >= 400 && output_step > maximum_step)
                maximum_step <= output_step;
            previous_output <= output_sample;
            if (output_sample < minimum_output)
                minimum_output <= output_sample;
            if (output_sample > maximum_output)
                maximum_output <= output_sample;
            if (output_valid_180 && valid_count > 20 &&
                (output_sample + output_sample_180 < 4206 ||
                 output_sample + output_sample_180 > 4210))
                inversion_errors <= inversion_errors + 1;
        end
        if (reference_edge)
            crossing_samples <= crossing_samples + 1;
        if (output_valid_x2 && dut_x2.amplitude_ramp >= 400) begin
            if (!x2_was_above && output_sample_x2 >= 2104)
                x2_positive_crossings <= x2_positive_crossings + 1;
            x2_was_above <= output_sample_x2 >= 2104;
            if (output_sample_x2 < x2_minimum_output)
                x2_minimum_output <= output_sample_x2;
            if (output_sample_x2 > x2_maximum_output)
                x2_maximum_output <= output_sample_x2;
        end
    end

    initial begin
        if (dut.scale_envelope(12'd800, 2'd0) != 12'd800 ||
            dut.scale_envelope(12'd800, 2'd1) != 12'd200 ||
            dut.scale_envelope(12'd800, 2'd2) != 12'd400 ||
            dut.scale_envelope(12'd800, 2'd3) != 12'd600) begin
            $display("FAIL: amplitude scale encoding");
            $finish;
        end
        #200;
        rst = 0;
        @(posedge clk);
        config_valid = 1;
        @(posedge clk);
        config_valid = 0;
        repeat (TEST_SAMPLES * 2) @(posedge clk);
        if (!signal_present || !locked || !signal_present_180 ||
            !locked_180 || !signal_present_x2 || !locked_x2) begin
            $display("FAIL: DDS acquire signal=%0d lock=%0d envelope=%0d age=%0d samples=%0d step=%0d cross=%0d",
                     signal_present, locked, dut.envelope,
                     dut.samples_since_crossing,
                     sample_index, dut.phase_step,
                     crossing_samples);
            $finish;
        end
        if (valid_count < TEST_SAMPLES / 2 || minimum_output > 1700 ||
            maximum_output < 2500 || inversion_errors != 0 ||
            maximum_step > 64 ||
            x2_positive_crossings < 6 ||
            x2_minimum_output < 1900 || x2_minimum_output > 2050 ||
            x2_maximum_output < 2150 || x2_maximum_output > 2300 ||
            (STEP_ERROR_PPM != 0 && dut.frequency_trim == 0)) begin
            $display("FAIL: invalid DDS output count/range/step %0d %0d %0d %0d x2=%0d..%0d",
                     valid_count, minimum_output, maximum_output, maximum_step,
                     x2_minimum_output, x2_maximum_output);
            $finish;
        end
        $display("PASS: continuous-phase DDS lock, max code step=%0d",
                 maximum_step);
        $finish;
    end

endmodule
