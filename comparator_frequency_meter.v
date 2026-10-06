// Measure a comparator square wave in the 50 MHz clock domain.
// A 100 ms gate gives 10 Hz display resolution and a new DDS frequency
// without relying on the ADC waveform or the MSPM0 FFT.
module comparator_frequency_meter #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer SAMPLE_HZ = 25_000_000,
    parameter integer GATE_HZ = 10,
    parameter integer MIN_HZ = 1_000,
    parameter integer MAX_HZ = 100_000
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        comparator_in,
    output reg         rising_edge = 1'b0,
    output reg         frequency_valid = 1'b0,
    output reg  [16:0] frequency_hz = 17'd0,
    output reg  [31:0] phase_step = 32'd0
);
    localparam integer GATE_CYCLES = CLK_HZ / GATE_HZ;
    localparam integer MIN_EDGES = MIN_HZ / GATE_HZ;
    localparam integer MAX_EDGES = MAX_HZ / GATE_HZ;
    // The rounded Q16 multiplier converts edges per gate directly to the
    // 32-bit phase increment for a SAMPLE_HZ DDS update clock.
    localparam [63:0] STEP_SCALE_Q16 =
        (((64'd1 << 32) * GATE_HZ * 65536) + SAMPLE_HZ / 2) / SAMPLE_HZ;

    reg sync0 = 1'b0;
    reg sync1 = 1'b0;
    reg [3:0] recent = 4'b0000;
    reg stable_high = 1'b0;
    reg [31:0] gate_count = 32'd0;
    reg [14:0] edge_count = 15'd0;
    wire [14:0] completed_count = edge_count + rising_edge;
    wire [63:0] step_product = completed_count * STEP_SCALE_Q16;

    always @(posedge clk) begin
        sync0 <= comparator_in;
        sync1 <= sync0;
        recent <= {recent[2:0], sync1};
        rising_edge <= 1'b0;
        if (rst) begin
            sync0 <= 1'b0;
            sync1 <= 1'b0;
            recent <= 4'b0000;
            stable_high <= 1'b0;
            rising_edge <= 1'b0;
            gate_count <= 32'd0;
            edge_count <= 15'd0;
            frequency_valid <= 1'b0;
            frequency_hz <= 17'd0;
            phase_step <= 32'd0;
        end else begin
            if (&recent && !stable_high) begin
                stable_high <= 1'b1;
                rising_edge <= 1'b1;
            end else if (~|recent) begin
                stable_high <= 1'b0;
            end

            frequency_valid <= 1'b0;
            if (gate_count == GATE_CYCLES - 1) begin
                gate_count <= 32'd0;
                edge_count <= 15'd0;
                frequency_valid <= 1'b1;
                // One edge of boundary tolerance avoids rejecting a valid
                // end-point frequency because of gate/edge alignment.
                if (completed_count >= MIN_EDGES - 1 &&
                    completed_count <= MAX_EDGES + 1) begin
                    frequency_hz <= completed_count * GATE_HZ;
                    phase_step <= (step_product + 64'd32768) >> 16;
                end else begin
                    frequency_hz <= 17'd0;
                    phase_step <= 32'd0;
                end
            end else begin
                gate_count <= gate_count + 1'b1;
                // Retain one count beyond the valid range to reject an
                // over-frequency or noisy input instead of wrapping.
                if (rising_edge && edge_count < MAX_EDGES + 2)
                    edge_count <= edge_count + 1'b1;
            end
        end
    end

    initial begin
        if (GATE_HZ < 1 || CLK_HZ < GATE_HZ || CLK_HZ % GATE_HZ != 0)
            $display("ERROR: invalid frequency-meter gate rate");
        if (MIN_HZ < GATE_HZ || MIN_HZ % GATE_HZ != 0 ||
            MAX_HZ % GATE_HZ != 0 || MAX_EDGES + 2 >= 32768)
            $display("ERROR: invalid frequency-meter range");
    end
endmodule
