module dpll_adc_capture #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer SAMPLE_HZ = 25_000_000,
    parameter integer PIPELINE_DISCARD = 8
) (
    input  wire        clk,
    input  wire        rst,
    input  wire [11:0] adc_data,
    input  wire        adc_otr,
    output reg         adc_clk = 1'b0,
    output reg [11:0]  sample_data = 12'd0,
    output reg         sample_otr = 1'b0,
    output reg         sample_valid = 1'b0
);
    localparam integer HALF_DIV = CLK_HZ / (2 * SAMPLE_HZ);
    reg [31:0] divide_count = 32'd0;
    reg [7:0] discard_count = 8'd0;

    always @(posedge clk) begin
        sample_valid <= 1'b0;
        if (rst) begin
            divide_count <= 32'd0;
            discard_count <= 8'd0;
            adc_clk <= 1'b0;
            sample_data <= 12'd0;
            sample_otr <= 1'b0;
        end else if (divide_count == HALF_DIV - 1) begin
            divide_count <= 32'd0;
            adc_clk <= ~adc_clk;
            if (adc_clk) begin
                sample_data <= adc_data;
                sample_otr <= adc_otr;
                if (discard_count < PIPELINE_DISCARD)
                    discard_count <= discard_count + 1'b1;
                else
                    sample_valid <= 1'b1;
            end
        end else begin
            divide_count <= divide_count + 1'b1;
        end
    end

    initial begin
        if (HALF_DIV <= 0 || CLK_HZ != HALF_DIV * 2 * SAMPLE_HZ)
            $display("ERROR: invalid ADC clock ratio");
    end
endmodule

