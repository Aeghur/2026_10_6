`timescale 1ns/1ps
module tb_dac904_output;
    reg rst = 1'b1;
    reg clk = 1'b0;
    reg sample_clk = 1'b0;
    reg sample_valid = 1'b0;
    reg [13:0] dac_code = 14'd0;
    wire dac_clk;
    wire [13:0] dac_data;

    integer last_data_change = 0;
    integer latch_count = 0;
    reg [13:0] latched_data = 14'h0000;

    always #10 clk = ~clk;
    always #20 sample_clk = ~sample_clk;
    always @(dac_data) last_data_change = $time;

    always @(posedge dac_clk) begin
        if (!rst && $time - last_data_change < 20) begin
            $display("FAIL: DAC setup interval is only %0d ns",
                     $time - last_data_change);
            $finish;
        end
        latched_data = dac_data;
        if (!rst) latch_count = latch_count + 1;
    end

    dac904_output dut (
        .rst(rst), .clk(clk), .sample_clk(sample_clk),
        .sample_valid(sample_valid), .dac_code(dac_code),
        .dac_clk(dac_clk), .dac_data(dac_data)
    );

    initial begin
        #10;
        if (dac_data !== 14'h2000) begin
            $display("FAIL: reset code is %04x", dac_data);
            $finish;
        end

        #35;
        rst = 1'b0;
        dac_code = 14'h048d;
        sample_valid = 1'b1;
        @(negedge clk);
        #1;
        if (dac_data !== 14'h048d) begin
            $display("FAIL: timing interface changed code to %04x", dac_data);
            $finish;
        end

        @(posedge dac_clk);
        #1;
        if (latched_data !== 14'h048d) begin
            $display("FAIL: DAC latched %04x", latched_data);
            $finish;
        end

        dac_code = 14'h3fff;
        @(negedge clk);
        #1;
        if (dac_data !== 14'h3fff) begin
            $display("FAIL: full-scale code is %04x", dac_data);
            $finish;
        end
        sample_valid = 1'b0;
        dac_code = 14'h0000;
        @(negedge clk);
        #1;
        if (dac_data !== 14'h3fff) begin
            $display("FAIL: data changed while sample_valid was low");
            $finish;
        end

        if (latch_count < 2) begin
            $display("FAIL: DAC latch count=%0d", latch_count);
            $finish;
        end
        $display("PASS: DAC904 14-bit timing interface and setup verified");
        $finish;
    end
endmodule
