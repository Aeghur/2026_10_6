// MINI_FPGA DIG1..DIG6: left to right, common cathode, active-high segments
// and active-low digit selects. Input is an integer frequency in hertz.
module six_digit_frequency_display #(
    parameter integer CLK_HZ = 50_000_000
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        frequency_valid,
    input  wire [16:0] frequency_hz,
    output reg  [7:0]  digitron_out,
    output reg  [5:0]  digitron_cs_n
);
    localparam integer SCAN_CYCLES = CLK_HZ / 6000;
    reg [31:0] scan_count = 32'd0;
    reg [2:0] scan_digit = 3'd0;
    reg [16:0] binary_work = 17'd0;
    reg [23:0] bcd_work = 24'd0;
    reg [23:0] bcd_display = 24'd0;
    reg [4:0] bits_left = 5'd0;
    reg [23:0] adjusted;
    reg [3:0] digit;
    integer i;

    always @(posedge clk) begin
        if (rst) begin
            binary_work <= 17'd0;
            bcd_work <= 24'd0;
            bcd_display <= 24'd0;
            bits_left <= 5'd0;
            scan_count <= 32'd0;
            scan_digit <= 3'd0;
        end else begin
            // Serial double-dabble uses 17 clocks after each measurement.
            if (frequency_valid) begin
                binary_work <= frequency_hz;
                bcd_work <= 24'd0;
                bits_left <= 5'd17;
            end else if (bits_left != 0) begin
                adjusted = bcd_work;
                for (i = 0; i < 6; i = i + 1)
                    if (adjusted[i*4 +: 4] >= 5)
                        adjusted[i*4 +: 4] = adjusted[i*4 +: 4] + 4'd3;
                bcd_work <= {adjusted[22:0], binary_work[16]};
                binary_work <= {binary_work[15:0], 1'b0};
                bits_left <= bits_left - 1'b1;
                if (bits_left == 1)
                    bcd_display <= {adjusted[22:0], binary_work[16]};
            end

            if (scan_count == SCAN_CYCLES - 1) begin
                scan_count <= 32'd0;
                scan_digit <= (scan_digit == 5) ? 3'd0 : scan_digit + 1'b1;
            end else begin
                scan_count <= scan_count + 1'b1;
            end
        end
    end

    always @* begin
        case (scan_digit)
            0: digit = bcd_display[23:20];
            1: digit = bcd_display[19:16];
            2: digit = bcd_display[15:12];
            3: digit = bcd_display[11:8];
            4: digit = bcd_display[7:4];
            default: digit = bcd_display[3:0];
        endcase
        digitron_cs_n = ~(6'b000001 << scan_digit);
        // [0:6] = A,B,C,D,E,F,G; [7] = decimal point, always off.
        case (digit)
            0: digitron_out = 8'h3f;
            1: digitron_out = 8'h06;
            2: digitron_out = 8'h5b;
            3: digitron_out = 8'h4f;
            4: digitron_out = 8'h66;
            5: digitron_out = 8'h6d;
            6: digitron_out = 8'h7d;
            7: digitron_out = 8'h07;
            8: digitron_out = 8'h7f;
            9: digitron_out = 8'h6f;
            default: digitron_out = 8'h00;
        endcase
    end

    initial begin
        if (SCAN_CYCLES < 1)
            $display("ERROR: display clock is too slow");
    end
endmodule
