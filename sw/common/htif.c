/* HTIF exit for Spike. */
#include <stdint.h>

volatile uint64_t tohost __attribute__((section(".tohost"), aligned(8)));
volatile uint64_t fromhost __attribute__((section(".tohost"), aligned(8)));

void __attribute__((noreturn)) exit(int code)
{
    volatile uint32_t *th = (volatile uint32_t *)&tohost;
    th[1] = 0;
    th[0] = ((uint32_t)code << 1) | 1;
    for (;;)
        ;
}
