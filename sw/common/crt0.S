/* Minimal RV32 start-up: stack, trap vector, call main, exit via HTIF. */
  .section .text.init, "ax"
  .globl _start
_start:
  la    sp, _stack_top
  la    t0, trap_entry
  csrw  mtvec, t0
  call  main
  call  exit          /* exit(main's return value); never returns */
1: j 1b

/* Trap handler for ECALL: skip the faulting instruction and resume.
   It contains no calls or returns, so it doesn't disturb the shadow stack. */
  .align 2
trap_entry:
  csrrw t1, mscratch, t1
  csrr  t1, mepc
  addi  t1, t1, 4
  csrw  mepc, t1
  csrrw t1, mscratch, t1
  mret
