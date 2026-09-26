// Reusable DAC904 timing interface for an already mapped 14-bit code stream.
//
// DAC904 Bit 1 is D13 (MSB) and Bit 14 is D0 (LSB). Updating dac_data on clk
// falling edges avoids a derived-clock pulse race. dac_clk is the inverse of
// sample_clk, leaving ample setup and hold time before the next rising edge.
module dac904_output #(
    parameter [13:0] RESET_CODE = 14'h2000
) (
    input  wire        rst,
    input  wire        clk,
    input  wire        sample_clk,
    input  wire        sample_valid,
    input  wire [13:0] dac_code,
    output wire        dac_clk,
    output reg  [13:0] dac_data = RESET_CODE
);
    assign dac_clk = ~sample_clk;

    always @(negedge clk or posedge rst) begin
        if (rst)
            dac_data <= RESET_CODE;
        else if (sample_valid)
            dac_data <= dac_code;
    end
endmodule
