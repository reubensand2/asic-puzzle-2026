`timescale 1ns / 1ps

module puzzle_solution_tb;
    reg clk;
    reg rst_n;
    reg enable;
    reg I;
    wire success;
    wire [7:0] O;

    reg pixels [0:120];
    integer cycle;

    puzzle dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .I(I),
        .success(success),
        .O(O)
    );

    task rising_edge;
        begin
            #5 clk = 1'b1;
            #0.001;
        end
    endtask

    task falling_edge;
        begin
            #4.999 clk = 1'b0;
        end
    endtask

    initial begin
        $readmemb("artifacts/puzzle/solution.mem", pixels);
        clk = 1'b0;
        rst_n = 1'b0;
        enable = 1'b0;
        I = 1'b0;

        repeat (3) begin
            rising_edge;
            falling_edge;
        end

        rst_n = 1'b1;
        rising_edge;
        falling_edge;

        enable = 1'b1;
        for (cycle = 0; cycle < 121; cycle = cycle + 1) begin
            I = pixels[cycle];
            rising_edge;
            falling_edge;
        end

        enable = 1'b0;
        I = 1'b0;
        rising_edge;
        if (success !== 1'b1) begin
            $display("FAIL: recovered checker did not assert success");
            $finish;
        end
        $write("Chip output: ");
        if (O !== 8'b0)
            $write("%c", O);
        falling_edge;

        for (cycle = 0; cycle < 63; cycle = cycle + 1) begin
            rising_edge;
            if (O !== 8'b0)
                $write("%c", O);
            falling_edge;
        end
        $display("");
        $display("PASS: accepted image asserted success");
        $finish;
    end
endmodule
