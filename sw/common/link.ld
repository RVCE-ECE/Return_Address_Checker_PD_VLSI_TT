/* Bare-metal RV32 layout for Spike: everything in RAM at 0x80000000. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY { RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 64K }

SECTIONS
{
  .text.init : { *(.text.init) } > RAM
  .text      : { *(.text .text.*) } > RAM
  .rodata    : { *(.rodata .rodata.* .srodata .srodata.*) } > RAM
  . = ALIGN(64);
  .tohost    : { *(.tohost) } > RAM
  .data      : { *(.data .data.* .sdata .sdata.*) } > RAM
  .bss       : { *(.bss .bss.* .sbss .sbss.* COMMON) } > RAM
  . = ALIGN(16);
  . += 4K;
  _stack_top = .;
}
