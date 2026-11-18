/*
 * Copyright (c) 2026 Siddharth Senthil Kumar, Sumedh Kallapur, Vibin DJ, Yatin Sangwan
 * SPDX-License-Identifier: Apache-2.0
 *
 * ss_readback: VIEW register and uo_out multiplexer.
 *   0 status {depth[3:0], lp_fault, underflow, overflow, fault}
 *   1 expected[7:0]      2 expected[15:8]
 *   3 fault_count        4 lost
 *   5 last_bad_pc[7:0]   6 last_bad_pc[15:8]
 *   7 ID byte 0x5A       8 skip_count       9-15: 0
 */

`default_nettype none

module ss_readback #(
    parameter ADDR_W = 16
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              set_view,
    input  wire [3:0]        view_in,
    input  wire [3:0]        depth,
    input  wire              lp_fault,
    input  wire              underflow,
    input  wire              overflow,
    input  wire              fault,
    input  wire [ADDR_W-1:0] expected,
    input  wire [7:0]        fault_count,
    input  wire [7:0]        lost,
    input  wire [ADDR_W-1:0] last_bad_pc,
    input  wire [7:0]        skip_count,
    output reg  [7:0]        out
);

  localparam [7:0] ID_BYTE = 8'h5A;

  reg [3:0] view;

  wire [15:0] exp16  = {{(16-ADDR_W){1'b0}}, expected};
  wire [15:0] lbpc16 = {{(16-ADDR_W){1'b0}}, last_bad_pc};

  always @(posedge clk) begin
    if (!rst_n)        view <= 4'd0;
    else if (set_view) view <= view_in;
  end

  always @(*) begin
    case (view)
      4'd0:    out = {depth, lp_fault, underflow, overflow, fault};
      4'd1:    out = exp16[7:0];
      4'd2:    out = exp16[15:8];
      4'd3:    out = fault_count;
      4'd4:    out = lost;
      4'd5:    out = lbpc16[7:0];
      4'd6:    out = lbpc16[15:8];
      4'd7:    out = ID_BYTE;
      4'd8:    out = skip_count;
      default: out = 8'd0;
    endcase
  end

endmodule
