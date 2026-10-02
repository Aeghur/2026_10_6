`timescale 1ns/1ps
// Standalone DAC904 board test for the DAC904_V4_4 module.
//
// The repaired board uses RSET=2 kohm, two 25 ohm DAC loads and matched
// R4=R5=200 ohm, R6=R7=430 ohm OPA690 differential resistors.  With both
// +5 V and -5 V present, the measured OUT levels are approximately:
//
//   DAC code       OUT voltage
//      0           -0.950 V
//   8192           -0.010 V
//  16383           +0.930 V
//   8192           -0.010 V
//
// Each level is held for one second by default and the sequence repeats.
module dac904_board_test #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer HOLD_SAMPLES = 25_000_000
) (
    input  wire        clk_50m,
    output reg         dac_clk = 1'b0,
    output reg  [13:0] dac_data = 14'd0
);
    reg [24:0] hold_count = 25'd0;
    reg [1:0] test_state = 2'd0;

    // Divide the 50 MHz oscillator by two.  Data changes on the falling
    // edge of dac_clk and is stable for 20 ns before the next rising edge,
    // comfortably exceeding the DAC904 setup/hold requirements.
    always @(posedge clk_50m) begin
        dac_clk <= ~dac_clk;

        if (dac_clk) begin
            if (hold_count == HOLD_SAMPLES - 1) begin
                hold_count <= 25'd0;
                case (test_state)
                    2'd0: begin
                        test_state <= 2'd1;
                        dac_data   <= 14'd8192;
                    end
                    2'd1: begin
                        test_state <= 2'd2;
                        dac_data   <= 14'd16383;
                    end
                    2'd2: begin
                        test_state <= 2'd3;
                        dac_data   <= 14'd8192;
                    end
                    default: begin
                        test_state <= 2'd0;
                        dac_data   <= 14'd0;
                    end
                endcase
            end else begin
                hold_count <= hold_count + 1'b1;
            end
        end
    end

    initial begin
        if (CLK_HZ != 2 * HOLD_SAMPLES)
            $display("INFO: each DAC test level is not exactly one second");
    end
endmodule
