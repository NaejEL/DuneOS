/*
 * Minimal DuneOS app — used to validate the ELF loader.
 *
 * Resolves exactly one external symbol: duneos_exit.
 * No I/O, no memory allocation — pure control flow test.
 *
 * Expected loader behaviour:
 *   1. Load ET_REL ELF into PSRAM
 *   2. Resolve duneos_exit → kernel export table
 *   3. Apply R_XTENSA_32 or SLOT0_OP relocation for the call site
 *   4. Jump to app_main → kernel logs "app exited cleanly"
 */

extern void duneos_exit(int code);

void app_main(void)
{
    duneos_exit(0);
}
