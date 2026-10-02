// Positive-going zero-crossing period estimator with hysteresis. Eight input
// periods are accumulated before division to reduce single-sample jitter.
// phase_increment = round(2^32 * AVERAGE_CYCLES / accumulated_samples).
module coarse_frequency_estimator #(
    parameter integer AVERAGE_LOG2 = 3,
    parameter integer HYSTERESIS_CODES = 12,
    parameter integer MIN_PERIOD_SAMPLES = 125,
    parameter integer MAX_PERIOD_SAMPLES = 50000
) (
    input  wire                clk,
    input  wire                rst,
    input  wire                sample_valid,
    input  wire signed [12:0]  sample_centered,
    output reg                 crossing = 1'b0,
    output reg                 frequency_valid = 1'b0,
    output reg [31:0]          phase_increment = 32'd0
);
    localparam integer AVERAGE_CYCLES = 1 << AVERAGE_LOG2;
    localparam [35:0] DIVIDEND = 36'h800000000; // 2^32 * 8

    reg armed_negative = 1'b0;
    reg have_previous_crossing = 1'b0;
    reg [23:0] period_counter = 24'd0;
    reg [23:0] period_sum = 24'd0;
    reg [AVERAGE_LOG2-1:0] interval_count = {AVERAGE_LOG2{1'b0}};
    reg divider_start = 1'b0;
    reg [23:0] divider_denominator = 24'd1;
    wire divider_busy;
    wire divider_done;
    wire [35:0] divider_quotient;

    unsigned_serial_divider #(
        .NUMERATOR_WIDTH(36), .DENOMINATOR_WIDTH(24)
    ) divider_inst (
        .clk(clk), .rst(rst), .start(divider_start),
        .numerator(DIVIDEND), .denominator(divider_denominator),
        .busy(divider_busy), .done(divider_done),
        .quotient(divider_quotient)
    );

    wire [23:0] completed_period = period_counter + 1'b1;
    wire [24:0] completed_sum = period_sum + completed_period;
    wire period_in_range =
        completed_period >= MIN_PERIOD_SAMPLES &&
        completed_period <= MAX_PERIOD_SAMPLES;

    always @(posedge clk) begin
        crossing <= 1'b0;
        frequency_valid <= 1'b0;
        divider_start <= 1'b0;

        if (rst) begin
            armed_negative <= 1'b0;
            have_previous_crossing <= 1'b0;
            period_counter <= 24'd0;
            period_sum <= 24'd0;
            interval_count <= {AVERAGE_LOG2{1'b0}};
            divider_denominator <= 24'd1;
            phase_increment <= 32'd0;
        end else begin
            if (divider_done) begin
                phase_increment <= divider_quotient[31:0];
                frequency_valid <= 1'b1;
            end

            if (sample_valid) begin
                if (have_previous_crossing && period_counter < 24'hffffff)
                    period_counter <= period_counter + 1'b1;

                if (sample_centered <= -HYSTERESIS_CODES)
                    armed_negative <= 1'b1;

                if (armed_negative && sample_centered >= HYSTERESIS_CODES) begin
                    crossing <= 1'b1;
                    armed_negative <= 1'b0;
                    period_counter <= 24'd0;

                    if (!have_previous_crossing) begin
                        have_previous_crossing <= 1'b1;
                        period_sum <= 24'd0;
                        interval_count <= {AVERAGE_LOG2{1'b0}};
                    end else if (!period_in_range) begin
                        have_previous_crossing <= 1'b0;
                        period_sum <= 24'd0;
                        interval_count <= {AVERAGE_LOG2{1'b0}};
                    end else if (interval_count == AVERAGE_CYCLES - 1) begin
                        if (!divider_busy) begin
                            divider_denominator <= completed_sum[23:0];
                            divider_start <= 1'b1;
                        end
                        period_sum <= 24'd0;
                        interval_count <= {AVERAGE_LOG2{1'b0}};
                    end else begin
                        period_sum <= completed_sum[23:0];
                        interval_count <= interval_count + 1'b1;
                    end
                end else if (period_counter > MAX_PERIOD_SAMPLES) begin
                    have_previous_crossing <= 1'b0;
                    period_counter <= 24'd0;
                    period_sum <= 24'd0;
                    interval_count <= {AVERAGE_LOG2{1'b0}};
                end
            end
        end
    end
endmodule

