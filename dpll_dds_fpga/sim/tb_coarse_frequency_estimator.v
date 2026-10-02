`timescale 1ns/1ps
module tb_coarse_frequency_estimator;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg sample_valid = 1'b0;
    reg signed [12:0] sample_centered = 13'sd0;
    wire crossing;
    wire frequency_valid;
    wire [31:0] phase_increment;
    integer sample_index;
    integer period_samples;
    integer errors = 0;

    always #10 clk = ~clk;

    coarse_frequency_estimator dut (
        .clk(clk), .rst(rst), .sample_valid(sample_valid),
        .sample_centered(sample_centered), .crossing(crossing),
        .frequency_valid(frequency_valid), .phase_increment(phase_increment)
    );

    task measure_period;
        input integer requested_period;
        input [31:0] expected_word;
        integer timeout;
        integer difference;
        begin
            period_samples = requested_period;
            sample_index = 0;
            rst = 1'b1;
            repeat (4) @(posedge clk);
            rst = 1'b0;
            timeout = requested_period * 12;
            while (!frequency_valid && timeout > 0) begin
                @(negedge clk);
                sample_valid = 1'b1;
                sample_centered =
                    (sample_index < requested_period / 2) ? 13'sd1000 : -13'sd1000;
                @(negedge clk);
                sample_valid = 1'b0;
                sample_index = sample_index + 1;
                if (sample_index == requested_period)
                    sample_index = 0;
                timeout = timeout - 1;
            end
            if (!frequency_valid) begin
                $display("FAIL: no result for period %0d", requested_period);
                errors = errors + 1;
            end else begin
                difference = phase_increment > expected_word ?
                    phase_increment - expected_word : expected_word - phase_increment;
                if (difference > 2) begin
                    $display("FAIL period=%0d word=%0d expected=%0d",
                        requested_period, phase_increment, expected_word);
                    errors = errors + 1;
                end else begin
                    $display("PASS period=%0d phase_increment=%0d",
                        requested_period, phase_increment);
                end
            end
        end
    endtask

    initial begin
        measure_period(250, 32'd17179869);   // 100 kHz at 25 MSPS
        measure_period(2500, 32'd1717987);   // 10 kHz
        measure_period(25000, 32'd171799);   // 1 kHz
        if (errors == 0)
            $display("PASS: 1..100 kHz coarse acquisition");
        else
            $display("FAIL: coarse estimator errors=%0d", errors);
        $finish;
    end
endmodule

