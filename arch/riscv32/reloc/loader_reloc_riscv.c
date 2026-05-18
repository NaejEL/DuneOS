/*
 * DuneOS ELF relocator — RISC-V 32-bit.
 *
 * Future target: WCH CH32V / ESP32-C3 and any rv32imac board.
 *
 * Implemented relocations (planned):
 *   R_RISCV_32        — absolute 32-bit reference
 *   R_RISCV_HI20      — upper 20 bits of PC-relative or absolute address
 *   R_RISCV_LO12_I    — lower 12 bits (I-type instruction encoding)
 *   R_RISCV_LO12_S    — lower 12 bits (S-type instruction encoding)
 *   R_RISCV_CALL      — AUIPC+JALR pair (function call)
 *
 * Phase 24 (DHI): register with loader via duneos_arch_ops_t interface.
 *
 * Reference: RISC-V ELF psABI specification.
 */

/* TODO Phase 24+: implement riscv_apply_reloc() and expose via
 * duneos_arch_ops_t { .apply_reloc = riscv_apply_reloc }.  */
