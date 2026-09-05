`timescale 1ns / 1ps

module warmup_tb;
    reg clk;
    reg rst_n;
    reg en;
    reg A;
    reg B;
    wire S;

    integer a_value;
    integer b_value;
    integer checked;
    reg expected;

    adder_demo dut (
        .clk(clk),
        .rst_n(rst_n),
        .en(en),
        .A(A),
        .B(B),
        .S(S)
    );

    always #1 clk = ~clk;

    // The shift registers use {old[6:0], serial_in}, so each operand is sent
    // most-significant bit first. Every eight clocks completely replaces the
    // previous operands, allowing one reset before the exhaustive sweep.
    task shift_operands;
        input [7:0] a_operand;
        input [7:0] b_operand;
        integer bit_index;
        begin
            for (bit_index = 7; bit_index >= 0; bit_index = bit_index - 1) begin
                @(negedge clk);
                A = a_operand[bit_index];
                B = b_operand[bit_index];
            end
            // Capture the final (least-significant) bits before checking S.
            @(posedge clk);
        end
    endtask

    initial begin
        clk = 1'b0;
        rst_n = 1'b1;
        en = 1'b0;
        A = 1'b0;
        B = 1'b0;
        checked = 0;

        // Create a real falling edge so both RTL and UDP flip-flops observe
        // the asynchronous active-low reset.
        #0.25 rst_n = 1'b0;
        #2 rst_n = 1'b1;
        en = 1'b1;

        for (a_value = 0; a_value < 256; a_value = a_value + 1) begin
            for (b_value = 0; b_value < 256; b_value = b_value + 1) begin
                shift_operands(a_value[7:0], b_value[7:0]);
                expected = ((a_value + b_value) == 496);
                #0.01;

                if (S !== expected) begin
                    $display(
                        "FAIL: A=%0d B=%0d expected S=%b, observed S=%b",
                        a_value,
                        b_value,
                        expected,
                        S
                    );
                    $finish;
                end
                checked = checked + 1;
            end
        end

        $display("PASS: checked all %0d operand pairs", checked);
        $finish;
    end
endmodule
