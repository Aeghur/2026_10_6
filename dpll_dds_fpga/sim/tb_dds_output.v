`timescale 1ns/1ps
module tb_dds_output;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg sample_valid = 1'b0;
    reg [31:0] phase_word = 32'd0;
    reg [13:0] amplitude = 14'd4096;
    wire output_valid;
    wire [13:0] dac_code;
    wire clipped;
    integer errors = 0;

    always #10 clk = ~clk;

    dds_output #(
        .DAC_ZERO_CODE(8279),
        .ROM_FILE("../rtl/sine_1024x16.hex")
    ) dut (
        .clk(clk), .rst(rst), .sample_valid(sample_valid),
        .phase_word(phase_word), .amplitude_code(amplitude),
        .output_valid(output_valid), .dac_code(dac_code), .clipped(clipped)
    );

    task check_phase;
        input [31:0] requested_phase;
        input integer expected_code;
        integer difference;
        begin
            @(negedge clk);
            phase_word = requested_phase;
            sample_valid = 1'b1;
            @(negedge clk);
            sample_valid = 1'b0;
            @(posedge output_valid);
            #1;
            difference = dac_code > expected_code ?
                dac_code - expected_code : expected_code - dac_code;
            if (difference > 2 || clipped) begin
                $display("FAIL phase=%h code=%0d expected=%0d clip=%b",
                    requested_phase, dac_code, expected_code, clipped);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst = 1'b0;
        check_phase(32'h00000000, 8279);
        check_phase(32'h20000000, 11175); // +45 degrees
        check_phase(32'h40000000, 12375); // +90 degrees
        check_phase(32'h80000000, 8279);  // 180 degrees
        check_phase(32'hc0000000, 4183);  // 270 degrees
        if (errors == 0)
            $display("PASS: DDS 0/45/90/180/270-degree mapping");
        else
            $display("FAIL: DDS errors=%0d", errors);
        $finish;
    end
endmodule

