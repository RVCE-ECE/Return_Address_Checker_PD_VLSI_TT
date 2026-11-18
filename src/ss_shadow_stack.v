/*
 * Copyright (c) 2026 Siddharth Senthil Kumar, Sumedh Kallapur, Vibin DJ, Yatin Sangwan
 * SPDX-License-Identifier: Apache-2.0
 *
 * ss_shadow_stack: circular buffer of return addresses with a `lost` counter.
 *
 * Pop classification (combinational, from current state):
 *   WRAP     (ovf_sat=0): entry if depth>0; else skip if lost>0; else under
 *   SATURATE (ovf_sat=1): skip if lost>0; else entry if depth>0; else under
 * Push:
 *   depth<DEPTH -> store, depth++
 *   full        -> ovf_evt; lost++ (or set desync if lost==255);
 *                  WRAP overwrites the oldest entry, SATURATE drops the push
 * Pop+push with an entry replaces the top entry in place.
 * See docs/design.md and model/shadow_model.py.
 */

`default_nettype none

module ss_shadow_stack #(
    parameter DEPTH  = 8,
    parameter ADDR_W = 16
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              clr,        // RESET_STACK
    input  wire              op_valid,   // TRACE packet is being processed
    input  wire              push,
    input  wire              pop,
    input  wire [ADDR_W-1:0] ra,
    input  wire              ovf_sat,
    output wire              pop_entry,
    output wire              pop_skip,
    output wire              pop_under,
    output wire [ADDR_W-1:0] top,
    output wire              ovf_evt,
    output reg  [3:0]        depth,
    output reg  [7:0]        lost,
    output reg               desync
);

  localparam PW = (DEPTH > 1) ? $clog2(DEPTH) : 1;
  localparam [3:0] DEPTH4 = DEPTH;
  localparam integer LASTI = DEPTH - 1;
  localparam [PW-1:0] LAST = LASTI[PW-1:0];

  reg [ADDR_W-1:0] mem [0:DEPTH-1];
  reg [PW-1:0]     tp;

  wire empty     = (depth == 4'd0);
  wire full      = (depth == DEPTH4);
  wire lost_zero = (lost == 8'd0);

  assign pop_entry = ovf_sat ? (lost_zero && !empty) : !empty;
  assign pop_skip  = ovf_sat ? !lost_zero : (empty && !lost_zero);
  assign pop_under = empty && lost_zero;
  assign top       = mem[tp];

  wire [PW-1:0] tp_next = (tp == LAST) ? {PW{1'b0}} : tp + 1'b1;
  wire [PW-1:0] tp_prev = (tp == {PW{1'b0}}) ? LAST : tp - 1'b1;

  wire replace  = pop && pop_entry && push;
  wire pop_only = pop && pop_entry && !push;
  wire do_push  = push && !(pop && pop_entry);   // a real push after any non-entry pop
  wire lost_dec = pop && pop_skip;

  assign ovf_evt = op_valid && do_push && full;

  // lost after the pop half of the operation
  wire [7:0] lost_mid = lost_dec ? lost - 8'd1 : lost;

  always @(posedge clk) begin
    if (!rst_n || clr) begin
      tp     <= {PW{1'b0}};
      depth  <= 4'd0;
      lost   <= 8'd0;
      desync <= 1'b0;
    end else if (op_valid) begin
      lost <= lost_mid;
      if (replace) begin
        mem[tp] <= ra;
      end else if (pop_only) begin
        depth <= depth - 4'd1;
        if (depth != 4'd1) tp <= tp_prev;
      end
      if (do_push) begin
        if (!full) begin
          if (!empty) tp <= tp_next;
          mem[empty ? tp : tp_next] <= ra;
          depth <= depth + 4'd1;
        end else begin
          if (lost_mid == 8'hFF) desync <= 1'b1;
          else                   lost   <= lost_mid + 8'd1;
          if (!ovf_sat) begin
            tp          <= tp_next;
            mem[tp_next] <= ra;
          end
        end
      end
    end
  end

endmodule
