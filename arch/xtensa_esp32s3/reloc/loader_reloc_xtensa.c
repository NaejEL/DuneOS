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
 * Phase 24 (DHI): this file will be registered with the loader via the
 * duneos_arch_ops_t interface so that duneos_loader/src/loader.c has no
 * Xtensa-specific code.
 *
 * Reference: Xtensa ISA Reference Manual, Appendix E (ELF ABI).
 */

/* TODO Phase 24: extract Xtensa reloc logic from loader.c into this file
 * and expose via duneos_arch_ops_t { .apply_reloc = xtensa_apply_reloc }.  */
