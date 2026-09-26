`timescale 1ns/1ps
module tb_ads805;
    localparam integer TEST_SAMPLES = 16;
    localparam integer FRAME_LENGTH = 8 + 8 + TEST_SAMPLES * 2;
    reg clk = 0;
    reg [11:0] adc_data = 0;
    reg adc_otr = 0;
    reg uart_rx = 1;
    wire adc_clk;
    wire dac_clk;
    wire [13:0] dac_data;
    wire uart_tx;

    reg [7:0] frame [0:FRAME_LENGTH-1];
    integer frame_count = 0;
    integer i;
    integer errors = 0;
    reg [15:0] calculated_crc;
    reg [15:0] received_crc;

    always #10 clk = ~clk;
    always @(posedge adc_clk) begin
        adc_data <= adc_data + 1'b1;
        if (adc_data == 12'h080)
            adc_otr <= 1'b1;
        else
            adc_otr <= 1'b0;
    end

    function [15:0] crc16_next;
        input [15:0] crc_in;
        input [7:0] value;
        integer n;
        reg [15:0] work;
        begin
            work = crc_in ^ {value, 8'h00};
            for (n = 0; n < 8; n = n + 1)
                work = work[15] ? ({work[14:0],1'b0} ^ 16'h1021) :
                                  {work[14:0],1'b0};
            crc16_next = work;
        end
    endfunction

    task send_uart_byte;
        input [7:0] value;
        integer bit_no;
        begin
            uart_rx = 1'b0;
            #500;
            for (bit_no = 0; bit_no < 8; bit_no = bit_no + 1) begin
                uart_rx = value[bit_no];
                #500;
            end
            uart_rx = 1'b1;
            #500;
        end
    endtask

    task receive_uart_byte;
        output [7:0] value;
        integer bit_no;
        begin
            @(negedge uart_tx);
            #750;
            for (bit_no = 0; bit_no < 8; bit_no = bit_no + 1) begin
                value[bit_no] = uart_tx;
                #500;
            end
            if (uart_tx !== 1'b1) begin
                $display("FAIL: bad UART stop bit");
                errors = errors + 1;
            end
            #250;
        end
    endtask

    ads805 #(
        .UART_BAUD(2_000_000),
        .DECIMATION(64),
        .CAPTURE_SAMPLES(TEST_SAMPLES)
    ) dut (
        .clk_50m(clk), .adc_data(adc_data), .adc_otr(adc_otr),
        .adc_clk(adc_clk), .dac_clk(dac_clk), .dac_data(dac_data),
        .uart_tx(uart_tx), .uart_rx(uart_rx)
    );

    initial begin
        // Wait for power-on reset and the eight discarded pipeline words.
        #15000;
        calculated_crc = 16'hffff;
        calculated_crc = crc16_next(calculated_crc, 8'h01);
        calculated_crc = crc16_next(calculated_crc, 8'h37);
        calculated_crc = crc16_next(calculated_crc, 8'h00);
        calculated_crc = crc16_next(calculated_crc, 8'h00);
        send_uart_byte(8'ha5);
        send_uart_byte(8'h5a);
        send_uart_byte(8'h01);
        send_uart_byte(8'h37);
        send_uart_byte(8'h00);
        send_uart_byte(8'h00);
        send_uart_byte(calculated_crc[7:0]);
        send_uart_byte(calculated_crc[15:8]);

        for (i = 0; i < FRAME_LENGTH; i = i + 1) begin
            receive_uart_byte(frame[i]);
            frame_count = frame_count + 1;
        end

        if ({frame[1],frame[0]} !== 16'h5aa5) begin
            $display("FAIL: sync %02x %02x", frame[0], frame[1]);
            errors = errors + 1;
        end
        if (frame[2] !== 8'h81 || frame[3] !== 8'h37) begin
            $display("FAIL: type/sequence %02x %02x", frame[2], frame[3]);
            errors = errors + 1;
        end
        if ({frame[5],frame[4]} !== 8 + 2 * TEST_SAMPLES) begin
            $display("FAIL: payload length %0d", {frame[5],frame[4]});
            errors = errors + 1;
        end
        if ({frame[9],frame[8],frame[7],frame[6]} !== 32'd390625 ||
            {frame[11],frame[10]} !== TEST_SAMPLES) begin
            $display("FAIL: capture metadata");
            errors = errors + 1;
        end

        calculated_crc = 16'hffff;
        for (i = 2; i < FRAME_LENGTH - 2; i = i + 1)
            calculated_crc = crc16_next(calculated_crc, frame[i]);
        received_crc = {frame[FRAME_LENGTH-1], frame[FRAME_LENGTH-2]};
        if (received_crc !== calculated_crc) begin
            $display("FAIL: CRC expected=%04x got=%04x",
                     calculated_crc, received_crc);
            errors = errors + 1;
        end

        // Each retained sample must be exactly 64 ADC conversion codes apart.
        for (i = 1; i < TEST_SAMPLES; i = i + 1)
            if ((({frame[15+2*i][3:0],frame[14+2*i]} -
                   {frame[13+2*i][3:0],frame[12+2*i]}) & 12'hfff) != 64) begin
                $display("FAIL: sample step at %0d", i);
                errors = errors + 1;
            end

        // MSPM0 programs a five-sample phase delay with command 0x03.
        calculated_crc = 16'hffff;
        calculated_crc = crc16_next(calculated_crc, 8'h03);
        calculated_crc = crc16_next(calculated_crc, 8'h38);
        calculated_crc = crc16_next(calculated_crc, 8'h02);
        calculated_crc = crc16_next(calculated_crc, 8'h00);
        calculated_crc = crc16_next(calculated_crc, 8'h05);
        calculated_crc = crc16_next(calculated_crc, 8'h00);
        send_uart_byte(8'ha5);
        send_uart_byte(8'h5a);
        send_uart_byte(8'h03);
        send_uart_byte(8'h38);
        send_uart_byte(8'h02);
        send_uart_byte(8'h00);
        send_uart_byte(8'h05);
        send_uart_byte(8'h00);
        send_uart_byte(calculated_crc[7:0]);
        send_uart_byte(calculated_crc[15:8]);
        #1000;
        if (dut.configured_delay !== 5 ||
            dut.phase_delay_inst.active_delay !== 5) begin
            $display("FAIL: delay configuration was not applied");
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS: MSPM0 capture, CRC and phase-delay command");
        else
            $display("FAIL: %0d errors", errors);
        $finish;
    end

    initial begin
        #5000000;
        $display("FAIL: timeout after %0d frame bytes", frame_count);
        $finish;
    end
endmodule
