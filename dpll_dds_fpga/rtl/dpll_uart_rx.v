module dpll_uart_rx #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer BAUD = 2_000_000
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,
    output reg [7:0]  data = 8'd0,
    output reg        valid = 1'b0,
    output reg        framing_error = 1'b0
);
    localparam integer CLKS_PER_BIT = (CLK_HZ + BAUD / 2) / BAUD;
    localparam integer HALF_BIT = CLKS_PER_BIT / 2;
    reg rx_meta = 1'b1;
    reg rx_sync = 1'b1;
    reg rx_previous = 1'b1;
    reg busy = 1'b0;
    reg [15:0] clock_count = 16'd0;
    reg [3:0] bit_index = 4'd0;
    reg [7:0] shift = 8'd0;

    always @(posedge clk) begin
        rx_meta <= rx;
        rx_sync <= rx_meta;
        rx_previous <= rx_sync;
        valid <= 1'b0;
        framing_error <= 1'b0;
        if (rst) begin
            rx_meta <= 1'b1;
            rx_sync <= 1'b1;
            rx_previous <= 1'b1;
            busy <= 1'b0;
            clock_count <= 16'd0;
            bit_index <= 4'd0;
        end else if (!busy) begin
            if (rx_previous && !rx_sync) begin
                busy <= 1'b1;
                clock_count <= HALF_BIT - 1;
                bit_index <= 4'd0;
            end
        end else if (clock_count != 0) begin
            clock_count <= clock_count - 1'b1;
        end else if (bit_index == 0) begin
            if (rx_sync)
                busy <= 1'b0;
            else begin
                bit_index <= 4'd1;
                clock_count <= CLKS_PER_BIT - 1;
            end
        end else if (bit_index <= 8) begin
            shift[bit_index-1'b1] <= rx_sync;
            bit_index <= bit_index + 1'b1;
            clock_count <= CLKS_PER_BIT - 1;
        end else begin
            busy <= 1'b0;
            if (rx_sync) begin
                data <= shift;
                valid <= 1'b1;
            end else begin
                framing_error <= 1'b1;
            end
        end
    end
endmodule

