`timescale 1ns/1ps
module tb_dpll_high_band;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg sample_valid = 1'b0;
    reg [11:0] adc_sample = 12'd2104;
    reg adc_otr = 1'b0;
    wire output_valid;
    wire [13:0] dac_code;
    wire locked;
    wire signal_present;
    wire [31:0] phase_increment;
    wire signed [23:0] phase_error;
    wire signed [23:0] signal_magnitude;

    integer input_period;
    integer sample_index;
    integer sample_value;
    integer expected_word;
    integer difference;
    integer errors = 0;
    integer positive_correlation;
    integer negative_correlation;
    real radians;

    always #10 clk = ~clk;

    dpll_dds_core #(.ROM_FILE("../rtl/sine_1024x16.hex")) dut (
        .clk(clk), .rst(rst), .sample_valid(sample_valid),
        .adc_sample(adc_sample), .adc_otr(adc_otr),
        .phase_lag_word(32'd0), .calibration_phase_word(32'd0),
        .amplitude_code(14'd2143), .output_valid(output_valid),
        .dac_code(dac_code), .dac_clipped(), .locked(locked),
        .signal_present(signal_present), .phase_increment(phase_increment),
        .phase_error(phase_error), .signal_magnitude(signal_magnitude),
        .coarse_frequency_valid()
    );

    task drive_samples;
        input integer count;
        input integer measure_correlation;
        integer remaining;
        begin
            for (remaining = 0; remaining < count; remaining = remaining + 1) begin
                radians = 6.283185307179586 * sample_index / input_period;
                sample_value = 2104 + $rtoi(900.0 * $sin(radians));
                @(negedge clk);
                adc_sample = sample_value[11:0];
                sample_valid = 1'b1;
                @(negedge clk);
                sample_valid = 1'b0;
                if (measure_correlation != 0 && output_valid) begin
                    if ((sample_value - 2104) * ($signed({1'b0, dac_code}) - 8279) >= 0)
                        positive_correlation = positive_correlation + 1;
                    else
                        negative_correlation = negative_correlation + 1;
                end
                sample_index = sample_index + 1;
                if (sample_index == input_period)
                    sample_index = 0;
            end
        end
    endtask

    task check_frequency;
        input integer period;
        begin
            input_period = period;
            sample_index = 0;
            positive_correlation = 0;
            negative_correlation = 0;
            // More than two time constants of the synthesis LPF, followed by
            // twenty complete cycles used only for the polarity check.
            drive_samples(150000, 0);
            drive_samples(period * 20, 1);
            expected_word = 4294967296.0 / period;
            difference = phase_increment > expected_word ?
                phase_increment - expected_word : expected_word - phase_increment;
            if (!locked || !signal_present || difference > 20000 ||
                positive_correlation <= negative_correlation) begin
                $display("FAIL period=%0d lock=%b signal=%b word=%0d expected=%0d error=%0d mag=%0d corr=%0d/%0d",
                    period, locked, signal_present, phase_increment, expected_word,
                    phase_error, signal_magnitude, positive_correlation,
                    negative_correlation);
                errors = errors + 1;
            end else begin
                $display("PASS period=%0d word=%0d error=%0d mag=%0d corr=%0d/%0d",
                    period, phase_increment, phase_error, signal_magnitude,
                    positive_correlation, negative_correlation);
            end
        end
    endtask

    initial begin
        repeat (5) @(posedge clk);
        rst = 1'b0;
        check_frequency(625); // 40 kHz
        check_frequency(500); // 50 kHz
        check_frequency(417); // approximately 60 kHz
        check_frequency(313); // approximately 80 kHz
        check_frequency(250); // 100 kHz
        if (errors == 0)
            $display("PASS: real-parameter 40..100 kHz DPLL stability and polarity");
        else
            $display("FAIL: high-band errors=%0d", errors);
        $finish;
    end
endmodule
