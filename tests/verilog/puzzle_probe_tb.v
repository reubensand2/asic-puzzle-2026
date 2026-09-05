`timescale 1ns / 1ps

module puzzle_probe_tb;
    reg clk;
    reg rst_n;
    reg enable;
    reg I;
    wire success;
    wire [7:0] O;

    reg pixels [0:120];
    reg [8*256-1:0] input_file;
    reg [8*64-1:0] case_name;
    reg [7:0] clean_O;
    integer cycle;
    integer bit_index;

    puzzle dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .I(I),
        .success(success),
        .O(O)
    );

    task clock_cycle;
        begin
            #5 clk = 1'b1;
            #0.001;
            #4.999 clk = 1'b0;
        end
    endtask

    // Two output bits have a path through the one undriven extracted net.
    // Treat X/Z as zero when displaying ROM text; asserted bits remain exact.
    task print_output_byte;
        begin
            clean_O = 8'b0;
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
                if (O[bit_index] === 1'b1)
                    clean_O[bit_index] = 1'b1;
            if (clean_O != 8'b0)
                $write("%c", clean_O);
        end
    endtask

    initial begin
        if (!$value$plusargs("input=%s", input_file)) begin
            $display("Missing +input=<memory file>");
            $finish;
        end
        if (!$value$plusargs("name=%s", case_name))
            case_name = "unnamed";
        $readmemb(input_file, pixels);

        clk = 1'b0;
        rst_n = 1'b0;
        enable = 1'b0;
        I = 1'b0;
        repeat (3)
            clock_cycle;

        rst_n = 1'b1;
        clock_cycle;
        enable = 1'b1;
        for (cycle = 0; cycle < 121; cycle = cycle + 1) begin
            I = pixels[cycle];
            clock_cycle;
        end

        enable = 1'b0;
        I = 1'b0;
        #5 clk = 1'b1;
        #0.001;
        $write("%0s: success=%b, output=", case_name, success);
        print_output_byte;
        #4.999 clk = 1'b0;
        for (cycle = 0; cycle < 31; cycle = cycle + 1) begin
            #5 clk = 1'b1;
            #0.001;
            print_output_byte;
            #4.999 clk = 1'b0;
        end
        $display("");
        $finish;
    end
endmodule
