`timescale 1ns/1ps
module tb_comparator_frequency_meter;
    localparam integer CLK_HZ = 1_000_000;
    localparam integer SAMPLE_HZ = 250_000;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg comparator_in = 1'b0;
    reg tone_enable = 1'b1;
    wire rising_edge;
    wire frequency_valid;
    wire [16:0] frequency_hz;
    wire [31:0] phase_step;
    wire [7:0] digitron_out;
    wire [5:0] digitron_cs_n;
    reg [63:0] expected_step;

    always #500 clk = ~clk;
    always #100000 comparator_in = tone_enable && !comparator_in;

    comparator_frequency_meter #(
        .CLK_HZ(CLK_HZ), .SAMPLE_HZ(SAMPLE_HZ),
        .MIN_HZ(1_000), .MAX_HZ(10_000)
    ) meter (
        .clk(clk), .rst(rst), .comparator_in(comparator_in),
        .rising_edge(rising_edge), .frequency_valid(frequency_valid),
        .frequency_hz(frequency_hz), .phase_step(phase_step)
    );
    six_digit_frequency_display #(.CLK_HZ(CLK_HZ)) display (
        .clk(clk), .rst(rst), .frequency_valid(frequency_valid),
        .frequency_hz(frequency_hz), .digitron_out(digitron_out),
        .digitron_cs_n(digitron_cs_n)
    );

    initial begin
        #2000 rst = 1'b0;
        repeat (3) @(posedge frequency_valid);
        #20000;
        expected_step =
            (64'd4294967296 * frequency_hz + SAMPLE_HZ / 2) / SAMPLE_HZ;
        if (frequency_hz !== 17'd5000 ||
            phase_step < expected_step - 1 ||
            phase_step > expected_step + 1 ||
            display.bcd_display !== 24'h005000 ||
            digitron_cs_n === 6'b111111) begin
            $display("FAIL: 5 kHz measurement/display: hz=%0d step=%0d bcd=%h",
                     frequency_hz, phase_step, display.bcd_display);
            $finish;
        end

        tone_enable = 1'b0;
        repeat (2) @(posedge frequency_valid);
        #20000;
        if (frequency_hz !== 17'd0 || phase_step !== 32'd0 ||
            display.bcd_display !== 24'h000000) begin
            $display("FAIL: missing comparator signal was not cleared");
            $finish;
        end
        $display("PASS: T14 frequency, DDS phase step, six digits, signal loss");
        $finish;
    end
endmodule
