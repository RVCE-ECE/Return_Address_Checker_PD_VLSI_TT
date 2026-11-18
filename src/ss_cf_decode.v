/*
 * Copyright (c) 2026 Siddharth Senthil Kumar, Sumedh Kallapur, Vibin DJ, Yatin Sangwan
 * SPDX-License-Identifier: Apache-2.0
 *
 * ss_cf_decode: classify an RV32IMC instruction using the RISC-V
 * return-address-stack hint rules (link registers x1 and x5).
 * Only inst[23:0] is needed: opcode, rd, funct3, rs1, or a full
 * 16-bit compressed encoding.
 */

`default_nettype none

module ss_cf_decode (
    input  wire [23:0] inst,
    output reg         push,
    output reg         pop,
    output wire        is_c,
    output reg         indirect_nonret,
    output reg         is_lpad
);

  assign is_c = (inst[1:0] != 2'b11);

  // 32-bit fields
  wire [6:0] opcode = inst[6:0];
  wire [4:0] rd     = inst[11:7];
  wire [2:0] f3     = inst[14:12];
  wire [4:0] rs1    = inst[19:15];

  // 16-bit fields
  wire [2:0] c_f3   = inst[15:13];
  wire       c_b12  = inst[12];
  wire [4:0] c_rs1  = inst[11:7];
  wire [4:0] c_rs2  = inst[6:2];

  wire rd_link    = (rd == 5'd1) || (rd == 5'd5);
  wire rs1_link   = (rs1 == 5'd1) || (rs1 == 5'd5);
  wire rs1_exempt = rs1_link || (rs1 == 5'd7);
  wire c_rs1_link   = (c_rs1 == 5'd1) || (c_rs1 == 5'd5);
  wire c_rs1_exempt = c_rs1_link || (c_rs1 == 5'd7);

  always @(*) begin
    push            = 1'b0;
    pop             = 1'b0;
    indirect_nonret = 1'b0;
    is_lpad         = 1'b0;
    if (!is_c) begin
      if (opcode == 7'b1101111) begin                  // JAL
        push = rd_link;
      end else if (opcode == 7'b1100111 && f3 == 3'b000) begin  // JALR
        push            = rd_link;
        pop             = rs1_link && !(rd_link && (rd == rs1));
        indirect_nonret = !rs1_exempt;
      end else if (opcode == 7'b0010111 && rd == 5'd0) begin    // AUIPC x0 (LPAD)
        is_lpad = 1'b1;
      end
    end else begin
      if (inst[1:0] == 2'b01 && c_f3 == 3'b001) begin  // C.JAL
        push = 1'b1;
      end else if (inst[1:0] == 2'b10 && c_f3 == 3'b100 &&
                   c_rs1 != 5'd0 && c_rs2 == 5'd0) begin // C.JR / C.JALR
        if (!c_b12) begin                                // C.JR
          pop = c_rs1_link;
        end else begin                                   // C.JALR, rd = x1
          push = 1'b1;
          pop  = (c_rs1 == 5'd5);
        end
        indirect_nonret = !c_rs1_exempt;
      end
    end
  end

  wire _unused = &{inst[23:20], 1'b0};

endmodule
