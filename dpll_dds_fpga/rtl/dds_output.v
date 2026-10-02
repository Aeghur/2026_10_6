// Calibrated 14-bit DAC sine generator. phase_word is unsigned modulo 2^32;
// amplitude_code is peak displacement about DAC_ZERO_CODE in DAC codes.
module dds_output #(
    parameter integer DAC_ZERO_CODE = 8279,
    parameter ROM_FILE = "../rtl/sine_1024x16.hex"
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               sample_valid,
    input  wire [31:0]        phase_word,
    input  wire [13:0]        amplitude_code,
    output reg                output_valid = 1'b0,
    output reg [13:0]         dac_code = DAC_ZERO_CODE[13:0],
    output reg                clipped = 1'b0
);
    reg signed [15:0] memory [0:1023];
    reg signed [15:0] sine_sample = 16'sd0;
    reg [13:0] delayed_amplitude = 14'd0;
    reg rom_valid = 1'b0;
    reg signed [30:0] scaled_sample = 31'sd0;
    reg scale_valid = 1'b0;

    initial
        $readmemh(ROM_FILE, memory);

    wire signed [31:0] mapped_value =
        $signed(DAC_ZERO_CODE) + (scaled_sample >>> 15);

    always @(posedge clk) begin
        if (rst) begin
            sine_sample <= 16'sd0;
            delayed_amplitude <= 14'd0;
            rom_valid <= 1'b0;
            scaled_sample <= 31'sd0;
            scale_valid <= 1'b0;
            output_valid <= 1'b0;
            dac_code <= DAC_ZERO_CODE[13:0];
            clipped <= 1'b0;
        end else begin
            rom_valid <= sample_valid;
            if (sample_valid) begin
                sine_sample <= memory[phase_word[31:22]];
                delayed_amplitude <= amplitude_code;
            end

            scale_valid <= rom_valid;
            if (rom_valid)
                scaled_sample <= $signed(sine_sample) * $signed({1'b0, delayed_amplitude});

            output_valid <= scale_valid;
            clipped <= 1'b0;
            if (scale_valid) begin
                if (mapped_value < 0) begin
                    dac_code <= 14'd0;
                    clipped <= 1'b1;
                end else if (mapped_value > 16383) begin
                    dac_code <= 14'd16383;
                    clipped <= 1'b1;
                end else begin
                    dac_code <= mapped_value[13:0];
                end
            end
        end
    end
endmodule

