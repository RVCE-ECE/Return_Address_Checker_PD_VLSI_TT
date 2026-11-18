# Build the demo programs, run them on Spike and produce trace packets.
#   make            -> build/*.elf, build/*.log, ../traces/{clean,attack}.{bin,json}
CC      = riscv64-elf-gcc
OBJDUMP = riscv64-elf-objdump
NM      = riscv64-elf-nm
SPIKE   = spike
PYTHON ?= python3

CFLAGS  = -march=rv32imc_zicsr -mabi=ilp32 -O0 -fno-stack-protector -ffreestanding \
          -nostdlib -nostartfiles -fno-builtin -g -Wall
LDFLAGS = -T common/link.ld -nostdlib -static -Wl,--no-warn-rwx-segments
COMMON  = common/crt0.S common/htif.c

TRACES  = ../traces
TOOLS   = ../tools

PROGS   = clean attack lp_clean lp_attack
all: $(PROGS:%=$(TRACES)/%.bin)

LPFLAGS = -march=rv32imc_zicsr_zicfilp -fcf-protection=branch
EXTRA_clean     =
EXTRA_attack    = -DATTACK
EXTRA_lp_clean  = $(LPFLAGS)
EXTRA_lp_attack = $(LPFLAGS) -DFWD_ATTACK
CONFIG_lp_clean  = 4
CONFIG_lp_attack = 4

build:
	mkdir -p build

build/%.elf: demo.c $(COMMON) common/link.ld | build
	$(CC) $(CFLAGS) $(EXTRA_$*) $(LDFLAGS) -o $@ $(COMMON) demo.c

build/%.dis: build/%.elf
	$(OBJDUMP) -d $< > $@

# Spike writes the commit log to stderr; a non-zero exit code is expected for attack (42).
build/%.log: build/%.elf build/%.dis
	-$(SPIKE) --isa=rv32imc_zicsr -m0x80000000:0x10000 -l --log-commits $< 2> $@; echo "exit code $$?" > build/$*.exit
	cat build/$*.exit

$(TRACES)/%.bin: build/%.log build/%.elf
	mkdir -p $(TRACES)
	$(PYTHON) $(TOOLS)/spike_to_trace.py $< -o $@ --elf build/$*.elf --nm $(NM) --config $(or $(CONFIG_$*),0)

clean:
	rm -rf build

.PRECIOUS: build/%.elf build/%.log build/%.dis
.PHONY: all clean
