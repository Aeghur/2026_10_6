// Pipelined 24-iteration CORDIC vectoring unit.
// Angle format: signed Q23 where +pi/2 = 0x400000 and -pi = 0x800000.
// Adapted from E:/eishero2q/学长资料/Cordic_Atan2.v (author: binbin).
module cordic_atan2 (
    input  wire               clk,
    input  wire               rst,
    input  wire               input_valid,
    input  wire signed [23:0] x_in,
    input  wire signed [23:0] y_in,
    output wire               output_valid,
    output wire signed [23:0] phase_out,
    output wire signed [23:0] magnitude_out
);
    localparam signed [23:0] CORDIC_GAIN = 24'd5093751;
    localparam signed [23:0] PI = 24'h800000;

    reg signed [24:0] x_pipe [0:24];
    reg signed [24:0] y_pipe [0:24];
    reg signed [23:0] z_pipe [0:24];
    reg [24:0] valid_pipe = 25'd0;

    wire signed [23:0] atan_table [0:23];
    assign atan_table[0]  = 24'd2097152; assign atan_table[1]  = 24'd1238053;
    assign atan_table[2]  = 24'd654271;  assign atan_table[3]  = 24'd332205;
    assign atan_table[4]  = 24'd166687;  assign atan_table[5]  = 24'd83416;
    assign atan_table[6]  = 24'd41716;   assign atan_table[7]  = 24'd20860;
    assign atan_table[8]  = 24'd10430;   assign atan_table[9]  = 24'd5215;
    assign atan_table[10] = 24'd2608;    assign atan_table[11] = 24'd1304;
    assign atan_table[12] = 24'd652;     assign atan_table[13] = 24'd326;
    assign atan_table[14] = 24'd163;     assign atan_table[15] = 24'd81;
    assign atan_table[16] = 24'd41;      assign atan_table[17] = 24'd20;
    assign atan_table[18] = 24'd10;      assign atan_table[19] = 24'd5;
    assign atan_table[20] = 24'd3;       assign atan_table[21] = 24'd1;
    assign atan_table[22] = 24'd1;       assign atan_table[23] = 24'd0;

    wire signed [47:0] x_scaled_full = $signed(x_in) * CORDIC_GAIN;
    wire signed [47:0] y_scaled_full = $signed(y_in) * CORDIC_GAIN;
    wire signed [24:0] x_scaled = x_scaled_full >>> 23;
    wire signed [24:0] y_scaled = y_scaled_full >>> 23;

    integer index;
    always @(posedge clk) begin
        if (rst) begin
            valid_pipe <= 25'd0;
            for (index = 0; index <= 24; index = index + 1) begin
                x_pipe[index] <= 25'sd0;
                y_pipe[index] <= 25'sd0;
                z_pipe[index] <= 24'sd0;
            end
        end else begin
            valid_pipe <= {valid_pipe[23:0], input_valid};
            if (x_in >= 0) begin
                x_pipe[0] <= x_scaled;
                y_pipe[0] <= y_scaled;
                z_pipe[0] <= 24'sd0;
            end else if (y_in >= 0) begin
                x_pipe[0] <= -x_scaled;
                y_pipe[0] <= -y_scaled;
                z_pipe[0] <= PI;
            end else begin
                x_pipe[0] <= -x_scaled;
                y_pipe[0] <= -y_scaled;
                z_pipe[0] <= -PI;
            end

            for (index = 0; index < 24; index = index + 1) begin
                if (y_pipe[index] >= 0) begin
                    x_pipe[index+1] <= x_pipe[index] + (y_pipe[index] >>> index);
                    y_pipe[index+1] <= y_pipe[index] - (x_pipe[index] >>> index);
                    z_pipe[index+1] <= z_pipe[index] + atan_table[index];
                end else begin
                    x_pipe[index+1] <= x_pipe[index] - (y_pipe[index] >>> index);
                    y_pipe[index+1] <= y_pipe[index] + (x_pipe[index] >>> index);
                    z_pipe[index+1] <= z_pipe[index] - atan_table[index];
                end
            end
        end
    end

    wire signed [24:0] magnitude_full = x_pipe[24];
    assign output_valid = valid_pipe[24];
    assign phase_out = z_pipe[24];
    assign magnitude_out =
        (magnitude_full > 25'sd8388607) ? 24'sd8388607 :
        (magnitude_full < 0) ? 24'sd0 : magnitude_full[23:0];
endmodule

