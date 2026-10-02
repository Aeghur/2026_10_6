module dpll_uart_tx #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer BAUD = 2_000_000
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output reg        tx = 1'b1,
    output reg        busy = 1'b0,
    output reg        done = 1'b0
);
    localparam integer CLKS_PER_BIT = (CLK_HZ + BAUD / 2) / BAUD;
    reg [15:0] clock_count = 16'd0;
    reg [3:0] bit_index = 4'd0;
    reg [9:0] frame = 10'h3ff;

    always @(posedge clk) begin
        done <= 1'b0;
        if (rst) begin
            tx <= 1'b1;
            busy <= 1'b0;
            clock_count <= 16'd0;
            bit_index <= 4'd0;
            frame <= 10'h3ff;
        end else if (!busy) begin
            tx <= 1'b1;
            if (start) begin
                frame <= {1'b1, data, 1'b0};
                tx <= 1'b0;
                busy <= 1'b1;
                bit_index <= 4'd0;
                clock_count <= CLKS_PER_BIT - 1;
            end
        end else if (clock_count != 0) begin
            clock_count <= clock_count - 1'b1;
        end else if (bit_index == 9) begin
            tx <= 1'b1;
            busy <= 1'b0;
            done <= 1'b1;
        end else begin
            bit_index <= bit_index + 1'b1;
            tx <= frame[bit_index + 1'b1];
            clock_count <= CLKS_PER_BIT - 1;
        end
    end
endmodule

