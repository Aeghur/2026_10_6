// Calibrated affine mapper from AD9226 straight-binary codes to DAC904 codes.
//
//   dac = DAC_ZERO_CODE +/- GAIN_Q16 * (adc - ADC_ZERO_CODE) / 65536
//
// The multiply is pipelined so a new sample can be accepted on every clk.
// Rounding is symmetric around zero and out-of-range results are saturated.
module adc_dac_voltage_mapper #(
    parameter integer ADC_ZERO_CODE  = 2103,
    parameter integer DAC_ZERO_CODE  = 8192,
    parameter integer GAIN_Q16       = 262144,
    parameter integer INVERT_OUTPUT  = 0
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        sample_valid,
    input  wire [11:0] sample_data,
    output reg         mapped_valid = 1'b0,
    output reg  [13:0] dac_code = 14'h2000,
    output reg         clipped = 1'b0
);
    wire signed [31:0] centered_sample =
        $signed({1'b0, sample_data}) - $signed(ADC_ZERO_CODE);
    reg signed [63:0] product_q16 = 64'sd0;
    reg product_valid = 1'b0;

    wire signed [63:0] rounded_magnitude =
        ((-product_q16) + 64'sd32768) >>> 16;
    wire signed [63:0] rounded_gain =
        (product_q16 >= 0) ? ((product_q16 + 64'sd32768) >>> 16) :
                             -rounded_magnitude;
    wire signed [63:0] signed_gain =
        INVERT_OUTPUT ? -rounded_gain : rounded_gain;
    wire signed [63:0] mapped_sum = $signed(DAC_ZERO_CODE) + signed_gain;

    always @(posedge clk) begin
        if (rst) begin
            product_q16 <= 64'sd0;
            product_valid <= 1'b0;
            mapped_valid <= 1'b0;
            dac_code <= DAC_ZERO_CODE[13:0];
            clipped <= 1'b0;
        end else begin
            product_valid <= sample_valid;
            mapped_valid <= product_valid;
            clipped <= 1'b0;

            if (sample_valid)
                product_q16 <= centered_sample * $signed(GAIN_Q16);

            if (product_valid) begin
                if (mapped_sum < 0) begin
                    dac_code <= 14'd0;
                    clipped <= 1'b1;
                end else if (mapped_sum > 16383) begin
                    dac_code <= 14'd16383;
                    clipped <= 1'b1;
                end else begin
                    dac_code <= mapped_sum[13:0];
                end
            end
        end
    end

    initial begin
        if (ADC_ZERO_CODE < 0 || ADC_ZERO_CODE > 4095)
            $display("ERROR: ADC_ZERO_CODE must be in 0..4095");
        if (DAC_ZERO_CODE < 0 || DAC_ZERO_CODE > 16383)
            $display("ERROR: DAC_ZERO_CODE must be in 0..16383");
        if (GAIN_Q16 < 0)
            $display("ERROR: GAIN_Q16 must be non-negative");
    end
endmodule
