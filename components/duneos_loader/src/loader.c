#include "duneos/loader.h"
#include "duneos/elf.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_heap_caps.h"

static const char *TAG = "duneos/loader";

/* -------------------------------------------------------------------------
 * Internal app descriptor
 * ---------------------------------------------------------------------- */

#define MAX_SECTIONS 64

struct duneos_app {
    duneos_app_manifest_t manifest;

    /* Runtime base address of each section, indexed by section header index.
     * NULL for sections not loaded into memory (no SHF_ALLOC, size 0, etc.) */
    void *section_bases[MAX_SECTIONS];
    int   section_count;

    /* All PSRAM allocations — freed on unload */
    void *allocs[MAX_SECTIONS];
    int   alloc_count;

    void (*entry)(void);
};

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */

static void *psram_alloc(duneos_app_t *app, size_t size)
{
    void *p = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p && app->alloc_count < MAX_SECTIONS) {
        app->allocs[app->alloc_count++] = p;
    }
    return p;
}

static esp_err_t read_at(FILE *f, long offset, void *buf, size_t len)
{
    if (fseek(f, offset, SEEK_SET) != 0) return ESP_ERR_INVALID_ARG;
    if (fread(buf, 1, len, f) != len)    return ESP_ERR_INVALID_SIZE;
    return ESP_OK;
}

static const char *shdr_name(const char *shstrtab, const elf32_shdr_t *sh)
{
    return shstrtab + sh->sh_name;
}

/* -------------------------------------------------------------------------
 * ELF validation
 * ---------------------------------------------------------------------- */

static esp_err_t elf_validate(const elf32_hdr_t *hdr)
{
    if (memcmp(hdr->e_ident, ELF_MAGIC, ELF_MAGIC_SIZE) != 0) {
        ESP_LOGE(TAG, "not an ELF file");
        return ESP_ERR_INVALID_ARG;
    }
    if (hdr->e_ident[EI_CLASS] != ELFCLASS32) {
        ESP_LOGE(TAG, "not a 32-bit ELF");
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (hdr->e_ident[EI_DATA] != ELFDATA2LSB) {
        ESP_LOGE(TAG, "not little-endian ELF");
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (hdr->e_type != ET_REL) {
        ESP_LOGE(TAG, "e_type=%u — only ET_REL (1) supported", hdr->e_type);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (hdr->e_machine != EM_XTENSA) {
        ESP_LOGE(TAG, "e_machine=%u — expected EM_XTENSA (94)", hdr->e_machine);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (hdr->e_shoff == 0 || hdr->e_shnum == 0) {
        ESP_LOGE(TAG, "no section headers");
        return ESP_ERR_INVALID_ARG;
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Section classification
 *
 * With -ffunction-sections the compiler emits .text.funcname, .literal.funcname
 * etc. rather than a single .text.  We match by prefix.
 * Literal pools (.literal.*) are executable data adjacent to code — we load
 * them the same way as .text (both have SHF_ALLOC | SHF_EXECINSTR).
 * ---------------------------------------------------------------------- */

typedef enum {
    SEC_IGNORE,
    SEC_TEXT,       /* .text* and .literal* */
    SEC_DATA,       /* .data* */
    SEC_RODATA,     /* .rodata* */
    SEC_BSS,        /* .bss* */
} sec_kind_t;

static sec_kind_t classify_section(const char *name, uint32_t flags)
{
    if (!(flags & SHF_ALLOC)) return SEC_IGNORE;

    if (strncmp(name, ".text",    5) == 0) return SEC_TEXT;
    if (strncmp(name, ".literal", 8) == 0) return SEC_TEXT;
    if (strncmp(name, ".rodata",  7) == 0) return SEC_RODATA;
    if (strncmp(name, ".data",    5) == 0) return SEC_DATA;
    if (strncmp(name, ".bss",     4) == 0) return SEC_BSS;

    /* Xtensa-specific tool sections — not needed at runtime */
    if (strncmp(name, ".xt.",    4) == 0) return SEC_IGNORE;
    if (strncmp(name, ".xtensa", 7) == 0) return SEC_IGNORE;

    return SEC_IGNORE;
}

/* -------------------------------------------------------------------------
 * Section loading
 * ---------------------------------------------------------------------- */

static esp_err_t load_sections(FILE              *f,
                                const elf32_hdr_t  *hdr,
                                const elf32_shdr_t *shdrs,
                                const char         *shstrtab,
                                duneos_app_t       *app)
{
    app->section_count = hdr->e_shnum;

    for (int i = 0; i < hdr->e_shnum; i++) {
        const elf32_shdr_t *sh   = &shdrs[i];
        const char         *name = shdr_name(shstrtab, sh);
        app->section_bases[i]    = NULL;

        if (sh->sh_size == 0) continue;

        sec_kind_t kind = classify_section(name, sh->sh_flags);
        if (kind == SEC_IGNORE) continue;

        void *mem = psram_alloc(app, sh->sh_size);
        if (!mem) {
            ESP_LOGE(TAG, "PSRAM alloc failed: '%s' (%lu B)",
                     name, (unsigned long)sh->sh_size);
            return ESP_ERR_NO_MEM;
        }
        app->section_bases[i] = mem;

        if (sh->sh_type == SHT_NOBITS) {
            /* .bss: zero-initialise — the data is NOT stored in the ELF */
            memset(mem, 0, sh->sh_size);
        } else {
            if (read_at(f, sh->sh_offset, mem, sh->sh_size) != ESP_OK) {
                ESP_LOGE(TAG, "read failed: '%s'", name);
                return ESP_ERR_INVALID_ARG;
            }
        }

        ESP_LOGD(TAG, "  loaded %-28s %4lu B @ %p",
                 name, (unsigned long)sh->sh_size, mem);
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Symbol resolution
 * ---------------------------------------------------------------------- */

static void * __attribute__((used)) resolve_symbol(const char *name)
{
    const duneos_symbol_t *t = duneos_symbol_table_get();
    for (; t->name; t++) {
        if (strcmp(t->name, name) == 0) return t->ptr;
    }
    return NULL;
}

/* Compute the runtime address of an ELF symbol. */
static void *symbol_address(const elf32_sym_t  *sym,
                             const char         *strtab,
                             const duneos_app_t *app)
{
    if (sym->st_shndx == SHN_ABS) {
        return (void *)(uintptr_t)sym->st_value;
    }
    if (sym->st_shndx == SHN_UNDEF) {
        const char *name = strtab + sym->st_name;
        void *ptr = resolve_symbol(name);
        if (!ptr) {
            ESP_LOGE(TAG, "unresolved symbol: '%s'", name);
        }
        return ptr;
    }
    if (sym->st_shndx < MAX_SECTIONS && app->section_bases[sym->st_shndx]) {
        return (uint8_t *)app->section_bases[sym->st_shndx] + sym->st_value;
    }
    return NULL;
}

/* -------------------------------------------------------------------------
 * Relocation application
 *
 * Xtensa uses RELA (relocation with explicit addend) exclusively.
 *
 * Types handled:
 *   R_XTENSA_32        — 32-bit absolute: *ptr = S + A
 *   R_XTENSA_SLOT0_OP  — patch instruction slot 0 (typically L32R)
 *   R_XTENSA_ASM_EXPAND — assembler hint, no loader action needed
 *
 * The .rela.xt.* sections (Xtensa tool metadata) target sections without
 * SHF_ALLOC and are skipped by the target-section check below.
 * ---------------------------------------------------------------------- */

/* Apply R_XTENSA_SLOT0_OP to a loaded instruction word.
 *
 * The only SLOT0_OP case we see for a simple call through a literal pool is
 * L32R (opcode 0x1), which loads a 32-bit value from a PC-relative address.
 *
 * L32R encoding (24-bit, little-endian):
 *   byte[0] = (dest_reg << 4) | 0x1
 *   byte[1] = imm16[7:0]
 *   byte[2] = imm16[15:8]
 *
 * The 16-bit signed offset encodes: target = ((PC+3) & ~3) + imm16 * 4
 * So:  imm16 = (S + A - ((PC + 3) & ~3)) / 4
 */
static esp_err_t apply_slot0_op(uint8_t  *insn_ptr,
                                 uint32_t  insn_pc,
                                 uint32_t  target_addr)
{
    uint8_t opcode = insn_ptr[0] & 0x0F;

    if (opcode == 0x1) {
        /* L32R */
        uint32_t aligned_pc = (insn_pc + 3) & ~3u;
        int32_t  offset     = ((int32_t)target_addr - (int32_t)aligned_pc) / 4;

        if ((offset * 4) != ((int32_t)target_addr - (int32_t)aligned_pc)) {
            ESP_LOGE(TAG, "L32R offset not aligned: target=0x%08lx pc=0x%08lx",
                     (unsigned long)target_addr, (unsigned long)insn_pc);
            return ESP_ERR_INVALID_ARG;
        }
        if (offset < -32768 || offset > 32767) {
            ESP_LOGE(TAG, "L32R offset out of 16-bit range: %ld", (long)offset);
            return ESP_ERR_INVALID_ARG;
        }

        insn_ptr[1] = (uint8_t)(offset & 0xFF);
        insn_ptr[2] = (uint8_t)((offset >> 8) & 0xFF);
        return ESP_OK;
    }

    ESP_LOGW(TAG, "SLOT0_OP: unhandled opcode 0x%x at %p — skipping",
             opcode, insn_ptr);
    return ESP_OK;
}

static esp_err_t apply_relocations(FILE               *f,
                                    const elf32_hdr_t  *hdr,
                                    const elf32_shdr_t *shdrs,
                                    const char         *shstrtab,
                                    const elf32_sym_t  *symtab,
                                    int                 symcount,
                                    const char         *strtab,
                                    duneos_app_t       *app)
{
    (void)f;

    for (int i = 0; i < hdr->e_shnum; i++) {
        const elf32_shdr_t *rsh = &shdrs[i];
        if (rsh->sh_type != SHT_RELA) continue;

        /* Target section: sh_info for RELA */
        uint32_t target_idx = rsh->sh_info;
        if (target_idx >= (uint32_t)hdr->e_shnum) continue;

        void *target_base = app->section_bases[target_idx];
        if (!target_base) continue; /* section not loaded (e.g. .xt.*) */

        int nentries = rsh->sh_size / sizeof(elf32_rela_t);
        elf32_rela_t *relas = malloc(rsh->sh_size);
        if (!relas) return ESP_ERR_NO_MEM;

        if (read_at(f, rsh->sh_offset, relas, rsh->sh_size) != ESP_OK) {
            free(relas);
            return ESP_ERR_INVALID_ARG;
        }

        ESP_LOGD(TAG, "  rela %-28s → %-20s (%d entries)",
                 shdr_name(shstrtab, rsh),
                 shdr_name(shstrtab, &shdrs[target_idx]),
                 nentries);

        for (int j = 0; j < nentries; j++) {
            const elf32_rela_t *rel = &relas[j];
            uint32_t sym_idx = ELF32_R_SYM(rel->r_info);
            uint8_t  rel_type = ELF32_R_TYPE(rel->r_info);

            if (sym_idx >= (uint32_t)symcount) {
                ESP_LOGE(TAG, "relocation symbol index %lu out of range",
                         (unsigned long)sym_idx);
                free(relas);
                return ESP_ERR_INVALID_ARG;
            }

            const elf32_sym_t *sym = &symtab[sym_idx];
            void *S = symbol_address(sym, strtab, app);

            uint8_t *patch_ptr = (uint8_t *)target_base + rel->r_offset;
            uint32_t patch_pc  = (uint32_t)(uintptr_t)patch_ptr;
            uint32_t target_addr = (uint32_t)(uintptr_t)S + rel->r_addend;

            switch (rel_type) {
            case R_XTENSA_32:
                /* 32-bit absolute address */
                if (!S) {
                    free(relas);
                    return ESP_ERR_INVALID_ARG;
                }
                *(uint32_t *)patch_ptr = target_addr;
                break;

            case R_XTENSA_SLOT0_OP: {
                if (!S) {
                    free(relas);
                    return ESP_ERR_INVALID_ARG;
                }
                esp_err_t err = apply_slot0_op(patch_ptr, patch_pc, target_addr);
                if (err != ESP_OK) { free(relas); return err; }
                break;
            }

            case R_XTENSA_ASM_EXPAND:
                /* Assembler relaxation hint — no loader action needed */
                break;

            default:
                ESP_LOGW(TAG, "unhandled relocation type %u at offset 0x%lx — skipping",
                         rel_type, (unsigned long)rel->r_offset);
                break;
            }
        }

        free(relas);
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Manifest extraction
 * ---------------------------------------------------------------------- */

static esp_err_t extract_manifest(FILE               *f,
                                   const elf32_hdr_t  *hdr,
                                   const elf32_shdr_t *shdrs,
                                   const char         *shstrtab,
                                   duneos_app_manifest_t *out)
{
    for (int i = 0; i < hdr->e_shnum; i++) {
        if (strcmp(shdr_name(shstrtab, &shdrs[i]),
                   DUNEOS_MANIFEST_SECTION) != 0) continue;

        const elf32_shdr_t *sh = &shdrs[i];
        if (sh->sh_size < 2 || sh->sh_size > 4096) return ESP_ERR_INVALID_SIZE;

        char *json = malloc(sh->sh_size + 1);
        if (!json) return ESP_ERR_NO_MEM;
        if (read_at(f, sh->sh_offset, json, sh->sh_size) != ESP_OK) {
            free(json);
            return ESP_ERR_INVALID_ARG;
        }
        json[sh->sh_size] = '\0';

        strlcpy(out->name, "unknown", sizeof(out->name));
        out->required_abi_version = 1;

        const char *p;
        if ((p = strstr(json, "\"name\"")) != NULL) {
            p = strchr(p + 6, '"');
            if (p++) sscanf(p, "%63[^\"]", out->name);
        }
        if ((p = strstr(json, "\"version\"")) != NULL) {
            p = strchr(p + 9, '"');
            if (p++) sscanf(p, "%15[^\"]", out->version);
        }
        if ((p = strstr(json, "\"required_abi_version\"")) != NULL) {
            p = strchr(p + 22, ':');
            if (p) sscanf(p + 1, "%lu",
                          (unsigned long *)&out->required_abi_version);
        }

        free(json);
        ESP_LOGI(TAG, "manifest: '%s' v%s (ABI>=%lu)",
                 out->name, out->version,
                 (unsigned long)out->required_abi_version);
        return ESP_OK;
    }

    ESP_LOGW(TAG, "no " DUNEOS_MANIFEST_SECTION " section");
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

esp_err_t duneos_loader_load(const char *path, duneos_app_t **out_app)
{
    if (!path || !out_app) return ESP_ERR_INVALID_ARG;
    *out_app = NULL;

    FILE *f = fopen(path, "rb");
    if (!f) {
        ESP_LOGE(TAG, "cannot open '%s'", path);
        return ESP_ERR_NOT_FOUND;
    }

    esp_err_t    err     = ESP_FAIL;
    elf32_hdr_t  hdr;
    elf32_shdr_t *shdrs    = NULL;
    char         *shstrtab = NULL;
    elf32_sym_t  *symtab   = NULL;
    char         *strtab   = NULL;
    int           symcount = 0;
    duneos_app_t *app      = NULL;

    /* 1. ELF header */
    if (read_at(f, 0, &hdr, sizeof(hdr)) != ESP_OK) {
        ESP_LOGE(TAG, "cannot read ELF header");
        err = ESP_ERR_INVALID_ARG;
        goto out;
    }
    err = elf_validate(&hdr);
    if (err != ESP_OK) goto out;

    if (hdr.e_shnum > MAX_SECTIONS) {
        ESP_LOGE(TAG, "too many sections: %u (max %d)", hdr.e_shnum, MAX_SECTIONS);
        err = ESP_ERR_NOT_SUPPORTED;
        goto out;
    }

    ESP_LOGI(TAG, "loading '%s' (%u sections)", path, hdr.e_shnum);

    /* 2. Section header table */
    shdrs = malloc(hdr.e_shnum * sizeof(elf32_shdr_t));
    if (!shdrs) { err = ESP_ERR_NO_MEM; goto out; }
    if (read_at(f, hdr.e_shoff, shdrs, hdr.e_shnum * sizeof(elf32_shdr_t)) != ESP_OK) {
        err = ESP_ERR_INVALID_ARG;
        goto out;
    }

    /* 3. Section name string table */
    {
        const elf32_shdr_t *ss = &shdrs[hdr.e_shstrndx];
        shstrtab = malloc(ss->sh_size + 1);
        if (!shstrtab) { err = ESP_ERR_NO_MEM; goto out; }
        if (read_at(f, ss->sh_offset, shstrtab, ss->sh_size) != ESP_OK) {
            err = ESP_ERR_INVALID_ARG;
            goto out;
        }
        shstrtab[ss->sh_size] = '\0';
    }

    /* 4. Symbol table + string table */
    for (int i = 0; i < hdr.e_shnum; i++) {
        if (shdrs[i].sh_type != SHT_SYMTAB) continue;

        symcount = shdrs[i].sh_size / sizeof(elf32_sym_t);
        symtab   = malloc(shdrs[i].sh_size);
        if (!symtab) { err = ESP_ERR_NO_MEM; goto out; }
        if (read_at(f, shdrs[i].sh_offset, symtab, shdrs[i].sh_size) != ESP_OK) {
            err = ESP_ERR_INVALID_ARG;
            goto out;
        }

        const elf32_shdr_t *str_sh = &shdrs[shdrs[i].sh_link];
        strtab = malloc(str_sh->sh_size + 1);
        if (!strtab) { err = ESP_ERR_NO_MEM; goto out; }
        if (read_at(f, str_sh->sh_offset, strtab, str_sh->sh_size) != ESP_OK) {
            err = ESP_ERR_INVALID_ARG;
            goto out;
        }
        strtab[str_sh->sh_size] = '\0';
        break;
    }

    /* 5. Allocate app descriptor */
    app = calloc(1, sizeof(duneos_app_t));
    if (!app) { err = ESP_ERR_NO_MEM; goto out; }

    /* 6. Manifest */
    err = extract_manifest(f, &hdr, shdrs, shstrtab, &app->manifest);
    if (err != ESP_OK) goto out;

    if (app->manifest.required_abi_version > DUNEOS_ABI_VERSION) {
        ESP_LOGE(TAG, "app requires ABI v%lu, kernel is v%d",
                 (unsigned long)app->manifest.required_abi_version,
                 DUNEOS_ABI_VERSION);
        err = ESP_ERR_NOT_SUPPORTED;
        goto out;
    }

    /* 7. Load sections into PSRAM */
    err = load_sections(f, &hdr, shdrs, shstrtab, app);
    if (err != ESP_OK) goto out;

    /* 8. Apply relocations */
    err = apply_relocations(f, &hdr, shdrs, shstrtab,
                             symtab, symcount, strtab, app);
    if (err != ESP_OK) goto out;

    /* 9. Locate app_main */
    for (int i = 0; i < symcount; i++) {
        const elf32_sym_t *sym = &symtab[i];
        if (sym->st_shndx == SHN_UNDEF || sym->st_shndx >= hdr.e_shnum) continue;
        if (strcmp(strtab + sym->st_name, "app_main") != 0) continue;

        void *base = app->section_bases[sym->st_shndx];
        if (!base) {
            ESP_LOGE(TAG, "app_main is in an unloaded section");
            err = ESP_ERR_INVALID_ARG;
            goto out;
        }
        app->entry = (void (*)(void))((uint8_t *)base + sym->st_value);
        ESP_LOGI(TAG, "app_main @ %p", (void *)app->entry);
        break;
    }

    if (!app->entry) {
        ESP_LOGE(TAG, "app_main not found in symbol table");
        err = ESP_ERR_NOT_FOUND;
        goto out;
    }

    *out_app = app;
    app = NULL;
    err = ESP_OK;

out:
    fclose(f);
    free(shdrs);
    free(shstrtab);
    free(symtab);
    free(strtab);
    if (app) duneos_loader_unload(app);
    return err;
}

const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app)
{
    return app ? &app->manifest : NULL;
}

esp_err_t duneos_loader_run(duneos_app_t *app)
{
    if (!app || !app->entry) return ESP_ERR_INVALID_ARG;
    ESP_LOGI(TAG, "jumping to app_main @ %p", (void *)app->entry);
    app->entry();
    return ESP_OK;
}

void duneos_loader_unload(duneos_app_t *app)
{
    if (!app) return;
    for (int i = 0; i < app->alloc_count; i++) {
        heap_caps_free(app->allocs[i]);
    }
    free(app);
}
