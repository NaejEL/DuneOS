ifeq ($(origin CC),default)
CC      := gcc
endif
CFLAGS  ?= -std=c17 -O1 -g
CFLAGS  += -Wall -Wextra -Werror -D_POSIX_C_SOURCE=200809L

ROOT    := ../..

TESTS   := test_re test_i2c_decode test_known_yaml test_elf_validate \
           test_elf_scan test_appmeta test_manifest_depth test_ld2450 \
           test_input_intent test_ui_vkbd

# The shared pure ELF32 reader (ADR 043): one source, one include dir, for the
# kernel-side suites and the SDK-side one alike.
ELF_INC := -I$(ROOT)/lib/elf/include
ELF_SRC := $(ROOT)/lib/elf/elf_parse.c
ELF_HDR := $(ROOT)/lib/elf/include/duneos/elf_parse.h \
           $(ROOT)/lib/elf/include/duneos/elf.h
LIMITS_HDR := $(ROOT)/lib/elf/include/duneos/loader_limits.h
LOADER_INC := -I$(ROOT)/kernel/duneos_loader/include
APPMETA_SRC := $(ROOT)/sdk/app/libappmeta.c
CJSON_INC := -I$(ROOT)/third_party/cjson
CJSON_SRC := $(ROOT)/third_party/cjson/cJSON.c

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

elf_parse.o: $(ELF_SRC) $(ELF_HDR)
	$(CC) $(CFLAGS) $(ELF_INC) -c $< -o $@

test_re: test_re.c $(ROOT)/sdk/re/re.c tassert.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/re/include test_re.c $(ROOT)/sdk/re/re.c -o $@

test_i2c_decode: test_i2c_decode.c $(ROOT)/sdk/proto/i2c_decode.c tassert.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/proto/include test_i2c_decode.c $(ROOT)/sdk/proto/i2c_decode.c -o $@

test_known_yaml: test_known_yaml.c known_yaml_cases.h tassert.h \
                 $(ROOT)/sdk/net/known_yaml.c \
                 $(ROOT)/sdk/net/include/duneos/known_yaml.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/net/include test_known_yaml.c \
	    $(ROOT)/sdk/net/known_yaml.c -o $@

test_ld2450: test_ld2450.c $(ROOT)/sdk/sensor/libld2450.c \
             $(ROOT)/sdk/sensor/include/duneos/ld2450.h tassert.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/sensor/include test_ld2450.c \
	    $(ROOT)/sdk/sensor/libld2450.c -o $@

# The two pure halves of the input-portability layer. Both are compiled with the
# same -Wall -Wextra -Werror as everything else here, which is the gate that
# keeps them free of ESP-IDF, FreeRTOS and any fd: a dependency creeping back in
# breaks this build before it reaches a board.
test_input_intent: test_input_intent.c $(ROOT)/sdk/input/libinput.c \
                   $(ROOT)/sdk/input/include/duneos/input.h \
                   $(ROOT)/kernel/duneos_kernel/include/duneos/input_ioctl.h tassert.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/input/include \
	    -I$(ROOT)/kernel/duneos_kernel/include \
	    test_input_intent.c $(ROOT)/sdk/input/libinput.c -o $@

test_ui_vkbd: test_ui_vkbd.c $(ROOT)/sdk/ui/ui_vkbd.c \
              $(ROOT)/sdk/ui/include/duneos/ui_vkbd.h \
              $(ROOT)/kernel/duneos_kernel/include/duneos/input_ioctl.h tassert.h
	$(CC) $(CFLAGS) -I$(ROOT)/sdk/ui/include \
	    -I$(ROOT)/kernel/duneos_kernel/include \
	    test_ui_vkbd.c $(ROOT)/sdk/ui/ui_vkbd.c -o $@

test_elf_validate: test_elf_validate.c elf_corpus.c elf_corpus.h $(ELF_SRC) tassert.h \
                   $(ELF_HDR) $(LIMITS_HDR)
	$(CC) $(CFLAGS) $(ELF_INC) test_elf_validate.c elf_corpus.c $(ELF_SRC) -o $@

test_elf_scan: test_elf_scan.c elf_corpus.c elf_corpus.h $(ELF_SRC) tassert.h \
               $(ELF_HDR) $(LIMITS_HDR)
	$(CC) $(CFLAGS) $(ELF_INC) test_elf_scan.c elf_corpus.c $(ELF_SRC) -o $@

# DUP-01: the SDK-side consumer of the shared reader, compiled from the SHIPPED
# sdk/app/libappmeta.c and the SHIPPED lib/elf/elf_parse.c — no copy of either.
# -Wall -Wextra -Werror is the gate that keeps libappmeta free of anything an
# app cannot link.
test_appmeta: test_appmeta.c elf_corpus.c elf_corpus.h $(APPMETA_SRC) $(ELF_SRC) tassert.h \
              $(ROOT)/sdk/app/include/duneos/appmeta.h $(ELF_HDR) $(LIMITS_HDR)
	$(CC) $(CFLAGS) $(ELF_INC) -I$(ROOT)/sdk/app/include \
	    test_appmeta.c elf_corpus.c $(APPMETA_SRC) $(ELF_SRC) -o $@

# LEG-41: cJSON is linked in at its STOCK nesting limit on purpose — the test
# asserts that what the pre-parse bound rejects is what cJSON would have
# descended into. Do not pass -DCJSON_NESTING_LIMIT here.
test_manifest_depth: test_manifest_depth.c \
                     $(ROOT)/kernel/duneos_loader/src/manifest_depth.c \
                     $(ROOT)/kernel/duneos_loader/include/duneos/manifest_depth.h \
                     $(CJSON_SRC) tassert.h
	$(CC) $(CFLAGS) $(LOADER_INC) $(CJSON_INC) test_manifest_depth.c \
	    $(ROOT)/kernel/duneos_loader/src/manifest_depth.c $(CJSON_SRC) -o $@

test: all
	@rc=0; \
	for t in $(TESTS); do \
		./$$t || rc=1; \
	done; \
	exit $$rc

# The fuzz targets are deliberately NOT reachable from `all` or `test`: the
# unit suite must stay runnable on a machine with no clang.
fuzz: fuzz_elf_validate

fuzz_elf_validate: fuzz_elf_validate.c $(ELF_SRC) $(ELF_HDR)
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
	      elf_scan_*/* appmeta_*/* \
	      .fuzzprobe .fuzzprobe.c fuzz_ran.marker
	rm -rf $(FUZZ_CORPUS) $(FUZZ_ARTIFACTS) elf_scan_* appmeta_*

.PHONY: all test clean fuzz fuzz-run
