// AD9226 capture bridge for MSPM0.
// Target: Cyclone IV E EP4CE6F17C8, 50 MHz board clock.
module ads805 #(
    parameter integer CLK_HZ          = 50_000_000,
    parameter integer SAMPLE_HZ       = 25_000_000,
    parameter integer UART_BAUD       = 2_000_000,
    parameter integer DECIMATION      = 16,
    parameter integer CAPTURE_SAMPLES = 4096,
    // 2026-09-25 bench calibration at 10 kHz, scope high-Z load.
    parameter integer ADC_ZERO_CODE   = 2100,
    parameter integer DAC_ZERO_CODE   = 7237,
    parameter integer DAC_GAIN_Q16    = 495241,
    parameter integer DAC_INVERT      = 1
) (
    input  wire        clk_50m,
    input  wire [11:0] adc_data,
    input  wire        adc_otr,
    output wire        adc_clk,
    output wire        dac_clk,
    output wire [13:0] dac_data,
    output wire        uart_tx,
    input  wire        uart_rx
);
    localparam integer EFFECTIVE_RATE = SAMPLE_HZ / DECIMATION;
    localparam integer PAYLOAD_LENGTH = 8 + 2 * CAPTURE_SAMPLES;
    localparam integer FRAME_LENGTH   = 8 + PAYLOAD_LENGTH;
    localparam integer ADDR_WIDTH =
        (CAPTURE_SAMPLES <= 2) ? 1 : $clog2(CAPTURE_SAMPLES);

    reg [7:0] por_count = 8'd0;
    wire rst = ~&por_count;
    always @(posedge clk_50m)
        if (rst) por_count <= por_count + 1'b1;

    wire [11:0] sample_data;
    wire sample_otr;
    wire sample_strobe;
    ad9226_capture #(
        .CLK_HZ(CLK_HZ),
        .SAMPLE_HZ(SAMPLE_HZ),
        .PIPELINE_DISCARD(8)
    ) adc_capture_inst (
        .clk(clk_50m), .rst(rst),
        .adc_data(adc_data), .adc_otr(adc_otr), .adc_clk(adc_clk),
        .sample_data(sample_data), .sample_otr(sample_otr),
        .sample_valid(sample_strobe)
    );

    // Full-rate calibrated ADC-to-DAC path. UART still reports raw ADC codes.
    wire [13:0] mapped_dac_code;
    wire mapped_valid;
    wire mapper_clipped;
    adc_dac_voltage_mapper #(
        .ADC_ZERO_CODE(ADC_ZERO_CODE),
        .DAC_ZERO_CODE(DAC_ZERO_CODE),
        .GAIN_Q16(DAC_GAIN_Q16),
        .INVERT_OUTPUT(DAC_INVERT)
    ) voltage_mapper_inst (
        .clk(clk_50m), .rst(rst),
        .sample_valid(sample_strobe), .sample_data(sample_data),
        .mapped_valid(mapped_valid), .dac_code(mapped_dac_code),
        .clipped(mapper_clipped)
    );

    dac904_output #(.RESET_CODE(DAC_ZERO_CODE)) dac_output_inst (
        .rst(rst), .clk(clk_50m), .sample_clk(adc_clk),
        .sample_valid(mapped_valid), .dac_code(mapped_dac_code),
        .dac_clk(dac_clk), .dac_data(dac_data)
    );

    function [15:0] crc16_next;
        input [15:0] crc_in;
        input [7:0] value;
        integer bit_no;
        reg [15:0] work;
        begin
            work = crc_in ^ {value, 8'h00};
            for (bit_no = 0; bit_no < 8; bit_no = bit_no + 1)
                if (work[15])
                    work = {work[14:0], 1'b0} ^ 16'h1021;
                else
                    work = {work[14:0], 1'b0};
            crc16_next = work;
        end
    endfunction

    // Receive and validate the fixed, empty CAPTURE request frame.
    wire [7:0] rx_byte;
    wire rx_valid;
    wire rx_framing_error;
    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(UART_BAUD)) uart_rx_inst (
        .clk(clk_50m), .rst(rst), .rx(uart_rx),
        .data(rx_byte), .valid(rx_valid), .framing_error(rx_framing_error)
    );

    reg [3:0] rx_state = 4'd0;
    reg [7:0] request_type = 8'd0;
    reg [7:0] request_seq = 8'd0;
    reg [15:0] rx_crc = 16'hffff;
    reg [7:0] rx_crc_low = 8'd0;
    reg capture_request = 1'b0;
    wire request_crc_ok = ({rx_byte, rx_crc_low} == rx_crc);

    always @(posedge clk_50m) begin
        capture_request <= 1'b0;
        if (rst || rx_framing_error) begin
            rx_state <= 4'd0;
            rx_crc   <= 16'hffff;
        end else if (rx_valid) begin
            case (rx_state)
                0: rx_state <= (rx_byte == 8'ha5) ? 1 : 0;
                1: rx_state <= (rx_byte == 8'h5a) ? 2 :
                              (rx_byte == 8'ha5) ? 1 : 0;
                2: begin
                    if (rx_byte == 8'h01) begin
                        request_type <= rx_byte;
                        rx_crc   <= crc16_next(16'hffff, rx_byte);
                        rx_state <= 3;
                    end else rx_state <= 0;
                end
                3: begin
                    request_seq <= rx_byte;
                    rx_crc      <= crc16_next(rx_crc, rx_byte);
                    rx_state    <= 4;
                end
                4: begin
                    rx_crc   <= crc16_next(rx_crc, rx_byte);
                    rx_state <= (rx_byte == 0) ? 5 : 0;
                end
                5: begin
                    rx_crc   <= crc16_next(rx_crc, rx_byte);
                    rx_state <= (rx_byte == 0) ? 6 : 0;
                end
                6: begin
                    rx_crc_low <= rx_byte;
                    rx_state   <= 7;
                end
                7: begin
                    if (request_crc_ok)
                        capture_request <= 1'b1;
                    rx_state <= 0;
                end
                default: rx_state <= 0;
            endcase
        end
    end

    // Decimate and store one finite acquisition in inferred embedded RAM.
    reg [11:0] sample_memory [0:CAPTURE_SAMPLES-1];
    reg capture_active = 1'b0;
    reg capture_complete = 1'b0;
    reg capture_otr = 1'b0;
    reg capture_dac_clip = 1'b0;
    reg [7:0] capture_seq = 8'd0;
    reg [15:0] decim_count = 16'd0;
    reg [ADDR_WIDTH-1:0] write_addr = {ADDR_WIDTH{1'b0}};

    always @(posedge clk_50m) begin
        capture_complete <= 1'b0;
        if (rst) begin
            capture_active <= 1'b0;
            capture_otr    <= 1'b0;
            capture_dac_clip <= 1'b0;
            capture_seq    <= 8'd0;
            decim_count    <= 16'd0;
            write_addr     <= {ADDR_WIDTH{1'b0}};
        end else if (capture_request && !capture_active) begin
            capture_active <= 1'b1;
            capture_otr    <= 1'b0;
            capture_dac_clip <= 1'b0;
            capture_seq    <= request_seq;
            decim_count    <= 16'd0;
            write_addr     <= {ADDR_WIDTH{1'b0}};
        end else begin
            // capture_complete covers the mapper pipeline tail belonging to
            // the final stored ADC sample.
            if ((capture_active || capture_complete) && mapped_valid)
                capture_dac_clip <= capture_dac_clip | mapper_clipped;

            if (capture_active && sample_strobe) begin
                capture_otr <= capture_otr | sample_otr;
                if (decim_count == DECIMATION - 1) begin
                    decim_count <= 16'd0;
                    sample_memory[write_addr] <= sample_data;
                    if (write_addr == CAPTURE_SAMPLES - 1) begin
                        capture_active   <= 1'b0;
                        capture_complete <= 1'b1;
                    end else begin
                        write_addr <= write_addr + 1'b1;
                    end
                end else begin
                    decim_count <= decim_count + 1'b1;
                end
            end
        end
    end

    // Synchronous RAM read port.  The next address is selected while the high
    // byte of the current sample is being sent.
    reg [ADDR_WIDTH-1:0] read_addr = {ADDR_WIDTH{1'b0}};
    reg [11:0] read_data = 12'd0;
    always @(posedge clk_50m)
        read_data <= sample_memory[read_addr];

    reg tx_active = 1'b0;
    reg [15:0] tx_index = 16'd0;
    reg [15:0] tx_crc = 16'hffff;
    reg [7:0] tx_data = 8'd0;
    reg tx_start = 1'b0;
    wire tx_busy;
    wire tx_done;

    function [7:0] frame_byte;
        input [15:0] index;
        begin
            case (index)
                0: frame_byte = 8'ha5;
                1: frame_byte = 8'h5a;
                2: frame_byte = 8'h81;
                3: frame_byte = capture_seq;
                4: frame_byte = PAYLOAD_LENGTH[7:0];
                5: frame_byte = PAYLOAD_LENGTH[15:8];
                6: frame_byte = EFFECTIVE_RATE[7:0];
                7: frame_byte = EFFECTIVE_RATE[15:8];
                8: frame_byte = EFFECTIVE_RATE[23:16];
                9: frame_byte = EFFECTIVE_RATE[31:24];
                10: frame_byte = CAPTURE_SAMPLES[7:0];
                11: frame_byte = CAPTURE_SAMPLES[15:8];
                12: frame_byte = {6'd0, capture_dac_clip, capture_otr};
                13: frame_byte = 8'd0;
                default:
                    if (index < FRAME_LENGTH - 2)
                        frame_byte = index[0] ? {4'd0, read_data[11:8]} :
                                               read_data[7:0];
                    else if (index == FRAME_LENGTH - 2)
                        frame_byte = tx_crc[7:0];
                    else
                        frame_byte = tx_crc[15:8];
            endcase
        end
    endfunction

    always @(posedge clk_50m) begin
        tx_start <= 1'b0;
        if (rst) begin
            tx_active <= 1'b0;
            tx_index  <= 16'd0;
            tx_crc    <= 16'hffff;
            read_addr <= {ADDR_WIDTH{1'b0}};
        end else if (capture_complete && !tx_active) begin
            tx_active <= 1'b1;
            tx_index  <= 16'd0;
            tx_crc    <= 16'hffff;
            read_addr <= {ADDR_WIDTH{1'b0}};
        end else if (tx_active && tx_done) begin
            if (tx_index == FRAME_LENGTH - 1)
                tx_active <= 1'b0;
            else
                tx_index <= tx_index + 1'b1;
        end else if (tx_active && !tx_busy && !tx_start) begin
            tx_data  <= frame_byte(tx_index);
            tx_start <= 1'b1;
            if (tx_index >= 2 && tx_index < FRAME_LENGTH - 2)
                tx_crc <= crc16_next(tx_crc, frame_byte(tx_index));
            // Sample bytes begin at index 14. Advance after each high byte.
            if (tx_index >= 15 && tx_index < FRAME_LENGTH - 2 &&
                tx_index[0] && read_addr != CAPTURE_SAMPLES - 1)
                read_addr <= read_addr + 1'b1;
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(UART_BAUD)) uart_tx_inst (
        .clk(clk_50m), .rst(rst), .start(tx_start), .data(tx_data),
        .tx(uart_tx), .busy(tx_busy), .done(tx_done)
    );

    initial begin
        if (DECIMATION < 1) $display("ERROR: DECIMATION must be >= 1");
        if (SAMPLE_HZ % DECIMATION != 0)
            $display("ERROR: SAMPLE_HZ must divide by DECIMATION");
        if (CAPTURE_SAMPLES > 4096)
            $display("ERROR: protocol frame index width supports at most 4096 samples");
    end
endmodule
