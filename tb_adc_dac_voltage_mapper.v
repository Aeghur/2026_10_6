`timescale 1ns/1ps
module tb_adc_dac_voltage_mapper;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg valid = 1'b0;
    reg [11:0] adc = 12'd0;
    wire normal_valid;
    wire [13:0] normal_code;
    wire normal_clip;
    wire invert_valid;
    wire [13:0] invert_code;
    wire invert_clip;
    wire round_valid;
    wire [13:0] round_code;
    wire round_clip;
    wire sat_valid;
    wire [13:0] sat_code;
    wire sat_clip;
    integer errors = 0;
    integer burst_seen = 0;
    reg monitor_burst = 1'b0;

    always #10 clk = ~clk;

    always @(posedge normal_valid) begin
        if (monitor_burst) begin
            #1;
            case (burst_seen)
                0: if (normal_code !== 14'd8192) errors = errors + 1;
                1: if (normal_code !== 14'd8196) errors = errors + 1;
                2: if (normal_code !== 14'd8188) errors = errors + 1;
                default: errors = errors + 1;
            endcase
            burst_seen = burst_seen + 1;
        end
    end

    adc_dac_voltage_mapper #(
        .ADC_ZERO_CODE(2103), .DAC_ZERO_CODE(8192),
        .GAIN_Q16(262144), .INVERT_OUTPUT(0)
    ) normal_dut (
        .clk(clk), .rst(rst), .sample_valid(valid), .sample_data(adc),
        .mapped_valid(normal_valid), .dac_code(normal_code), .clipped(normal_clip)
    );
    adc_dac_voltage_mapper #(
        .ADC_ZERO_CODE(2103), .DAC_ZERO_CODE(8192),
        .GAIN_Q16(262144), .INVERT_OUTPUT(1)
    ) invert_dut (
        .clk(clk), .rst(rst), .sample_valid(valid), .sample_data(adc),
        .mapped_valid(invert_valid), .dac_code(invert_code), .clipped(invert_clip)
    );
    adc_dac_voltage_mapper #(
        .ADC_ZERO_CODE(2048), .DAC_ZERO_CODE(8192),
        .GAIN_Q16(98304), .INVERT_OUTPUT(0)
    ) round_dut (
        .clk(clk), .rst(rst), .sample_valid(valid), .sample_data(adc),
        .mapped_valid(round_valid), .dac_code(round_code), .clipped(round_clip)
    );
    adc_dac_voltage_mapper #(
        .ADC_ZERO_CODE(2048), .DAC_ZERO_CODE(8192),
        .GAIN_Q16(327680), .INVERT_OUTPUT(0)
    ) sat_dut (
        .clk(clk), .rst(rst), .sample_valid(valid), .sample_data(adc),
        .mapped_valid(sat_valid), .dac_code(sat_code), .clipped(sat_clip)
    );

    task drive_and_check;
        input [11:0] input_code;
        input [13:0] expected_normal;
        input [13:0] expected_invert;
        input [13:0] expected_round;
        input expected_normal_clip;
        begin
            @(negedge clk);
            adc = input_code;
            valid = 1'b1;
            @(negedge clk);
            valid = 1'b0;
            @(posedge normal_valid);
            #1;
            if (normal_code !== expected_normal ||
                invert_code !== expected_invert ||
                round_code !== expected_round ||
                normal_clip !== expected_normal_clip) begin
                $display("FAIL adc=%0d normal=%0d/%0d inv=%0d/%0d round=%0d/%0d clip=%b/%b",
                    input_code, normal_code, expected_normal,
                    invert_code, expected_invert, round_code, expected_round,
                    normal_clip, expected_normal_clip);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        repeat (2) @(posedge clk);
        rst = 1'b0;
        drive_and_check(2103, 8192, 8192, 8275, 1'b0);
        drive_and_check(2104, 8196, 8188, 8276, 1'b0);
        drive_and_check(2102, 8188, 8196, 8273, 1'b0);
        drive_and_check(0, 0, 16383, 5120, 1'b1);
        drive_and_check(4095, 16160, 224, 11263, 1'b0);
        if (sat_code !== 14'd16383 || !sat_clip) begin
            $display("FAIL: upper saturation code=%0d clip=%b", sat_code, sat_clip);
            errors = errors + 1;
        end
        drive_and_check(0, 0, 16383, 5120, 1'b1);
        if (sat_code !== 14'd0 || !sat_clip) begin
            $display("FAIL: lower saturation code=%0d clip=%b", sat_code, sat_clip);
            errors = errors + 1;
        end

        // Three inputs at the real 25 MSPS cadence (one every 40 ns).
        monitor_burst = 1'b1;
        @(negedge clk); adc = 12'd2103; valid = 1'b1;
        @(negedge clk); valid = 1'b0;
        @(negedge clk); adc = 12'd2104; valid = 1'b1;
        @(negedge clk); valid = 1'b0;
        @(negedge clk); adc = 12'd2102; valid = 1'b1;
        @(negedge clk); valid = 1'b0;
        wait (burst_seen == 3);
        monitor_burst = 1'b0;
        if (normal_valid !== invert_valid || normal_valid !== round_valid ||
            normal_valid !== sat_valid) begin
            $display("FAIL: mapped_valid outputs are not aligned");
            errors = errors + 1;
        end
        if (errors == 0)
            $display("PASS: mapping, inversion, rounding, saturation and 25 MSPS throughput");
        else
            $display("FAIL: mapper errors=%0d", errors);
        $finish;
    end

    initial begin
        #5000;
        $display("FAIL: mapper timeout");
        $finish;
    end
endmodule
