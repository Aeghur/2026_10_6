`timescale 1ns/1ps
module tb_dpll_dds_core;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg sample_valid = 1'b0;
    reg [11:0] adc_sample = 12'd2104;
    reg adc_otr = 1'b0;
    reg [31:0] phase_lag_word = 32'd0;
    reg [31:0] calibration_phase_word = 32'd0;
    reg [13:0] amplitude_code = 14'd2048;
    wire output_valid;
    wire [13:0] dac_code;
    wire dac_clipped;
    wire locked;
    wire signal_present;
    wire [31:0] phase_increment;
    wire signed [23:0] phase_error;
    wire signed [23:0] signal_magnitude;
    wire coarse_frequency_valid;

    integer input_period = 2500;
    integer sample_index = 0;
    integer sample_value;
    integer errors = 0;
    reg [31:0] phase_before_command;
    real radians;

    always #10 clk = ~clk;

    dpll_dds_core #(
        .LPF_SHIFT(10), .LOOP_DECIMATION_LOG2(6),
        .ROM_FILE("../rtl/sine_1024x16.hex")
    ) dut (
        .clk(clk), .rst(rst), .sample_valid(sample_valid),
        .adc_sample(adc_sample), .adc_otr(adc_otr),
        .phase_lag_word(phase_lag_word),
        .calibration_phase_word(calibration_phase_word),
        .amplitude_code(amplitude_code), .output_valid(output_valid),
        .dac_code(dac_code), .dac_clipped(dac_clipped),
        .locked(locked), .signal_present(signal_present),
        .phase_increment(phase_increment), .phase_error(phase_error),
        .signal_magnitude(signal_magnitude),
        .coarse_frequency_valid(coarse_frequency_valid)
    );

    task run_samples;
        input integer count;
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
                sample_index = sample_index + 1;
                if (sample_index == input_period)
                    sample_index = 0;
            end
        end
    endtask

    task check_frequency;
        input integer expected;
        integer difference;
        begin
            difference = phase_increment > expected ?
                phase_increment - expected : expected - phase_increment;
            if (!locked || !signal_present || difference > 10000) begin
                $display("FAIL period=%0d lock=%b signal=%b word=%0d expected=%0d err=%0d mag=%0d",
                    input_period, locked, signal_present, phase_increment,
                    expected, phase_error, signal_magnitude);
                errors = errors + 1;
            end else begin
                $display("PASS period=%0d word=%0d phase_error=%0d",
                    input_period, phase_increment, phase_error);
            end
        end
    endtask

    initial begin
        repeat (5) @(posedge clk);
        rst = 1'b0;

        run_samples(50000); // accelerated-loop simulation parameters
        check_frequency(1717987);

        input_period = 1250; // step to 20 kHz
        sample_index = 0;
        run_samples(50000);
        check_frequency(3435974);

        phase_before_command = dut.output_phase;
        phase_lag_word = 32'h40000000; // command a 90-degree lag
        #1;
        if (dut.output_phase !== phase_before_command - 32'h40000000) begin
            $display("FAIL: positive lag command did not subtract phase");
            errors = errors + 1;
        end
        run_samples(5000);
        if (dac_clipped) begin
            $display("FAIL: unexpected DAC clipping");
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS: DPLL locks, reacquires and accepts phase command");
        else
            $display("FAIL: DPLL errors=%0d", errors);
        $finish;
    end

    initial begin
        #8000000;
        $display("FAIL: DPLL test timeout");
        $finish;
    end
endmodule
