/*
 * Copyright (c) 2026 Siddharth Senthil Kumar, Sumedh Kallapur, Vibin DJ, Yatin Sangwan
 * SPDX-License-Identifier: Apache-2.0
 *
 * Zicfiss-inspired external shadow-stack monitor (NOT Zicfiss compliant).
 * A host streams retired RV32IMC instructions in byte-serial packets; the
 * tile keeps a shadow stack of return addresses and flags mismatches.
 *
 * Pins:
 *   ui_in[7:0]  data byte
 *   uio[0] in   STROBE      uio[1] in  SOF      uio[2] in reserved
 *   uio[3] out  fault       uio[4] out overflow uio[5] out underflow
 *   uio[6] out  lp_fault    uio[7] out busy
 *   uo_out      readback byte selected by VIEW
 */

`default_nettype none

module tt_um_sidisnotacoder_shadowstack #(
    parameter DEPTH  = 6,    // 8 does not fit a 1x1 tile
    parameter ADDR_W = 16,
    parameter LP_EN  = 0,    // forward-edge check: needs a 1x2 tile
    parameter LBPC   = 0     // last_bad_pc register: needs a 1x2 tile
) (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

  localparam CMD_TRACE  = 4'h1;
  localparam CMD_RESET  = 4'h2;
  localparam CMD_CLEAR  = 4'h3;
  localparam CMD_CONFIG = 4'h4;
  localparam CMD_VIEW   = 4'h5;

  // ---------------------------------------------------------------- receiver
  wire        pkt_valid;
  wire [3:0]  cmd;
  wire        trap;
  wire [39:0] payload;
  wire        busy;

  ss_host_rx u_rx (
      .clk(clk), .rst_n(rst_n),
      .data(ui_in), .strobe_in(uio_in[0]), .sof_in(uio_in[1]),
      .pkt_valid(pkt_valid), .cmd(cmd), .trap(trap), .payload(payload), .busy(busy)
  );

  wire trace_valid = pkt_valid && (cmd == CMD_TRACE);
  wire clr_stack   = pkt_valid && (cmd == CMD_RESET);
  wire clr_flags   = pkt_valid && (cmd == CMD_CLEAR);
  wire set_config  = pkt_valid && (cmd == CMD_CONFIG);
  wire set_view    = pkt_valid && (cmd == CMD_VIEW);

  wire [15:0] pc16   = payload[15:0];
  wire [23:0] inst   = payload[39:16];
  wire [7:0]  arg    = payload[39:32];   // single-byte payload

  // ------------------------------------------------------------------ config
  reg cfg_ovf_sat, cfg_unf_fault, cfg_lp_en;
  always @(posedge clk) begin
    if (!rst_n) begin
      cfg_ovf_sat   <= 1'b0;
      cfg_unf_fault <= 1'b0;
      cfg_lp_en     <= 1'b0;
    end else if (set_config) begin
      cfg_ovf_sat   <= arg[0];
      cfg_unf_fault <= arg[1];
      cfg_lp_en     <= arg[2] & (LP_EN != 0);
    end
  end

  // ------------------------------------------------------------------ decode
  wire dec_push, dec_pop, dec_is_c, dec_ind, dec_lpad;
  ss_cf_decode u_dec (
      .inst(inst), .push(dec_push), .pop(dec_pop), .is_c(dec_is_c),
      .indirect_nonret(dec_ind), .is_lpad(dec_lpad)
  );

  wire [ADDR_W-1:0] pc_a = pc16[ADDR_W-1:0];
  wire [ADDR_W-1:0] ra   = pc_a + (dec_is_c ? 2 : 4);

  // ------------------------------------------------------------------- stack
  wire              pop_entry, pop_skip, pop_under, ovf_evt, desync;
  wire [ADDR_W-1:0] top;
  wire [3:0]        depth;
  wire [7:0]        lost;

  ss_shadow_stack #(.DEPTH(DEPTH), .ADDR_W(ADDR_W)) u_stack (
      .clk(clk), .rst_n(rst_n), .clr(clr_stack), .op_valid(trace_valid),
      .push(dec_push), .pop(dec_pop), .ra(ra), .ovf_sat(cfg_ovf_sat),
      .pop_entry(pop_entry), .pop_skip(pop_skip), .pop_under(pop_under),
      .top(top), .ovf_evt(ovf_evt), .depth(depth), .lost(lost), .desync(desync)
  );

  // ----------------------------------------------------------------- checker
  wire [ADDR_W-1:0] expected, last_bad_pc;
  wire              fault, overflow, underflow, lp_fault;
  wire [7:0]        fault_count, skip_count;

  ss_checker #(.ADDR_W(ADDR_W), .LP_EN(LP_EN), .LBPC(LBPC)) u_chk (
      .clk(clk), .rst_n(rst_n), .trace_valid(trace_valid),
      .clr_stack(clr_stack), .clr_flags(clr_flags), .trap(trap), .pc(pc_a),
      .pop(dec_pop), .indirect_nonret(dec_ind), .is_lpad(dec_lpad),
      .pop_entry(pop_entry), .pop_under(pop_under), .top(top),
      .ovf_evt(ovf_evt), .desync(desync),
      .unf_fault_en(cfg_unf_fault), .lp_en(cfg_lp_en),
      .expected(expected), .fault(fault), .overflow(overflow),
      .underflow(underflow), .lp_fault(lp_fault),
      .fault_count(fault_count), .skip_count(skip_count), .last_bad_pc(last_bad_pc)
  );

  // ---------------------------------------------------------------- readback
  ss_readback #(.ADDR_W(ADDR_W)) u_rb (
      .clk(clk), .rst_n(rst_n), .set_view(set_view), .view_in(arg[3:0]),
      .depth(depth), .lp_fault(lp_fault), .underflow(underflow),
      .overflow(overflow), .fault(fault), .expected(expected),
      .fault_count(fault_count), .lost(lost), .last_bad_pc(last_bad_pc),
      .skip_count(skip_count), .out(uo_out)
  );

  assign uio_out = {busy, lp_fault, underflow, overflow, fault, 3'b000};
  assign uio_oe  = 8'b1111_1000;

  wire _unused = &{ena, uio_in[7:2], arg[7:4], pop_skip, 1'b0};

endmodule
