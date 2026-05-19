/*
 * DuneOS ELF relocator — Xtensa ESP32-S3.
 *
 * Applies ET_REL RELA relocations produced by xtensa-esp32s3-elf-gcc.
 * Called by duneos_loader after section allocation and before app_main.
 *
 * Implemented relocations:
 *   R_XTENSA_32        — absolute 32-bit reference
 *   R_XTENSA_SLOT0_OP  — L32R literal patch (PC-relative, backward only)
 *   R_XTENSA_ASM_EXPAND — relaxation placeholder, safe to ignore
 *   R_XTENSA_DIFF8/16/32 — link-time difference, handled as signed delta
 *
 * Phase 28 (RISC-V Espressif ports): this file will be registered with the
 * loader via the duneos_arch_ops_t interface so that duneos_loader/src/loader.c
 * has no Xtensa-specific code. The extraction is the prerequisite for adding
 * RISC-V — without it, the reloc logic would be duplicated.
 *
 * Originally promised in Phase 24 but never executed; rescheduled to Phase 28
 * where the second architecture forces the modularisation.
 *
 * Reference: Xtensa ISA Reference Manual, Appendix E (ELF ABI).
 */

/* TODO Phase 28: extract Xtensa reloc logic from loader.c into this file
 * and expose via duneos_arch_ops_t { .apply_reloc = xtensa_apply_reloc }.
 * Concerns ~150 lines in loader.c: R_XTENSA_32, R_XTENSA_SLOT0_OP (with the
 * apply_slot0_op helper), R_XTENSA_ASM_EXPAND, R_XTENSA_DIFF8/16/32.  */
