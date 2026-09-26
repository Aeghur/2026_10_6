// Integer-sample programmable delay for the 25 MSPS ADC-to-DAC path.
// A value of zero bypasses RAM. Non-zero values use the inferred dual-port
// embedded memory. Configuration is updated atomically between samples.
module variable_sample_delay #(
    parameter integer DEPTH = 8192,
    parameter integer ADDR_WIDTH = $clog2(DEPTH)
) (
    input  wire                  clk,
    input  wire                  rst,
    input  wire                  sample_valid,
    input  wire [11:0]           sample_data,
    input  wire                  config_valid,
    input  wire [ADDR_WIDTH-1:0] delay_samples,
    output reg                   delayed_valid = 1'b0,
    output reg  [11:0]           delayed_data = 12'd0
);
    (* ramstyle = "M9K" *) reg [11:0] memory [0:DEPTH-1];
    reg [ADDR_WIDTH-1:0] write_addr = {ADDR_WIDTH{1'b0}};
    reg [ADDR_WIDTH-1:0] active_delay = {ADDR_WIDTH{1'b0}};
    reg [ADDR_WIDTH:0] samples_seen = {(ADDR_WIDTH+1){1'b0}};
    reg [11:0] ram_q = 12'd0;
    reg [11:0] bypass_data = 12'd0;
    reg use_bypass = 1'b0;
    reg read_pending = 1'b0;

    // Keep the memory access in this canonical single-clock block so that
    // Quartus 18.1 recognizes an M9K simple dual-port RAM.
    always @(posedge clk) begin
        if (sample_valid) begin
            memory[write_addr] <= sample_data;
            ram_q <= memory[write_addr - active_delay];
        end
    end

    always @(posedge clk) begin
        delayed_valid <= 1'b0;
        read_pending <= 1'b0;
        if (rst) begin
            write_addr   <= {ADDR_WIDTH{1'b0}};
            active_delay <= {ADDR_WIDTH{1'b0}};
            samples_seen <= {(ADDR_WIDTH+1){1'b0}};
            delayed_data <= 12'd0;
            bypass_data   <= 12'd0;
            use_bypass    <= 1'b0;
            read_pending  <= 1'b0;
        end else begin
            if (config_valid)
                active_delay <= delay_samples;
            if (sample_valid) begin
                bypass_data <= sample_data;
                use_bypass <= (active_delay == 0);
                write_addr <= write_addr + 1'b1;
                if (samples_seen < DEPTH)
                    samples_seen <= samples_seen + 1'b1;
                read_pending <=
                    (active_delay == 0) || (samples_seen >= active_delay);
            end
            if (read_pending) begin
                delayed_data <= use_bypass ? bypass_data : ram_q;
                delayed_valid <= 1'b1;
            end
        end
    end

    initial begin
        if ((1 << ADDR_WIDTH) != DEPTH)
            $display("ERROR: variable_sample_delay DEPTH must be a power of two");
    end
endmodule
