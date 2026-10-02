// MSPM0 control/status bridge using the existing A5 5A + CRC16-CCITT frame.
// RX 0x10 payload (11 bytes): lag_word u32, calibration_word u32,
// amplitude u16, control u8 (bit0 output enable).
// RX 0x01 empty payload requests a 0x90 status frame.
module dpll_uart_bridge #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer BAUD = 2_000_000
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               uart_rx,
    output wire               uart_tx,
    output reg [31:0]         phase_lag_word = 32'd0,
    output reg [31:0]         calibration_phase_word = 32'd0,
    output reg [13:0]         amplitude_code = 14'd2143,
    output reg                output_enable = 1'b1,
    output reg                config_valid = 1'b0,
    input  wire [31:0]        phase_increment,
    input  wire signed [23:0] phase_error,
    input  wire               locked,
    input  wire               signal_present,
    input  wire               dac_clipped,
    input  wire               adc_otr,
    input  wire [13:0]        dac_code
);
    function [15:0] crc16_next;
        input [15:0] crc_in;
        input [7:0] value;
        integer bit_number;
        reg [15:0] work;
        begin
            work = crc_in ^ {value, 8'h00};
            for (bit_number = 0; bit_number < 8; bit_number = bit_number + 1)
                work = work[15] ? {work[14:0],1'b0} ^ 16'h1021 :
                                  {work[14:0],1'b0};
            crc16_next = work;
        end
    endfunction

    wire [7:0] rx_byte;
    wire rx_valid;
    wire rx_error;
    dpll_uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) receiver (
        .clk(clk), .rst(rst), .rx(uart_rx), .data(rx_byte),
        .valid(rx_valid), .framing_error(rx_error)
    );

    reg [3:0] rx_state = 4'd0;
    reg [7:0] rx_type = 8'd0;
    reg [7:0] rx_sequence = 8'd0;
    reg [15:0] rx_length = 16'd0;
    reg [3:0] payload_index = 4'd0;
    reg [15:0] rx_crc = 16'hffff;
    reg [7:0] rx_crc_low = 8'd0;
    reg [31:0] pending_lag = 32'd0;
    reg [31:0] pending_calibration = 32'd0;
    reg [15:0] pending_amplitude = 16'd2143;
    reg [7:0] pending_control = 8'd1;
    reg status_request = 1'b0;

    always @(posedge clk) begin
        config_valid <= 1'b0;
        status_request <= 1'b0;
        if (rst || rx_error) begin
            rx_state <= 4'd0;
            rx_crc <= 16'hffff;
        end else if (rx_valid) begin
            case (rx_state)
                0: rx_state <= (rx_byte == 8'ha5) ? 1 : 0;
                1: rx_state <= (rx_byte == 8'h5a) ? 2 :
                              (rx_byte == 8'ha5) ? 1 : 0;
                2: begin
                    rx_type <= rx_byte;
                    rx_crc <= crc16_next(16'hffff, rx_byte);
                    rx_state <= 3;
                end
                3: begin
                    rx_sequence <= rx_byte;
                    rx_crc <= crc16_next(rx_crc, rx_byte);
                    rx_state <= 4;
                end
                4: begin
                    rx_length[7:0] <= rx_byte;
                    rx_crc <= crc16_next(rx_crc, rx_byte);
                    rx_state <= 5;
                end
                5: begin
                    rx_length[15:8] <= rx_byte;
                    rx_crc <= crc16_next(rx_crc, rx_byte);
                    payload_index <= 4'd0;
                    if ((rx_type == 8'h01 && {rx_byte,rx_length[7:0]} == 0) ||
                        (rx_type == 8'h10 && {rx_byte,rx_length[7:0]} == 11))
                        rx_state <= ({rx_byte,rx_length[7:0]} == 0) ? 7 : 6;
                    else
                        rx_state <= 0;
                end
                6: begin
                    rx_crc <= crc16_next(rx_crc, rx_byte);
                    case (payload_index)
                        0: pending_lag[7:0] <= rx_byte;
                        1: pending_lag[15:8] <= rx_byte;
                        2: pending_lag[23:16] <= rx_byte;
                        3: pending_lag[31:24] <= rx_byte;
                        4: pending_calibration[7:0] <= rx_byte;
                        5: pending_calibration[15:8] <= rx_byte;
                        6: pending_calibration[23:16] <= rx_byte;
                        7: pending_calibration[31:24] <= rx_byte;
                        8: pending_amplitude[7:0] <= rx_byte;
                        9: pending_amplitude[15:8] <= rx_byte;
                        10: pending_control <= rx_byte;
                    endcase
                    if (payload_index == 10)
                        rx_state <= 7;
                    else
                        payload_index <= payload_index + 1'b1;
                end
                7: begin
                    rx_crc_low <= rx_byte;
                    rx_state <= 8;
                end
                8: begin
                    if ({rx_byte,rx_crc_low} == rx_crc) begin
                        if (rx_type == 8'h10) begin
                            phase_lag_word <= pending_lag;
                            calibration_phase_word <= pending_calibration;
                            amplitude_code <= pending_amplitude > 16'd8104 ?
                                14'd8104 : pending_amplitude[13:0];
                            output_enable <= pending_control[0];
                            config_valid <= 1'b1;
                        end else if (rx_type == 8'h01) begin
                            status_request <= 1'b1;
                        end
                    end
                    rx_state <= 0;
                end
                default: rx_state <= 0;
            endcase
        end
    end

    reg [31:0] status_increment = 32'd0;
    reg signed [31:0] status_error = 32'sd0;
    reg [15:0] status_flags = 16'd0;
    reg [13:0] status_dac_code = 14'd0;
    reg [7:0] status_sequence = 8'd0;
    reg tx_active = 1'b0;
    reg [4:0] tx_index = 5'd0;
    reg [15:0] tx_crc = 16'hffff;
    reg [7:0] tx_data = 8'd0;
    reg tx_start = 1'b0;
    wire tx_busy;
    wire tx_done;

    function [7:0] status_byte;
        input [4:0] index;
        begin
            case (index)
                0: status_byte = 8'ha5;
                1: status_byte = 8'h5a;
                2: status_byte = 8'h90;
                3: status_byte = status_sequence;
                4: status_byte = 8'd16;
                5: status_byte = 8'd0;
                6: status_byte = status_increment[7:0];
                7: status_byte = status_increment[15:8];
                8: status_byte = status_increment[23:16];
                9: status_byte = status_increment[31:24];
                10: status_byte = status_error[7:0];
                11: status_byte = status_error[15:8];
                12: status_byte = status_error[23:16];
                13: status_byte = status_error[31:24];
                14: status_byte = status_flags[7:0];
                15: status_byte = status_flags[15:8];
                16: status_byte = status_dac_code[7:0];
                17: status_byte = {2'b00,status_dac_code[13:8]};
                18: status_byte = 8'h40; // 25,000,000 little endian
                19: status_byte = 8'h78;
                20: status_byte = 8'h7d;
                21: status_byte = 8'h01;
                22: status_byte = tx_crc[7:0];
                default: status_byte = tx_crc[15:8];
            endcase
        end
    endfunction

    dpll_uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) transmitter (
        .clk(clk), .rst(rst), .start(tx_start), .data(tx_data),
        .tx(uart_tx), .busy(tx_busy), .done(tx_done)
    );

    always @(posedge clk) begin
        tx_start <= 1'b0;
        if (rst) begin
            tx_active <= 1'b0;
            tx_index <= 5'd0;
            tx_crc <= 16'hffff;
        end else begin
            if (status_request && !tx_active) begin
                status_increment <= phase_increment;
                status_error <= {{8{phase_error[23]}},phase_error};
                status_flags <= {12'd0,adc_otr,dac_clipped,signal_present,locked};
                status_dac_code <= dac_code;
                status_sequence <= rx_sequence;
                tx_active <= 1'b1;
                tx_index <= 5'd0;
                tx_crc <= 16'hffff;
            end

            if (tx_active && !tx_busy && !tx_start && !tx_done) begin
                tx_data <= status_byte(tx_index);
                tx_start <= 1'b1;
            end

            if (tx_active && tx_done) begin
                if (tx_index >= 2 && tx_index <= 21)
                    tx_crc <= crc16_next(tx_crc, tx_data);
                if (tx_index == 23) begin
                    tx_active <= 1'b0;
                    tx_index <= 5'd0;
                end else begin
                    tx_index <= tx_index + 1'b1;
                end
            end
        end
    end
endmodule

