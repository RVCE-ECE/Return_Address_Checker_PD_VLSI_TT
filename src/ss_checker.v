/*
 * Copyright (c) 2026 Siddharth Senthil Kumar, Sumedh Kallapur, Vibin DJ, Yatin Sangwan
 * SPDX-License-Identifier: Apache-2.0
 *
 * ss_checker: pending-return compare, landing-pad check, sticky flags,
 * saturating counters and last_bad_pc.
 *
 * For each TRACE packet:
 *   1. Resolve the previous packet's checks (skipped if TRAP=1).
 *   2. Record this packet's pop (expected/pending) and indirect jump.
 */

`default_nettype none

module ss_checker #(
    parameter ADDR_W = 16,
    parameter LP_EN  = 1,   // build the forward-edge (landing pad) check
    parameter LBPC   = 1    // build the last_bad_pc register
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              trace_valid,
    input  wire              clr_stack,   // RESET_STACK
    input  wire              clr_flags,   // CLEAR_FLAGS
    input  wire              trap,
    input  wire [ADDR_W-1:0] pc,
    // decode
    input  wire              pop,
    input  wire              indirect_nonret,
    input  wire              is_lpad,
    // stack
    input  wire              pop_entry,
    input  wire              pop_under,
    input  wire [ADDR_W-1:0] top,
    input  wire              ovf_evt,
    input  wire              desync,
    // config
    input  wire              unf_fault_en,
    input  wire              lp_en,
    // state out
    output reg  [ADDR_W-1:0] expected,
    output reg               fault,
    output reg               overflow,
    output reg               underflow,
    output wire              lp_fault,
    output reg  [7:0]        fault_count,
    output reg  [7:0]        skip_count,
    output wire [ADDR_W-1:0] last_bad_pc
);

  reg pending;

  wire lp_pending;
  wire skip_evt     = trap && (pending || lp_pending);
  wire mismatch     = !trap && pending && (pc != expected);
  wire under_evt    = pop && pop_under;
  wire under_fault  = under_evt && unf_fault_en && !desync;
  wire [1:0] nfault = {1'b0, mismatch} + {1'b0, under_fault};

  wire [8:0] fc_sum = {1'b0, fault_count} + {7'd0, nfault};

  always @(posedge clk) begin
    if (!rst_n) begin
      pending     <= 1'b0;
      expected    <= {ADDR_W{1'b0}};
      fault       <= 1'b0;
      overflow    <= 1'b0;
      underflow   <= 1'b0;
      fault_count <= 8'd0;
      skip_count  <= 8'd0;
    end else begin
      if (clr_stack) pending <= 1'b0;
      if (clr_flags) begin
        fault       <= 1'b0;
        overflow    <= 1'b0;
        underflow   <= 1'b0;
        fault_count <= 8'd0;
        skip_count  <= 8'd0;
      end
      if (trace_valid) begin
        if (skip_evt && skip_count != 8'hFF) skip_count <= skip_count + 8'd1;
        if (nfault != 2'd0) begin
          fault       <= 1'b1;
          fault_count <= fc_sum[8] ? 8'hFF : fc_sum[7:0];
        end
        if (under_evt) underflow <= 1'b1;
        if (ovf_evt)   overflow  <= 1'b1;
        pending <= pop && pop_entry && !desync;
        if (pop && pop_entry) expected <= top;
      end
    end
  end

  generate
    if (LBPC) begin : g_lbpc
      reg [ADDR_W-1:0] lbpc;
      always @(posedge clk) begin
        if (!rst_n || clr_flags) lbpc <= {ADDR_W{1'b0}};
        else if (trace_valid && mismatch) lbpc <= pc;
      end
      assign last_bad_pc = lbpc;
    end else begin : g_no_lbpc
      assign last_bad_pc = {ADDR_W{1'b0}};
    end

    if (LP_EN) begin : g_lp
      reg lp_pend, lp_flt;
      always @(posedge clk) begin
        if (!rst_n) begin
          lp_pend <= 1'b0;
          lp_flt  <= 1'b0;
        end else begin
          if (clr_stack) lp_pend <= 1'b0;
          if (clr_flags) lp_flt  <= 1'b0;
          if (trace_valid) begin
            if (!trap && lp_pend && !is_lpad) lp_flt <= 1'b1;
            lp_pend <= indirect_nonret && lp_en;
          end
        end
      end
      assign lp_pending = lp_pend;
      assign lp_fault   = lp_flt;
    end else begin : g_no_lp
      assign lp_pending = 1'b0;
      assign lp_fault   = 1'b0;
    end
  endgenerate

endmodule
