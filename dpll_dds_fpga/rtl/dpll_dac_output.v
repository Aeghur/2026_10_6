module dpll_dac_output #(
    parameter [13:0] RESET_CODE = 14'd8279
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        sample_clk,
    input  wire        code_valid,
    input  wire [13:0] dac_code,
    output wire        dac_clk,
    output reg [13:0]  dac_data = RESET_CODE
);
    assign dac_clk = ~sample_clk;

    // Update halfway between DAC rising latch edges.
    always @(negedge clk or posedge rst) begin
        if (rst)
            dac_data <= RESET_CODE;
        else if (code_valid)
            dac_data <= dac_code;
    end
endmodule

