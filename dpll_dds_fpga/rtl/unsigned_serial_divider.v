// Iterative unsigned restoring divider. One result is produced after
// NUMERATOR_WIDTH clocks, avoiding a large combinational divider in the FPGA.
module unsigned_serial_divider #(
    parameter integer NUMERATOR_WIDTH = 36,
    parameter integer DENOMINATOR_WIDTH = 24
) (
    input  wire                           clk,
    input  wire                           rst,
    input  wire                           start,
    input  wire [NUMERATOR_WIDTH-1:0]     numerator,
    input  wire [DENOMINATOR_WIDTH-1:0]   denominator,
    output reg                            busy = 1'b0,
    output reg                            done = 1'b0,
    output reg [NUMERATOR_WIDTH-1:0]      quotient = {NUMERATOR_WIDTH{1'b0}}
);
    localparam integer COUNT_WIDTH = $clog2(NUMERATOR_WIDTH + 1);

    reg [NUMERATOR_WIDTH-1:0] dividend = {NUMERATOR_WIDTH{1'b0}};
    reg [DENOMINATOR_WIDTH-1:0] divisor = {DENOMINATOR_WIDTH{1'b0}};
    reg [DENOMINATOR_WIDTH:0] remainder = {(DENOMINATOR_WIDTH+1){1'b0}};
    reg [COUNT_WIDTH-1:0] count = {COUNT_WIDTH{1'b0}};

    wire [DENOMINATOR_WIDTH:0] shifted_remainder =
        {remainder[DENOMINATOR_WIDTH-1:0], dividend[NUMERATOR_WIDTH-1]};
    wire subtract_divisor = shifted_remainder >= {1'b0, divisor};
    wire [DENOMINATOR_WIDTH:0] next_remainder = subtract_divisor ?
        shifted_remainder - {1'b0, divisor} : shifted_remainder;
    wire [NUMERATOR_WIDTH-1:0] next_quotient =
        {quotient[NUMERATOR_WIDTH-2:0], subtract_divisor};

    always @(posedge clk) begin
        done <= 1'b0;
        if (rst) begin
            busy      <= 1'b0;
            quotient  <= {NUMERATOR_WIDTH{1'b0}};
            dividend  <= {NUMERATOR_WIDTH{1'b0}};
            divisor   <= {DENOMINATOR_WIDTH{1'b0}};
            remainder <= {(DENOMINATOR_WIDTH+1){1'b0}};
            count     <= {COUNT_WIDTH{1'b0}};
        end else if (start && !busy) begin
            if (denominator == 0) begin
                quotient <= {NUMERATOR_WIDTH{1'b1}};
                done <= 1'b1;
            end else begin
                busy      <= 1'b1;
                quotient  <= {NUMERATOR_WIDTH{1'b0}};
                dividend  <= numerator;
                divisor   <= denominator;
                remainder <= {(DENOMINATOR_WIDTH+1){1'b0}};
                count     <= NUMERATOR_WIDTH[COUNT_WIDTH-1:0];
            end
        end else if (busy) begin
            dividend  <= {dividend[NUMERATOR_WIDTH-2:0], 1'b0};
            remainder <= next_remainder;
            quotient  <= next_quotient;
            count     <= count - 1'b1;
            if (count == 1) begin
                busy <= 1'b0;
                done <= 1'b1;
            end
        end
    end
endmodule

