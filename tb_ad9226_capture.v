`timescale 1ns/1ps
module tb_ad9226_capture;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg [11:0] adc_data = 12'd0;
    reg adc_otr = 1'b0;
    wire adc_clk;
    wire [11:0] sample_data;
    wire sample_otr;
    wire sample_valid;

    integer clk_cycles = 0;
    integer falling_edges = 0;
    integer valid_samples = 0;
    integer previous_valid_cycle = 0;
    reg [11:0] next_adc_data;

    always #10 clk = ~clk; // 50 MHz

    // Model the AD9226 output changing after each rising sampling edge.
    always @(posedge adc_clk) begin
        next_adc_data = adc_data + 12'h155;
        adc_data <= next_adc_data;
        adc_otr  <= next_adc_data[11];
    end

    always @(negedge adc_clk)
        falling_edges = falling_edges + 1;

    always @(posedge clk) begin
        clk_cycles = clk_cycles + 1;
        if (sample_valid) begin
            if (falling_edges <= 8) begin
                $display("FAIL: sample_valid asserted during pipeline discard");
                $finish;
            end
            if (sample_otr !== sample_data[11]) begin
                $display("FAIL: OTR is not aligned with sample_data");
                $finish;
            end
            if (valid_samples > 0 &&
                clk_cycles - previous_valid_cycle != 2) begin
                $display("FAIL: sample_valid period is %0d system clocks",
                         clk_cycles - previous_valid_cycle);
                $finish;
            end
            previous_valid_cycle = clk_cycles;
            valid_samples = valid_samples + 1;
        end
    end

    ad9226_capture #(
        .SAMPLE_HZ(25_000_000)
    ) dut (
        .clk(clk),
        .rst(rst),
        .adc_data(adc_data),
        .adc_otr(adc_otr),
        .adc_clk(adc_clk),
        .sample_data(sample_data),
        .sample_otr(sample_otr),
        .sample_valid(sample_valid)
    );

    initial begin
        #200;
        rst = 1'b0;
        #5000;
        if (valid_samples < 100) begin
            $display("FAIL: only %0d valid samples", valid_samples);
            $finish;
        end
        $display("PASS: AD9226 capture clocks=%0d, valid samples=%0d",
                 falling_edges, valid_samples);
        $finish;
    end
endmodule
