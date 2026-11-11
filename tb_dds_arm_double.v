`timescale 1ns/1ps

module tb_dds_arm_double;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg config_valid = 1'b0;
    reg arm_double = 1'b0;
    reg timeout_arm = 1'b0;
    reg sample_valid = 1'b0;
    reg waveform_saw = 1'b0;
    wire output_valid;
    wire [11:0] output_sample;
    wire locked;
    wire signal_present;
    wire double_busy;
    wire double_committed;
    wire double_output_applied;
    wire double_error;
    wire [1:0] double_error_code;
    wire [31:0] double_sample_counter;
    wire [31:0] double_old_step;
    wire [31:0] double_new_step;
    wire [31:0] double_commit_phase;
    wire timeout_error;
    wire [1:0] timeout_error_code;
    integer errors = 0;
    integer samples_after_commit = 0;
    reg commit_seen = 1'b0;

    localparam [31:0] OLD_FTW = 32'h40000000;
    localparam [31:0] NEW_FTW = 32'h80000000;
    localparam [31:0] OLD_Q32_INCREMENT = OLD_FTW >> 6;
    wire [31:0] old_next_commit_phase =
        double_commit_phase + OLD_Q32_INCREMENT;

    always #10 clk = ~clk;
    always @(posedge clk) begin
        if (rst)
            sample_valid <= 1'b0;
        else
            sample_valid <= ~sample_valid;
        if (commit_seen && sample_valid)
            samples_after_commit <= samples_after_commit + 1;
    end

    zero_crossing_dds #(
        .MAX_FREE_RUN_FTW(32'h90000000),
        .DOUBLE_TIMEOUT_SAMPLES(2000)
    ) dut (
        .clk(clk), .rst(rst), .enable(1'b1), .free_run(1'b1),
        .config_valid(config_valid), .config_phase_step(OLD_FTW),
        .config_phase_lag(32'h40000000),
        .config_decay_start(16'he665),
        .config_decay_scale_q16(32'd327650),
        .frequency_x2(1'b0), .waveform_saw(waveform_saw),
        .saw_reverse(1'b0), .waveform_decay(1'b0),
        .arm_double(arm_double), .amplitude_scale_code(2'd0),
        .reference_edge(1'b0), .sample_valid(sample_valid),
        .sample_data(12'd2104), .output_valid(output_valid),
        .output_sample(output_sample), .locked(locked),
        .signal_present(signal_present), .double_busy(double_busy),
        .double_committed(double_committed),
        .double_output_applied(double_output_applied),
        .double_error(double_error), .double_error_code(double_error_code),
        .double_sample_counter(double_sample_counter),
        .double_old_step(double_old_step), .double_new_step(double_new_step),
        .double_commit_phase(double_commit_phase)
    );

    zero_crossing_dds #(
        .MAX_FREE_RUN_FTW(32'h90000000),
        .DOUBLE_TIMEOUT_SAMPLES(4)
    ) timeout_dut (
        .clk(clk), .rst(rst), .enable(1'b1), .free_run(1'b1),
        .config_valid(config_valid), .config_phase_step(OLD_FTW),
        .config_phase_lag(32'd0), .config_decay_start(16'he665),
        .config_decay_scale_q16(32'd327650), .frequency_x2(1'b0),
        .waveform_saw(1'b0), .saw_reverse(1'b0), .waveform_decay(1'b0),
        .arm_double(timeout_arm), .amplitude_scale_code(2'd0),
        .reference_edge(1'b0), .sample_valid(sample_valid),
        .sample_data(12'd2104), .output_valid(), .output_sample(),
        .locked(), .signal_present(), .double_busy(), .double_committed(),
        .double_output_applied(), .double_error(timeout_error),
        .double_error_code(timeout_error_code), .double_sample_counter(),
        .double_old_step(), .double_new_step(), .double_commit_phase()
    );

    initial begin
        repeat (4) @(posedge clk);
        rst = 1'b0;
        @(negedge clk);
        config_valid = 1'b1;
        @(negedge clk);
        config_valid = 1'b0;

        // A bounded wait must report timeout; it must never silently perform
        // an immediate frequency change when no selected crossing is reached.
        timeout_arm = 1'b1;
        @(negedge clk);
        timeout_arm = 1'b0;
        wait (timeout_error);
        #1;
        if (timeout_error_code !== 2'd3) begin
            $display("FAIL: timeout path returned %0d", timeout_error_code);
            errors = errors + 1;
        end

        // Wait until the commanded +90-degree lag has fully slewed.  Arming
        // before this point must not select a crossing.
        wait (dut.applied_phase_lag == dut.phase_lag && locked);
        repeat (17) @(posedge sample_valid);
        @(negedge clk);
        arm_double = 1'b1;
        @(negedge clk);
        arm_double = 1'b0;
        if (!double_busy) begin
            $display("FAIL: arm did not enter pending state");
            errors = errors + 1;
        end

        wait (double_committed);
        #1;
        commit_seen = 1'b1;
        if (double_old_step !== OLD_FTW || double_new_step !== NEW_FTW) begin
            $display("FAIL: FTW mapping old=%08x new=%08x",
                     double_old_step, double_new_step);
            errors = errors + 1;
        end
        if (double_commit_phase[31] !== 1'b0 ||
            old_next_commit_phase[31] !== 1'b1) begin
            $display("FAIL: commit was not at an old-frequency falling crossing: %08x",
                     double_commit_phase);
            errors = errors + 1;
        end
        if (dut.carrier_phase == 0 || dut.applied_phase_lag != dut.phase_lag) begin
            $display("FAIL: carrier reset or phase lag unsettled at commit");
            errors = errors + 1;
        end

        wait (double_output_applied);
        #1;
        if (samples_after_commit < 2) begin
            $display("FAIL: applied tag preceded first new-frequency sample");
            errors = errors + 1;
        end

        // A second doubling would exceed the configured output limit and
        // must be rejected explicitly rather than silently wrapping.
        @(negedge clk);
        arm_double = 1'b1;
        @(negedge clk);
        arm_double = 1'b0;
        wait (double_error);
        #1;
        if (double_error_code !== 2'd2) begin
            $display("FAIL: invalid second double error=%0d", double_error_code);
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS: atomic falling-zero Q38 doubling and applied tag");
        else
            $display("FAIL: %0d errors", errors);
        $finish;
    end

    initial begin
        #500000;
        $display("FAIL: timeout");
        $finish;
    end
endmodule
