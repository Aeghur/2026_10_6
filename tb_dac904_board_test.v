`timescale 1ns/1ps
module tb_dac904_board_test;
    reg clk_50m = 1'b0;
    wire dac_clk;
    wire [13:0] dac_data;
    integer errors = 0;

    always #10 clk_50m = ~clk_50m;

    dac904_board_test #(
        .CLK_HZ(6),
        .HOLD_SAMPLES(3)
    ) dut (
        .clk_50m(clk_50m),
        .dac_clk(dac_clk),
        .dac_data(dac_data)
    );

    task expect_next_code;
        input [13:0] expected;
        begin
            @(dac_data);
            #1;
            if (dac_data !== expected) begin
                $display("FAIL: expected DAC code %0d, got %0d",
                         expected, dac_data);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        if (dac_data !== 14'd0) begin
            $display("FAIL: initial DAC code is not zero");
            errors = errors + 1;
        end
        expect_next_code(14'd8192);
        expect_next_code(14'd16383);
        expect_next_code(14'd8192);
        expect_next_code(14'd0);

        if (errors == 0)
            $display("PASS: DAC904 board-test code sequence");
        else
            $display("FAIL: %0d errors", errors);
        $finish;
    end
endmodule
