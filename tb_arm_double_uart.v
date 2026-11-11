`timescale 1ns/1ps

module tb_arm_double_uart;
    reg clk = 1'b0;
    reg [11:0] adc_data = 12'd2104;
    reg uart_rx = 1'b1;
    wire adc_clk;
    wire dac_clk;
    wire [13:0] dac_data;
    wire [7:0] digitron_out;
    wire [5:0] digitron_cs_n;
    wire uart_tx;
    reg [7:0] response [0:24];
    reg [15:0] crc;
    reg [15:0] received_crc;
    integer i;
    integer errors = 0;
    reg dac_applied_seen = 1'b0;

    localparam [31:0] OLD_FTW = 32'h40000000;
    localparam [31:0] NEW_FTW = 32'h80000000;
    localparam [31:0] OLD_Q32_INCREMENT = OLD_FTW >> 6;
    wire [31:0] ack_commit_phase = {
        response[22], response[21], response[20], response[19]};
    wire [31:0] ack_next_old_phase =
        ack_commit_phase + OLD_Q32_INCREMENT;

    always #10 clk = ~clk;
    always @(posedge clk)
        if (dut.double_dac_applied)
            dac_applied_seen <= 1'b1;

    function [15:0] crc16_next;
        input [15:0] crc_in;
        input [7:0] value;
        integer n;
        reg [15:0] work;
        begin
            work = crc_in ^ {value, 8'h00};
            for (n = 0; n < 8; n = n + 1)
                work = work[15] ? ({work[14:0], 1'b0} ^ 16'h1021) :
                                  {work[14:0], 1'b0};
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
            #250;
        end
    endtask

    task send_set_sine_1x;
        integer index;
        reg [7:0] bytes [0:14];
        begin
            bytes[0] = 8'h04;
            bytes[1] = 8'h40;
            bytes[2] = 8'h09;
            bytes[3] = 8'h00;
            bytes[4] = 8'h11; // DDS + free run, sine, 1x, amplitude 8/8
            bytes[5] = OLD_FTW[7:0];
            bytes[6] = OLD_FTW[15:8];
            bytes[7] = OLD_FTW[23:16];
            bytes[8] = OLD_FTW[31:24];
            bytes[9] = 8'h00;
            bytes[10] = 8'h00;
            bytes[11] = 8'h00;
            bytes[12] = 8'h00;
            crc = 16'hffff;
            for (index = 0; index <= 12; index = index + 1)
                crc = crc16_next(crc, bytes[index]);
            send_uart_byte(8'ha5);
            send_uart_byte(8'h5a);
            for (index = 0; index <= 12; index = index + 1)
                send_uart_byte(bytes[index]);
            send_uart_byte(crc[7:0]);
            send_uart_byte(crc[15:8]);
        end
    endtask

    task send_arm_double;
        input [7:0] seq_value;
        begin
            crc = 16'hffff;
            crc = crc16_next(crc, 8'h05);
            crc = crc16_next(crc, seq_value);
            crc = crc16_next(crc, 8'h00);
            crc = crc16_next(crc, 8'h00);
            send_uart_byte(8'ha5);
            send_uart_byte(8'h5a);
            send_uart_byte(8'h05);
            send_uart_byte(seq_value);
            send_uart_byte(8'h00);
            send_uart_byte(8'h00);
            send_uart_byte(crc[7:0]);
            send_uart_byte(crc[15:8]);
        end
    endtask

    ads805 #(
        .CAPTURE_SAMPLES(16),
        .DOUBLE_TIMEOUT_SAMPLES(5000)
    ) dut (
        .clk_50m(clk), .adc_data(adc_data), .adc_otr(1'b0),
        .comparator_in(1'b0), .adc_clk(adc_clk), .dac_clk(dac_clk),
        .dac_data(dac_data), .digitron_out(digitron_out),
        .digitron_cs_n(digitron_cs_n), .uart_tx(uart_tx), .uart_rx(uart_rx)
    );

    initial begin
        #15000;
        fork
            send_set_sine_1x();
            begin
                for (i = 0; i < 8; i = i + 1)
                    receive_uart_byte(response[i]);
            end
        join
        if (response[2] !== 8'h84 || response[3] !== 8'h40) begin
            $display("FAIL: initial SET_OUTPUT ACK");
            errors = errors + 1;
        end

        fork
            send_arm_double(8'h41);
            begin
                for (i = 0; i < 25; i = i + 1)
                    receive_uart_byte(response[i]);
            end
        join

        crc = 16'hffff;
        for (i = 2; i < 23; i = i + 1)
            crc = crc16_next(crc, response[i]);
        received_crc = {response[24], response[23]};
        if ({response[1], response[0]} !== 16'h5aa5 ||
            response[2] !== 8'h85 || response[3] !== 8'h41 ||
            {response[5], response[4]} !== 16'd17 ||
            response[6] !== 8'd0 || received_crc !== crc) begin
            $display("FAIL: DOUBLE_ACK framing/status/CRC");
            errors = errors + 1;
        end
        if ({response[14], response[13], response[12], response[11]} !== OLD_FTW ||
            {response[18], response[17], response[16], response[15]} !== NEW_FTW) begin
            $display("FAIL: DOUBLE_ACK FTWs");
            errors = errors + 1;
        end
        if (ack_commit_phase[31] !== 1'b0 ||
            ack_next_old_phase[31] !== 1'b1) begin
            $display("FAIL: commit phase was not a falling crossing");
            errors = errors + 1;
        end
        if (!dac_applied_seen || dut.configured_phase_step !== NEW_FTW ||
            dut.output_frequency_x2 !== 1'b1) begin
            $display("FAIL: ACK preceded DAC apply or state was not updated");
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS: UART arm-double commits at falling zero and ACKs after DAC");
        else
            $display("FAIL: %0d errors", errors);
        $finish;
    end

    initial begin
        #3000000;
        $display("FAIL: timeout");
        $finish;
    end
endmodule
