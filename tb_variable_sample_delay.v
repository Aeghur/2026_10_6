`timescale 1ns/1ps
module tb_variable_sample_delay;
    reg clk = 0;
    reg rst = 1;
    reg sample_valid = 0;
    reg [11:0] sample_data = 0;
    reg config_valid = 0;
    reg [3:0] delay_samples = 0;
    wire delayed_valid;
    wire [11:0] delayed_data;
    integer index;
    integer errors = 0;

    always #10 clk = ~clk;

    variable_sample_delay #(.DEPTH(16), .ADDR_WIDTH(4)) dut (
        .clk(clk), .rst(rst),
        .sample_valid(sample_valid), .sample_data(sample_data),
        .config_valid(config_valid), .delay_samples(delay_samples),
        .delayed_valid(delayed_valid), .delayed_data(delayed_data)
    );

    task push_sample;
        input [11:0] value;
        input [11:0] expected;
        begin
            @(negedge clk);
            sample_valid = 1;
            sample_data = value;
            @(posedge clk);
            @(negedge clk);
            sample_valid = 0;
            @(posedge clk);
            #1;
            if (!delayed_valid || delayed_data !== expected) begin
                $display("FAIL: input=%0d expected=%0d got=%0d valid=%0d",
                         value, expected, delayed_data, delayed_valid);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst = 0;
        for (index = 0; index < 8; index = index + 1)
            push_sample(index, index);

        @(negedge clk);
        delay_samples = 4;
        config_valid = 1;
        @(posedge clk);
        @(negedge clk);
        config_valid = 0;
        for (index = 8; index < 16; index = index + 1)
            push_sample(index, index - 4);

        if (errors == 0)
            $display("PASS: variable integer sample delay");
        else
            $display("FAIL: %0d errors", errors);
        $finish;
    end
endmodule
