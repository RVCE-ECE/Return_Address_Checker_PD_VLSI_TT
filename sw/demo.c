/*
 * Shadow-stack monitor demo program (bare-metal RV32IMC, Spike).
 *
 * Build without -DATTACK -> clean.elf : ordinary calls, recursion deeper than
 *                                       the 8-entry shadow stack, function
 *                                       pointers, and an ECALL trap.
 * Build with    -DATTACK -> attack.elf: the same program, but process() copies
 *                                       more words than its stack buffer holds,
 *                                       overwriting the saved return address
 *                                       with &win.
 *
 * Build with -fcf-protection=branch (Zicfilp landing pads):
 *   lp_clean.elf                 : the clean program; every indirect call lands on an LPAD.
 *   lp_attack.elf (-DFWD_ATTACK) : an indirect call jumps to win+4, past its LPAD.
 *
 * Compile with -O0 -fno-stack-protector so the frame layout is the naive one.
 */

#include <stdint.h>

void exit(int code) __attribute__((noreturn));

volatile uint32_t sink;

/* ------------------------------------------------------------ the target */
void __attribute__((noinline)) win(void)
{
    sink = 0xBAD;
    exit(42);           /* the attacker "wins": exit code 42 */
}

/* ------------------------------------------------------- ordinary calls */
static int leaf(int x) { return x * 3 + 1; }
static int middle(int x) { return leaf(x) + leaf(x + 1); }
static int outer(int x) { return middle(x) - middle(x - 1); }

/* recursion deeper than the shadow stack (DEPTH = 8) */
static int fact(int n) { return n <= 1 ? 1 : n * fact(n - 1); }
static int fib(int n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

/* indirect calls through function pointers */
static int op_add(int a, int b) { return a + b; }
static int op_sub(int a, int b) { return a - b; }
static int op_mul(int a, int b) { return a * b; }
static int (*const ops[3])(int, int) = {op_add, op_sub, op_mul};

static int apply_all(int a, int b)
{
    int acc = 0;
    for (int i = 0; i < 3; i++)
        acc += ops[i](a, b);
    return acc;
}

static void do_syscall(void)
{
    __asm__ volatile("ecall");   /* trap and return: exercises the TRAP flag */
}

/* --------------------------------------------------- the vulnerable copy */
#ifdef ATTACK
#define N_WORDS 8               /* buf holds 4: words 4..7 smash the frame */
#else
#define N_WORDS 4
#endif

static uint32_t input[N_WORDS];

/* memcpy-style helper: its loop counter lives in its own frame */
static void __attribute__((noinline)) copy_words(uint32_t *dst, const uint32_t *src, int n)
{
    for (int i = 0; i < n; i++)
        dst[i] = src[i];
}

static void __attribute__((noinline)) process(const uint32_t *src, int n)
{
    uint32_t buf[4];
    copy_words(buf, src, n);     /* no bounds check */
    sink = buf[0];
}

int main(void)
{
    int r = 0;
    r += outer(5);
    r += fact(12);
    r += fib(10);
    r += apply_all(7, 3);
    do_syscall();

    for (int i = 0; i < N_WORDS; i++)
        input[i] = (uint32_t)(uintptr_t)win;   /* "attacker-controlled" data */
    process(input, N_WORDS);

#ifdef FWD_ATTACK
    /* Forward-edge hijack: an indirect call into the middle of win(), past its
       landing pad. Only detectable with -fcf-protection=branch + lp_en. */
    {
        void (*gadget)(void) = (void (*)(void))((uintptr_t)win + 4);
        gadget();
    }
#endif

    r += outer(2);
    sink = (uint32_t)r;
    return 0;
}
