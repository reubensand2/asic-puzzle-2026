`timescale 1ns / 1ps
`include "example_inputs.vh"

module puzzle_example_tb;
    reg clk;
    reg rst_n;
    reg enable;
    reg I;
    wire success;
    wire [7:0] O;

    reg [11:0] vectors [0:`PUZZLE_EXAMPLE_CYCLES - 1];
    reg expected_success;
    reg [7:0] expected_O;
    integer cycle;

    puzzle dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .I(I),
        .success(success),
        .O(O)
    );

    initial begin
        if ($test$plusargs("dump")) begin
            $dumpfile("artifacts/puzzle/puzzle_example_trace.vcd");
            $dumpvars(1, dut);
        end
    end

    initial begin
        $readmemb("artifacts/puzzle/example_inputs.mem", vectors);
        clk = 1'b0;

        for (cycle = 0; cycle < `PUZZLE_EXAMPLE_CYCLES; cycle = cycle + 1) begin
            {rst_n, enable, I, expected_success, expected_O} = vectors[cycle];
            #5 clk = 1'b1;
            #0.001;

            if (success !== expected_success || O !== expected_O) begin
                $display(
                    "FAIL: cycle=%0d inputs={rst_n:%b enable:%b I:%b} expected={success:%b O:%02x} observed={success:%b O:%02x}",
                    cycle,
                    rst_n,
                    enable,
                    I,
                    expected_success,
                    expected_O,
                    success,
                    O
                );
                $finish;
            end

            #4.999 clk = 1'b0;
        end

        $display("PASS: recovered puzzle matches all %0d example cycles", cycle);
        $finish;
    end
endmodule
