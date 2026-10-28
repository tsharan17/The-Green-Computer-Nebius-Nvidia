// A small convolution accelerator: top level, a MAC array and an AXI-stream buffer.
`timescale 1ns / 1ps

module conv_top #(
    parameter DATA_W = 8,
    parameter LANES = 16
) (
    input  wire                       clk,
    input  wire                       rst_n,
    input  wire [DATA_W*LANES-1:0]    s_axis_tdata,
    input  wire                       s_axis_tvalid,
    output wire                       s_axis_tready,
    output wire [31:0]                m_axis_tdata,
    output wire                       m_axis_tvalid
);
    wire [DATA_W*LANES-1:0] buffered;
    /* the stream buffer feeds the MAC array */
    stream_buf #(.W(DATA_W*LANES)) u_buf (
        .clk(clk), .rst_n(rst_n), .in(s_axis_tdata), .out(buffered)
    );
    mac_array #(.LANES(LANES)) u_mac (.clk(clk), .a(buffered), .acc(m_axis_tdata));
    xpm_fifo_sync u_fifo (.wr_clk(clk));
    assign s_axis_tready = 1'b1;
    assign m_axis_tvalid = s_axis_tvalid;
endmodule

module mac_array (clk, a, acc);
    parameter LANES = 16;
    input clk;
    input [8*LANES-1:0] a;
    output reg [31:0] acc;
    always @(posedge clk) acc <= acc + a[7:0];
endmodule

module stream_buf #(parameter W = 128) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire [W-1:0] in,
    output reg  [W-1:0] out
);
    always @(posedge clk) if (!rst_n) out <= 0; else out <= in;
endmodule
