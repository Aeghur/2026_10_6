`timescale 1ns/1ps
module tb_dds_free_run;
    reg clk=0, rst=1, config_valid=0;
    reg [31:0] word=0, lag=0;
    wire valid, locked, present;
    wire [11:0] sample;
    reg [37:0] before_phase, expected;
    reg [31:0] active_word=0;
    integer i,j;
    reg [31:0] words[0:10];
    always #10 clk=~clk;
    zero_crossing_dds dut(
        .clk(clk), .rst(rst), .enable(1'b1), .free_run(1'b1),
        .config_valid(config_valid), .config_phase_step(word),
        .config_phase_lag(lag), .frequency_x2(1'b0), .waveform_saw(1'b1),
        .amplitude_scale_code(2'd2), .reference_edge(1'b0),
        .sample_valid(1'b1), .sample_data(12'd2104),
        .output_valid(valid), .output_sample(sample), .locked(locked),
        .signal_present(present));
    task tick;
        begin
            before_phase={dut.carrier_phase,dut.free_run_fraction};
            expected=before_phase+active_word;
            @(posedge clk); #1;
            if ({dut.carrier_phase,dut.free_run_fraction} !== expected)
                $fatal(1,"phase discontinuity on frequency/lag update");
            @(negedge clk);
        end
    endtask
    initial begin
        // Rounded Q38 words, including all seven probe targets.
        words[0]=32'd2748779;
        words[1]=32'd2991870;
        words[2]=32'd5983740;
        words[3]=32'd12943803;
        words[4]=32'd46957580;
        words[5]=32'd59837369;
        words[6]=32'd95195816;
        words[7]=32'd143445739;
        words[8]=32'd2199023256;
        words[9]=32'd2199078231;
        words[10]=32'd2308974418;
        repeat(3) @(negedge clk);
        rst=0;
        for(i=0;i<11;i=i+1) begin
            word=words[i]; lag=i*32'h10000000; config_valid=1;
            tick; active_word=word; config_valid=0;
            for(j=0;j<2000;j=j+1) tick;
        end
        // Check the registered ramp at start, midpoint, peak and wrap.
        force dut.output_phase=32'h00000000;
        @(posedge clk); #1;
        if(dut.saw_sample_pipe !== -16'sd32768) $fatal(1,"saw start");
        force dut.output_phase=32'h80000000;
        @(posedge clk); #1;
        if(dut.saw_sample_pipe !== 16'sd0) $fatal(1,"saw midpoint");
        force dut.output_phase=32'hffffffff;
        @(posedge clk); #1;
        if(dut.saw_sample_pipe !== 16'sd32767) $fatal(1,"saw peak");
        force dut.output_phase=32'h00000000;
        @(posedge clk); #1;
        if(dut.saw_sample_pipe !== -16'sd32768) $fatal(1,"saw instant wrap");
        release dut.output_phase;
        $display("PASS: Q38 fractional probes, continuous updates, rising saw with instant wrap");
        $finish;
    end
endmodule
