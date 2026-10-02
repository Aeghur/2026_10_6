`timescale 1ns/1ps
module tb_top_smoke;
    reg clk = 1'b0;
    reg [11:0] adc_data = 12'd2104;
    reg adc_otr = 1'b0;
    reg uart_rx = 1'b1;
    wire adc_clk;
    wire dac_clk;
    wire [13:0] dac_data;
    wire uart_tx;
    integer errors = 0;
    integer adc_edges = 0;

    always #10 clk = ~clk;
    always @(posedge adc_clk)
        adc_edges = adc_edges + 1;

    dpll_dds_top #(.ROM_FILE("../rtl/sine_1024x16.hex")) dut (
        .clk_50m(clk), .adc_data(adc_data), .adc_otr(adc_otr),
        .adc_clk(adc_clk), .dac_clk(dac_clk), .dac_data(dac_data),
        .uart_tx(uart_tx), .uart_rx(uart_rx)
    );

    initial begin
        repeat (2000) @(posedge clk);
        if (adc_edges < 800) begin
            $display("FAIL: ADC clock did not run");
            errors = errors + 1;
        end
        if (dac_clk !== ~adc_clk) begin
            $display("FAIL: DAC clock polarity");
            errors = errors + 1;
        end
        if (dac_data !== 14'd8279) begin
            $display("FAIL: unlocked DAC code=%0d", dac_data);
            errors = errors + 1;
        end
        if (uart_tx !== 1'b1) begin
            $display("FAIL: UART TX is not idle");
            errors = errors + 1;
        end
        if (errors == 0)
            $display("PASS: board top reset, clocks and unlocked zero output");
        else
            $display("FAIL: top smoke errors=%0d", errors);
        $finish;
    end
endmodule

