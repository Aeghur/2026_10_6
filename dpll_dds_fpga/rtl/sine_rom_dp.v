// True dual-port signed Q1.15 sine ROM with synchronous reads.
module sine_rom_dp #(
    parameter ROM_FILE = "../rtl/sine_1024x16.hex"
) (
    input  wire              clk,
    input  wire [9:0]        address_a,
    input  wire [9:0]        address_b,
    output reg signed [15:0] sine_a = 16'sd0,
    output reg signed [15:0] sine_b = 16'sd0
);
    reg signed [15:0] memory [0:1023];

    initial
        $readmemh(ROM_FILE, memory);

    always @(posedge clk) begin
        sine_a <= memory[address_a];
        sine_b <= memory[address_b];
    end
endmodule

