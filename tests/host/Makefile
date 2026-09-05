ifeq ($(origin CC),default)
CC      := gcc
endif
CFLAGS  ?= -std=c17 -O1 -g
CFLAGS  += -Wall -Wextra -Werror -D_POSIX_C_SOURCE=200809L

ROOT    := ../..

TESTS   := test_re test_i2c_decode test_known_yaml test_elf_validate \
           test_elf_scan

ELF_INC := -I$(ROOT)/kernel/duneos_loader/include
ELF_SRC := $(ROOT)/kernel/duneos_loader/src/elf_parse.c

# Fuzzing (LEG-26). libFuzzer needs clang; override FUZZ_CC for another one.
FUZZ_CC        ?= clang
FUZZ_SECONDS   ?= 60
FUZZ_CORPUS    ?= fuzz_corpus
FUZZ_ARTIFACTS ?= fuzz_artifacts
FUZZ_CFLAGS    ?= -std=c17 -O1 -g -Wall -Wextra -Werror \
                  -fsanitize=fuzzer,address,undefined \
                  -fno-sanitize-recover=all


# Host-compilable kernel units with no test of their own yet: compiled here so
# the host build breaks the moment they regain a target-only dependency.
HOSTUNITS := elf_parse.o

all: $(TESTS) $(HOSTUNITS)

elf_parse.o: $(ROOT)/kernel/duneos_loader/src/elf_parse.c \
             $(ROOT)/kernel/duneos_loader/include/duneos/elf_parse.h
	$(CC) $(CFLAGS) -I$(ROOT)/kernel/duneos_loader/include -c $< -o $@

test_re: test_re.c $(ROOT)/sdk/re/re.c tassert.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/re/include test_re.c $(ROOT)/sdk/re/re.c -o $@

test_i2c_decode: test_i2c_decode.c $(ROOT)/sdk/proto/i2c_decode.c tassert.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/proto/include test_i2c_decode.c $(ROOT)/sdk/proto/i2c_decode.c -o $@

test_known_yaml: test_known_yaml.c known_yaml_cases.h tassert.h
	$(CC) $(CFLAGS) test_known_yaml.c -o $@

test_elf_validate: test_elf_validate.c elf_corpus.c elf_corpus.h $(ELF_SRC) tassert.h
	$(CC) $(CFLAGS) $(ELF_INC) test_elf_validate.c elf_corpus.c $(ELF_SRC) -o $@

test_elf_scan: test_elf_scan.c elf_corpus.c elf_corpus.h $(ELF_SRC) tassert.h \
               $(ROOT)/kernel/duneos_loader/include/duneos/loader_limits.h
	$(CC) $(CFLAGS) $(ELF_INC) test_elf_scan.c elf_corpus.c $(ELF_SRC) -o $@

test: all
	@rc=0; \
	for t in $(TESTS); do \
		./$$t || rc=1; \
	done; \
	exit $$rc

# The fuzz targets are deliberately NOT reachable from `all` or `test`: the
# unit suite must stay runnable on a machine with no clang.
fuzz: fuzz_elf_validate

fuzz_elf_validate: fuzz_elf_validate.c $(ELF_SRC) $(ROOT)/kernel/duneos_loader/include/duneos/elf_parse.h
	@command -v $(FUZZ_CC) >/dev/null 2>&1 || { \
		echo "ERROR: '$(FUZZ_CC)' not found — the fuzz target needs a libFuzzer-capable compiler."; \
		echo "       Install clang (Debian/Ubuntu: apt install clang; Arch: pacman -S clang),"; \
		echo "       or point FUZZ_CC at one: make -C tests/host fuzz FUZZ_CC=clang-18"; \
		exit 1; }
	@echo 'int LLVMFuzzerTestOneInput(const unsigned char *d, unsigned long n){(void)d;(void)n;return 0;}' > .fuzzprobe.c
	@$(FUZZ_CC) -fsanitize=fuzzer .fuzzprobe.c -o .fuzzprobe 2>/dev/null || { \
		rm -f .fuzzprobe.c .fuzzprobe; \
		echo "ERROR: $(FUZZ_CC) does not support -fsanitize=fuzzer."; \
		echo "       Install the compiler-rt fuzzer runtime (Debian/Ubuntu: apt install libclang-rt-dev),"; \
		echo "       or point FUZZ_CC at a clang that ships it."; \
		exit 1; }
	@rm -f .fuzzprobe.c .fuzzprobe
	$(FUZZ_CC) $(FUZZ_CFLAGS) $(ELF_INC) fuzz_elf_validate.c $(ELF_SRC) -o $@

# Time-bounded run seeded with the malformed-ELF corpus. A crash writes a
# reproducer under $(FUZZ_ARTIFACTS)/ and returns non-zero.
fuzz-run: fuzz_elf_validate test_elf_validate
	./test_elf_validate --dump-corpus $(FUZZ_CORPUS)
	mkdir -p $(FUZZ_ARTIFACTS)
	./fuzz_elf_validate -max_total_time=$(FUZZ_SECONDS) -timeout=25 \
		-artifact_prefix=$(FUZZ_ARTIFACTS)/ $(FUZZ_CORPUS)

clean:
	rm -f $(TESTS) $(HOSTUNITS) known_yaml_test_*.tmp fuzz_elf_validate \
	      elf_scan_*/* \
	      .fuzzprobe .fuzzprobe.c fuzz_ran.marker
	rm -rf $(FUZZ_CORPUS) $(FUZZ_ARTIFACTS) elf_scan_*

.PHONY: all test clean fuzz fuzz-run
