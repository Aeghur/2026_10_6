`timescale 1ns/1ps
module tb_uart_bridge;
    localparam integer CLKS_PER_BIT = 10;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg uart_rx = 1'b1;
    wire uart_tx;
    wire [31:0] phase_lag_word;
    wire [31:0] calibration_phase_word;
    wire [13:0] amplitude_code;
    wire output_enable;
    wire config_valid;
    integer errors = 0;
    integer response_count = 0;
    reg [7:0] response [0:23];

    wire [7:0] monitor_data;
    wire monitor_valid;
    wire monitor_error;

    always #10 clk = ~clk;

    dpll_uart_bridge #(.CLK_HZ(50_000_000), .BAUD(5_000_000)) dut (
        .clk(clk), .rst(rst), .uart_rx(uart_rx), .uart_tx(uart_tx),
        .phase_lag_word(phase_lag_word),
        .calibration_phase_word(calibration_phase_word),
        .amplitude_code(amplitude_code), .output_enable(output_enable),
        .config_valid(config_valid), .phase_increment(32'h12345678),
        .phase_error(-24'sd12345), .locked(1'b1),
        .signal_present(1'b1), .dac_clipped(1'b0), .adc_otr(1'b0),
        .dac_code(14'd8279)
    );

    dpll_uart_rx #(.CLK_HZ(50_000_000), .BAUD(5_000_000)) monitor (
        .clk(clk), .rst(rst), .rx(uart_tx), .data(monitor_data),
        .valid(monitor_valid), .framing_error(monitor_error)
    );

    always @(posedge clk)
        if (monitor_valid && response_count < 24) begin
            response[response_count] <= monitor_data;
            response_count <= response_count + 1;
        end

    function [15:0] crc16_next;
        input [15:0] crc_in;
        input [7:0] value;
        integer bit_number;
        reg [15:0] work;
        begin
            work = crc_in ^ {value,8'h00};
            for (bit_number=0; bit_number<8; bit_number=bit_number+1)
                work = work[15] ? {work[14:0],1'b0} ^ 16'h1021 :
                                  {work[14:0],1'b0};
            crc16_next = work;
        end
    endfunction

    task send_uart_byte;
        input [7:0] value;
        integer bit_number;
        begin
            uart_rx = 1'b0;
            repeat (CLKS_PER_BIT) @(posedge clk);
            for (bit_number=0; bit_number<8; bit_number=bit_number+1) begin
                uart_rx = value[bit_number];
                repeat (CLKS_PER_BIT) @(posedge clk);
            end
            uart_rx = 1'b1;
            repeat (CLKS_PER_BIT) @(posedge clk);
        end
    endtask

    task send_config;
        reg [15:0] crc;
        reg [7:0] payload [0:10];
        integer index;
        begin
            payload[0]=8'h00; payload[1]=8'h00; payload[2]=8'h00; payload[3]=8'h40;
            payload[4]=8'h67; payload[5]=8'h45; payload[6]=8'h23; payload[7]=8'h01;
            payload[8]=8'hb8; payload[9]=8'h0b; payload[10]=8'h01;
            crc=16'hffff;
            crc=crc16_next(crc,8'h10); crc=crc16_next(crc,8'h07);
            crc=crc16_next(crc,8'h0b); crc=crc16_next(crc,8'h00);
            for(index=0;index<11;index=index+1)
                crc=crc16_next(crc,payload[index]);
            send_uart_byte(8'ha5); send_uart_byte(8'h5a);
            send_uart_byte(8'h10); send_uart_byte(8'h07);
            send_uart_byte(8'h0b); send_uart_byte(8'h00);
            for(index=0;index<11;index=index+1)
                send_uart_byte(payload[index]);
            send_uart_byte(crc[7:0]); send_uart_byte(crc[15:8]);
        end
    endtask

    task send_status_request;
        reg [15:0] crc;
        begin
            crc=16'hffff;
            crc=crc16_next(crc,8'h01); crc=crc16_next(crc,8'h08);
            crc=crc16_next(crc,8'h00); crc=crc16_next(crc,8'h00);
            send_uart_byte(8'ha5); send_uart_byte(8'h5a);
            send_uart_byte(8'h01); send_uart_byte(8'h08);
            send_uart_byte(8'h00); send_uart_byte(8'h00);
            send_uart_byte(crc[7:0]); send_uart_byte(crc[15:8]);
        end
    endtask

    integer timeout;
    initial begin
        repeat (5) @(posedge clk);
        rst = 1'b0;
        send_config();
        repeat (20) @(posedge clk);
        if (phase_lag_word !== 32'h40000000 ||
            calibration_phase_word !== 32'h01234567 ||
            amplitude_code !== 14'd3000 || !output_enable) begin
            $display("FAIL: config lag=%h cal=%h amp=%0d enable=%b",
                phase_lag_word, calibration_phase_word,
                amplitude_code, output_enable);
            errors = errors + 1;
        end

        send_status_request();
        timeout = 20000;
        while (response_count < 24 && timeout > 0) begin
            @(posedge clk);
            timeout = timeout - 1;
        end
        if (response_count != 24 || response[0] !== 8'ha5 ||
            response[1] !== 8'h5a || response[2] !== 8'h90 ||
            response[3] !== 8'h08 || response[4] !== 8'd16 ||
            {response[9],response[8],response[7],response[6]} !== 32'h12345678) begin
            $display("FAIL: status response count=%0d", response_count);
            errors = errors + 1;
        end
        if (monitor_error) begin
            $display("FAIL: monitor framing error");
            errors = errors + 1;
        end
        if (errors == 0)
            $display("PASS: UART config and status protocol");
        else
            $display("FAIL: UART bridge errors=%0d", errors);
        $finish;
    end
endmodule

