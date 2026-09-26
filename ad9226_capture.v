// Reusable AD9226 parallel capture interface.
//
// AD9226 samples VINA/VINB on adc_clk rising edges.  Its pipelined data is
// captured by the FPGA on the following falling edge, giving the bus almost
// one half ADC period to settle.
module ad9226_capture #(
    parameter integer CLK_HZ           = 50_000_000,
    parameter integer SAMPLE_HZ        = 25_000_000,
    parameter integer PIPELINE_DISCARD = 8
) (
    input  wire        clk,
    input  wire        rst,
    input  wire [11:0] adc_data,
    input  wire        adc_otr,
    output reg         adc_clk = 1'b0,
    output reg  [11:0] sample_data = 12'd0,
    output reg         sample_otr = 1'b0,
    output reg         sample_valid = 1'b0
);
    localparam integer ADC_HALF_DIV = CLK_HZ / (2 * SAMPLE_HZ);
    localparam integer ADC_DIV_OK =
        (ADC_HALF_DIV > 0) && (CLK_HZ == ADC_HALF_DIV * 2 * SAMPLE_HZ);

    reg [31:0] div_count = 32'd0;
    reg [31:0] discard_count = 32'd0;

    always @(posedge clk) begin
        sample_valid <= 1'b0;
        if (rst) begin
            div_count     <= 32'd0;
            discard_count <= 32'd0;
            adc_clk       <= 1'b0;
            sample_data   <= 12'd0;
            sample_otr    <= 1'b0;
        end else if (div_count == ADC_HALF_DIV - 1) begin
            div_count <= 32'd0;
            adc_clk   <= ~adc_clk;
            // adc_clk is the old value here.  High means this update creates
            // the falling edge at the converter clock output.
            if (adc_clk) begin
                sample_data <= adc_data;
                sample_otr  <= adc_otr;
                if (discard_count < PIPELINE_DISCARD)
                    discard_count <= discard_count + 1'b1;
                else
                    sample_valid <= 1'b1;
            end
        end else begin
            div_count <= div_count + 1'b1;
        end
    end

    initial begin
        if (!ADC_DIV_OK)
            $display("ERROR: CLK_HZ must divide exactly to SAMPLE_HZ");
        if (PIPELINE_DISCARD < 7)
            $display("ERROR: AD9226 requires at least seven discarded words");
    end
endmodule
