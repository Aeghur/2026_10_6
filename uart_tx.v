module uart_tx #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer BAUD   = 2_000_000
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       start,
    input  wire [7:0] data,
    output reg        tx = 1'b1,
    output reg        busy = 1'b0,
    output reg        done = 1'b0
);
    // Round to the nearest whole system-clock count.  At 50 MHz and
    // The rounded divider supports both the 2 Mbaud production link and
    // slower parameterized simulation/debug configurations.
    localparam integer CLKS_PER_BIT = (CLK_HZ + BAUD / 2) / BAUD;
    reg [15:0] baud_count = 16'd0;
    reg [3:0]  bit_index = 4'd0;
    reg [9:0]  shift = 10'h3ff;

    always @(posedge clk) begin
        done <= 1'b0;
        if (rst) begin
            tx         <= 1'b1;
            busy       <= 1'b0;
            baud_count <= 16'd0;
            bit_index  <= 4'd0;
        end else if (!busy) begin
            tx <= 1'b1;
            if (start) begin
                shift       <= {1'b1, data, 1'b0};
                tx          <= 1'b0;
                busy        <= 1'b1;
                baud_count  <= 16'd0;
                bit_index   <= 4'd0;
            end
        end else if (baud_count == CLKS_PER_BIT - 1) begin
            baud_count <= 16'd0;
            if (bit_index == 4'd9) begin
                tx    <= 1'b1;
                busy  <= 1'b0;
                done  <= 1'b1;
            end else begin
                bit_index <= bit_index + 1'b1;
                shift     <= {1'b1, shift[9:1]};
                tx        <= shift[1];
            end
        end else begin
            baud_count <= baud_count + 1'b1;
        end
    end

    initial begin
        if (CLKS_PER_BIT < 2) $display("ERROR: UART baud rate is too high");
        if (CLK_HZ != CLKS_PER_BIT * BAUD)
            $display("INFO: UART TX uses rounded clocks-per-bit");
    end
endmodule
