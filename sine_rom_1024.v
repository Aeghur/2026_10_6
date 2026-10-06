// Portable simulation model plus an explicit Cyclone IV M9K implementation.
// Quartus receives QUARTUS_SYNTHESIS from ads805.qsf; ordinary RTL simulators
// use the behavioral branch and the same checked-in initialization file.
module sine_rom_1024 #(
    parameter ROM_FILE = "dpll_dds_fpga/rtl/sine_1024x16.hex",
    parameter MIF_FILE = "sine_1024x16.mif"
) (
    input  wire              clk,
    input  wire [9:0]        address,
    output wire signed [15:0] value
);
`ifdef QUARTUS_SYNTHESIS
    wire [15:0] rom_q;
    assign value = rom_q;

    altsyncram rom_component (
        .address_a(address),
        .clock0(clk),
        .q_a(rom_q)
    );
    defparam
        rom_component.intended_device_family = "Cyclone IV E",
        rom_component.operation_mode = "ROM",
        rom_component.width_a = 16,
        rom_component.widthad_a = 10,
        rom_component.numwords_a = 1024,
        rom_component.address_reg_a = "CLOCK0",
        rom_component.outdata_reg_a = "UNREGISTERED",
        rom_component.init_file = MIF_FILE,
        rom_component.lpm_type = "altsyncram";
`else
    reg signed [15:0] memory [0:1023];
    reg signed [15:0] simulation_q = 16'sd0;
    assign value = simulation_q;
    initial $readmemh(ROM_FILE, memory);
    always @(posedge clk)
        simulation_q <= memory[address];
`endif
endmodule
