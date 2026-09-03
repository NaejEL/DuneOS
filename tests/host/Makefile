ifeq ($(origin CC),default)
CC      := gcc
endif
CFLAGS  ?= -std=c17 -O1 -g
CFLAGS  += -Wall -Wextra -Werror -D_POSIX_C_SOURCE=200809L

ROOT    := ../..

TESTS   := test_re test_i2c_decode test_known_yaml

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

test: all
	@rc=0; \
	for t in $(TESTS); do \
		./$$t || rc=1; \
	done; \
	exit $$rc

clean:
	rm -f $(TESTS) $(HOSTUNITS) known_yaml_test_*.tmp

.PHONY: all test clean
