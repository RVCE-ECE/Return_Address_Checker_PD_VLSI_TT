/*
 * Copyright (c) 2026 Siddharth Senthil Kumar, Sumedh Kallapur, Vibin DJ, Yatin Sangwan
 * SPDX-License-Identifier: Apache-2.0
 *
 * ss_host_rx: byte-serial packet receiver.
 *  - STROBE and SOF go through 2-flop synchronizers; a byte is taken on the
 *    rising edge of the synchronized STROBE. The host keeps DATA and SOF
 *    stable from before STROBE rises until after it falls.
 *  - A byte with SOF=1 is always a header and restarts parsing.
 *  - Bytes without SOF while idle are discarded (resynchronization).
 *  - pkt_valid pulses for one clock once a full packet has arrived.
 *    Payload bytes shift in from the top, so for a 5-byte TRACE
 *    payload[7:0] is the first byte; a 1-byte payload is in payload[39:32].
 */

`default_nettype none

module ss_host_rx (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  data,
    input  wire        strobe_in,
    input  wire        sof_in,
    output reg         pkt_valid,
    output reg  [3:0]  cmd,
    output reg         trap,
    output reg  [39:0] payload,
    output wire        busy
);

  localparam CMD_TRACE  = 4'h1;
  localparam CMD_CONFIG = 4'h4;
  localparam CMD_VIEW   = 4'h5;

  reg [2:0] stb_sync;   // [0],[1] synchronizer, [2] previous value for edge detect
  reg [1:0] sof_sync;
  reg [2:0] remaining;  // payload bytes still expected

  wire byte_stb = stb_sync[1] & ~stb_sync[2];
  wire sof      = sof_sync[1];

  assign busy = (remaining != 3'd0);

  reg [2:0] hdr_len;
  always @(*) begin
    case (data[7:4])
      CMD_TRACE:  hdr_len = 3'd5;
      CMD_CONFIG: hdr_len = 3'd1;
      CMD_VIEW:   hdr_len = 3'd1;
      default:    hdr_len = 3'd0;
    endcase
  end

  wire hdr_known = (data[7:4] >= CMD_TRACE) && (data[7:4] <= CMD_VIEW);

  always @(posedge clk) begin
    if (!rst_n) begin
      stb_sync  <= 3'b000;
      sof_sync  <= 2'b00;
      remaining <= 3'd0;
      pkt_valid <= 1'b0;
    end else begin
      stb_sync  <= {stb_sync[1:0], strobe_in};
      sof_sync  <= {sof_sync[0], sof_in};
      pkt_valid <= 1'b0;
      if (byte_stb) begin
        if (sof) begin
          remaining <= hdr_known ? hdr_len : 3'd0;
          pkt_valid <= hdr_known && (hdr_len == 3'd0);
        end else if (busy) begin
          remaining <= remaining - 3'd1;
          pkt_valid <= (remaining == 3'd1);
        end
      end
    end
  end

  // Packet registers need no reset: they are only used when pkt_valid is set,
  // which requires a complete packet after reset. This saves area.
  always @(posedge clk) begin
    if (byte_stb) begin
      if (sof) begin
        cmd  <= data[7:4];
        trap <= data[0];
      end else if (busy) begin
        payload <= {data, payload[39:8]};
      end
    end
  end

endmodule
