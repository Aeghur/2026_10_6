// Type-II digital PLL controller. The coarse estimator supplies acquisition;
// atan2 phase error supplies fine tracking. Phase error is signed Q23 radians
// scaled so pi=2^23. Frequency is a 32-bit phase increment per 25 MSPS sample.
module dpll_controller #(
    parameter [31:0] DEFAULT_PHASE_INCREMENT = 32'd1717987,
    parameter [31:0] MIN_PHASE_INCREMENT = 32'd171799,
    parameter [31:0] MAX_PHASE_INCREMENT = 32'd17179869,
    parameter integer LOOP_DECIMATION_LOG2 = 10,
    parameter integer KP_SHIFT = 7,
    parameter integer KI_SHIFT = 16,
    parameter signed [39:0] MAX_INTEGRATOR = 40'sd1000000,
    parameter signed [23:0] MIN_MAGNITUDE = 24'sd50000
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               phase_valid,
    input  wire signed [23:0] phase_error,
    input  wire signed [23:0] signal_magnitude,
    input  wire               coarse_valid,
    input  wire [31:0]        coarse_phase_increment,
    output reg  [31:0]        phase_increment = DEFAULT_PHASE_INCREMENT,
    output reg                locked = 1'b0,
    output reg                signal_present = 1'b0,
    output reg                reacquired = 1'b0
);
    reg [31:0] base_increment = DEFAULT_PHASE_INCREMENT;
    reg signed [39:0] integrator = 40'sd0;
    reg [LOOP_DECIMATION_LOG2-1:0] loop_count =
        {LOOP_DECIMATION_LOG2{1'b0}};
    reg [5:0] lock_count = 6'd0;
    reg [3:0] unlock_count = 4'd0;
    reg coarse_seen = 1'b0;

    wire [31:0] coarse_difference =
        coarse_phase_increment > phase_increment ?
        coarse_phase_increment - phase_increment :
        phase_increment - coarse_phase_increment;
    wire coarse_step = coarse_difference > (phase_increment >> 5);

    wire signed [39:0] extended_error = {{16{phase_error[23]}}, phase_error};
    wire signed [39:0] proportional = extended_error >>> KP_SHIFT;
    wire signed [39:0] integral_step = extended_error >>> KI_SHIFT;
    wire signed [40:0] unclamped_integrator = integrator + integral_step;
    wire signed [39:0] next_integrator =
        (unclamped_integrator > MAX_INTEGRATOR) ? MAX_INTEGRATOR :
        (unclamped_integrator < -MAX_INTEGRATOR) ? -MAX_INTEGRATOR :
        unclamped_integrator[39:0];
    wire signed [41:0] requested_increment =
        $signed({1'b0, base_increment}) + next_integrator + proportional;
    wire [31:0] clamped_increment =
        (requested_increment < $signed({1'b0, MIN_PHASE_INCREMENT})) ?
            MIN_PHASE_INCREMENT :
        (requested_increment > $signed({1'b0, MAX_PHASE_INCREMENT})) ?
            MAX_PHASE_INCREMENT : requested_increment[31:0];

    wire [24:0] phase_absolute = phase_error[23] ?
        -$signed({phase_error[23], phase_error}) :
         $signed({phase_error[23], phase_error});
    localparam [24:0] LOCK_PHASE_LIMIT = 25'd699051;    // 15 degrees
    localparam [24:0] UNLOCK_PHASE_LIMIT = 25'd2796203; // 60 degrees

    always @(posedge clk) begin
        reacquired <= 1'b0;
        if (rst) begin
            base_increment <= DEFAULT_PHASE_INCREMENT;
            phase_increment <= DEFAULT_PHASE_INCREMENT;
            integrator <= 40'sd0;
            loop_count <= {LOOP_DECIMATION_LOG2{1'b0}};
            lock_count <= 6'd0;
            unlock_count <= 4'd0;
            locked <= 1'b0;
            signal_present <= 1'b0;
            coarse_seen <= 1'b0;
        end else if (coarse_valid && (!coarse_seen || coarse_step)) begin
            base_increment <= coarse_phase_increment;
            phase_increment <= coarse_phase_increment;
            integrator <= 40'sd0;
            locked <= 1'b0;
            lock_count <= 6'd0;
            unlock_count <= 4'd0;
            coarse_seen <= 1'b1;
            reacquired <= 1'b1;
        end else if (phase_valid) begin
            loop_count <= loop_count + 1'b1;
            if (loop_count == {LOOP_DECIMATION_LOG2{1'b1}}) begin
                signal_present <= signal_magnitude >= MIN_MAGNITUDE;
                if (signal_magnitude >= MIN_MAGNITUDE) begin
                    integrator <= next_integrator;
                    phase_increment <= clamped_increment;

                    if (phase_absolute <= LOCK_PHASE_LIMIT) begin
                        unlock_count <= 4'd0;
                        if (lock_count < 6'd32)
                            lock_count <= lock_count + 1'b1;
                        if (lock_count >= 6'd31)
                            locked <= 1'b1;
                    end else begin
                        lock_count <= 6'd0;
                        if (phase_absolute >= UNLOCK_PHASE_LIMIT) begin
                            if (unlock_count < 4'd8)
                                unlock_count <= unlock_count + 1'b1;
                            if (unlock_count >= 4'd7)
                                locked <= 1'b0;
                        end else begin
                            unlock_count <= 4'd0;
                        end
                    end
                end else begin
                    integrator <= 40'sd0;
                    locked <= 1'b0;
                    lock_count <= 6'd0;
                    unlock_count <= 4'd0;
                end
            end
        end
    end
endmodule

