// IQ phase detector for an ADC sine wave and a local NCO sine wave.
// For x=sin(input_phase):
//   I = LPF(x*cos(local)) = A/2*sin(input-local)
//   Q = LPF(x*sin(local)) = A/2*cos(input-local)
// atan2(I,Q) therefore returns signed input_phase-local_phase.
module iq_phase_detector #(
    parameter integer LPF_SHIFT = 16,
    parameter ROM_FILE = "../rtl/sine_1024x16.hex"
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               sample_valid,
    input  wire signed [12:0] sample_centered,
    input  wire [31:0]        local_phase,
    output wire               phase_valid,
    output wire signed [23:0] phase_error,
    output wire signed [23:0] signal_magnitude,
    output wire signed [23:0] i_filtered,
    output wire signed [23:0] q_filtered
);
    wire [31:0] cosine_phase = local_phase + 32'h40000000;
    wire signed [15:0] local_sine;
    wire signed [15:0] local_cosine;
    sine_rom_dp #(.ROM_FILE(ROM_FILE)) carrier_rom (
        .clk(clk),
        .address_a(local_phase[31:22]),
        .address_b(cosine_phase[31:22]),
        .sine_a(local_sine), .sine_b(local_cosine)
    );

    reg signed [12:0] delayed_sample = 13'sd0;
    reg delayed_valid = 1'b0;
    reg signed [39:0] i_lpf = 40'sd0;
    reg signed [39:0] q_lpf = 40'sd0;

    wire signed [28:0] i_product =
        $signed(delayed_sample) * $signed(local_cosine);
    wire signed [28:0] q_product =
        $signed(delayed_sample) * $signed(local_sine);
    wire signed [39:0] i_product_extended = {{11{i_product[28]}}, i_product};
    wire signed [39:0] q_product_extended = {{11{q_product[28]}}, q_product};

    always @(posedge clk) begin
        if (rst) begin
            delayed_sample <= 13'sd0;
            delayed_valid <= 1'b0;
            i_lpf <= 40'sd0;
            q_lpf <= 40'sd0;
        end else begin
            delayed_valid <= sample_valid;
            if (sample_valid)
                delayed_sample <= sample_centered;
            if (delayed_valid) begin
                i_lpf <= i_lpf + ((i_product_extended - i_lpf) >>> LPF_SHIFT);
                q_lpf <= q_lpf + ((q_product_extended - q_lpf) >>> LPF_SHIFT);
            end
        end
    end

    function signed [23:0] saturate_q23;
        input signed [39:0] value;
        begin
            if (value > 40'sd8388607)
                saturate_q23 = 24'sd8388607;
            else if (value < -40'sd8388608)
                saturate_q23 = -24'sd8388608;
            else
                saturate_q23 = value[23:0];
        end
    endfunction

    // Divide the ADC-code x Q1.15 products by eight so normal input levels
    // occupy most of the signed Q23 CORDIC input range without clipping.
    assign i_filtered = saturate_q23(i_lpf >>> 3);
    assign q_filtered = saturate_q23(q_lpf >>> 3);

    cordic_atan2 phase_cordic (
        .clk(clk), .rst(rst), .input_valid(delayed_valid),
        .x_in(q_filtered), .y_in(i_filtered),
        .output_valid(phase_valid), .phase_out(phase_error),
        .magnitude_out(signal_magnitude)
    );
endmodule

