/*
 * DuneOS ELF relocator — RISC-V 32-bit.
 *
 * Target boards (Phase 28): ESP32-C6 (RV32IMC), ESP32-P4 (RV32IMA dual-core
 * HP + LP). Also covers any future rv32imac board (WCH CH32V, etc.).
 *
 * Implemented relocations (planned):
 *   R_RISCV_32        — absolute 32-bit reference
 *   R_RISCV_HI20      — upper 20 bits of PC-relative or absolute address
 *   R_RISCV_LO12_I    — lower 12 bits (I-type instruction encoding)
 *   R_RISCV_LO12_S    — lower 12 bits (S-type instruction encoding)
 *   R_RISCV_CALL      — AUIPC+JALR pair (function call)
 *   R_RISCV_BRANCH    — conditional branch (B-type)
 *   R_RISCV_JAL       — direct jump (J-type)
 *
 * Phase 28: register with the loader via duneos_arch_ops_t interface — the
 * Xtensa extraction (loader.c → loader_reloc_xtensa.c) is the prerequisite
 * that lands first in Phase 28.
 *
 * Reference: RISC-V ELF psABI specification.
 */

/* TODO Phase 28: implement riscv_apply_reloc() and expose via
 * duneos_arch_ops_t { .apply_reloc = riscv_apply_reloc }.  */
