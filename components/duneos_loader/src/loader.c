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

struct duneos_app {
    duneos_app_manifest_t manifest;

    /* Runtime section pointers — all allocated from PSRAM */
    void   *text;
    void   *data;
    void   *bss;        /* zero-initialised by loader, not present in file */
    void   *rodata;

    size_t  text_size;
    size_t  data_size;
    size_t  bss_size;
    size_t  rodata_size;

    /* Per-section base addresses after loading (needed during relocation) */
    void   *section_bases[64];  /* indexed by section header index */
    int     section_count;

    void  (*entry)(void);
};

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */

static void *psram_alloc(size_t size)
{
    return heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}

static esp_err_t read_at(FILE *f, long offset, void *buf, size_t len)
{
    if (fseek(f, offset, SEEK_SET) != 0) return ESP_ERR_INVALID_ARG;
    if (fread(buf, 1, len, f) != len)    return ESP_ERR_INVALID_SIZE;
    return ESP_OK;
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
        ESP_LOGE(TAG, "ELF type %u not supported — only ET_REL (1)", hdr->e_type);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (hdr->e_machine != EM_XTENSA) {
        ESP_LOGE(TAG, "ELF machine %u not supported — expected EM_XTENSA (94)",
                 hdr->e_machine);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (hdr->e_shoff == 0 || hdr->e_shnum == 0) {
        ESP_LOGE(TAG, "ELF has no section headers");
        return ESP_ERR_INVALID_ARG;
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Section loading
 * ---------------------------------------------------------------------- */

static const char *shdr_name(const char *shstrtab, const elf32_shdr_t *sh)
{
    return shstrtab + sh->sh_name;
}

static esp_err_t load_sections(FILE *f,
                                const elf32_hdr_t  *hdr,
                                const elf32_shdr_t *shdrs,
                                const char         *shstrtab,
                                duneos_app_t       *app)
{
    app->section_count = hdr->e_shnum;

    for (int i = 0; i < hdr->e_shnum; i++) {
        const elf32_shdr_t *sh = &shdrs[i];
        app->section_bases[i] = NULL;

        if (!(sh->sh_flags & SHF_ALLOC) || sh->sh_size == 0) {
            continue;
        }

        const char *name = shdr_name(shstrtab, sh);
        void **dest_ptr  = NULL;
        size_t *size_ptr = NULL;

        if (strcmp(name, ".text") == 0) {
            dest_ptr = &app->text;
            size_ptr = &app->text_size;
        } else if (strcmp(name, ".data") == 0) {
            dest_ptr = &app->data;
            size_ptr = &app->data_size;
        } else if (strcmp(name, ".bss") == 0) {
            dest_ptr = &app->bss;
            size_ptr = &app->bss_size;
        } else if (strncmp(name, ".rodata", 7) == 0) {
            dest_ptr = &app->rodata;
            size_ptr = &app->rodata_size;
        } else {
            ESP_LOGD(TAG, "  skip section '%s'", name);
            continue;
        }

        *dest_ptr = psram_alloc(sh->sh_size);
        if (!*dest_ptr) {
            ESP_LOGE(TAG, "PSRAM alloc failed for '%s' (%lu B)",
                     name, (unsigned long)sh->sh_size);
            return ESP_ERR_NO_MEM;
        }
        *size_ptr = sh->sh_size;
        app->section_bases[i] = *dest_ptr;

        if (sh->sh_type == SHT_NOBITS) {
            /* .bss: not stored in file — zero-init */
            memset(*dest_ptr, 0, sh->sh_size);
            ESP_LOGD(TAG, "  .bss  %lu B (zeroed)", (unsigned long)sh->sh_size);
        } else {
            esp_err_t err = read_at(f, sh->sh_offset, *dest_ptr, sh->sh_size);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "read failed for section '%s'", name);
                return err;
            }
            ESP_LOGD(TAG, "  %-10s %lu B @ %p", name,
                     (unsigned long)sh->sh_size, *dest_ptr);
        }
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Manifest extraction
 * ---------------------------------------------------------------------- */

static esp_err_t extract_manifest(FILE *f,
                                   const elf32_hdr_t  *hdr,
                                   const elf32_shdr_t *shdrs,
                                   const char         *shstrtab,
                                   duneos_app_manifest_t *out)
{
    for (int i = 0; i < hdr->e_shnum; i++) {
        if (strcmp(shdr_name(shstrtab, &shdrs[i]),
                   DUNEOS_MANIFEST_SECTION) != 0) {
            continue;
        }

        const elf32_shdr_t *sh = &shdrs[i];
        if (sh->sh_size < 2 || sh->sh_size > 4096) {
            ESP_LOGE(TAG, "manifest section size suspicious: %lu",
                     (unsigned long)sh->sh_size);
            return ESP_ERR_INVALID_SIZE;
        }

        char *json = malloc(sh->sh_size + 1);
        if (!json) return ESP_ERR_NO_MEM;

        esp_err_t err = read_at(f, sh->sh_offset, json, sh->sh_size);
        if (err != ESP_OK) { free(json); return err; }
        json[sh->sh_size] = '\0';

        /* Reuse the manifest parser via a thin inline parse */
        /* For now, populate minimal fields from JSON by hand to avoid
         * pulling in cJSON into the loader component.
         * TODO: extract a shared json_parse_manifest() helper. */
        strlcpy(out->name, "unknown", sizeof(out->name));
        out->required_abi_version = 1;

        /* Quick-and-dirty field extraction — good enough until we share cJSON */
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
        ESP_LOGI(TAG, "manifest: name='%s' version='%s' abi>=%lu",
                 out->name, out->version,
                 (unsigned long)out->required_abi_version);
        return ESP_OK;
    }

    ESP_LOGW(TAG, "no " DUNEOS_MANIFEST_SECTION " section — using filename as name");
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Symbol resolution
 * ---------------------------------------------------------------------- */

static void *resolve_symbol(const char *name)
{
    const duneos_symbol_t *table = duneos_symbol_table_get();
    for (const duneos_symbol_t *s = table; s->name != NULL; s++) {
        if (strcmp(s->name, name) == 0) {
            return s->ptr;
        }
    }
    return NULL;
}

/* -------------------------------------------------------------------------
 * Relocations (Phase 3 — stub for now)
 * ---------------------------------------------------------------------- */

static esp_err_t apply_relocations(FILE *f,
                                    const elf32_hdr_t  *hdr,
                                    const elf32_shdr_t *shdrs,
                                    const char         *shstrtab,
                                    const elf32_sym_t  *symtab,
                                    int                 symcount,
                                    const char         *strtab,
                                    duneos_app_t       *app)
{
    (void)f; (void)hdr; (void)shdrs; (void)shstrtab;
    (void)symtab; (void)symcount; (void)strtab; (void)app;

    /*
     * TODO Phase 3: for each SHT_RELA / SHT_REL section:
     *   1. Find the target section (sh_info) and its runtime base address
     *   2. For each relocation entry:
     *      a. Get the symbol (ELF32_R_SYM) — if SHN_UNDEF, resolve via
     *         resolve_symbol() against the kernel export table
     *      b. Compute S (symbol address) and A (addend)
     *      c. Patch memory at (section_base + r_offset) according to type:
     *         R_XTENSA_32:       *(uint32_t*)ptr = S + A
     *         R_XTENSA_SLOT0_OP: decode Xtensa instruction, patch field,
     *                            re-encode — see Xtensa ISA and Flipper Zero
     *                            loader for the per-instruction encoding logic
     *
     * Reference: github.com/flipperdevices/flipperzero-firmware
     *            lib/flipper_application/elf/elf_file.c
     */
    ESP_LOGW(TAG, "relocations not yet applied (Phase 3)");
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

    esp_err_t err = ESP_FAIL;
    elf32_hdr_t  hdr;
    elf32_shdr_t *shdrs   = NULL;
    char         *shstrtab = NULL;
    elf32_sym_t  *symtab   = NULL;
    char         *strtab   = NULL;
    duneos_app_t *app      = NULL;

    /* --- 1. Read and validate ELF header -------------------------------- */
    if (read_at(f, 0, &hdr, sizeof(hdr)) != ESP_OK) {
        ESP_LOGE(TAG, "cannot read ELF header");
        err = ESP_ERR_INVALID_ARG;
        goto out;
    }
    err = elf_validate(&hdr);
    if (err != ESP_OK) goto out;

    ESP_LOGI(TAG, "loading '%s' — %u sections", path, hdr.e_shnum);

    /* --- 2. Read section header table ----------------------------------- */
    if (hdr.e_shnum > 64) {
        ESP_LOGE(TAG, "too many sections: %u", hdr.e_shnum);
        err = ESP_ERR_NOT_SUPPORTED;
        goto out;
    }
    shdrs = malloc(hdr.e_shnum * sizeof(elf32_shdr_t));
    if (!shdrs) { err = ESP_ERR_NO_MEM; goto out; }

    if (read_at(f, hdr.e_shoff, shdrs,
                hdr.e_shnum * sizeof(elf32_shdr_t)) != ESP_OK) {
        ESP_LOGE(TAG, "cannot read section headers");
        err = ESP_ERR_INVALID_ARG;
        goto out;
    }

    /* --- 3. Read section name string table (.shstrtab) ------------------ */
    const elf32_shdr_t *shstr_sh = &shdrs[hdr.e_shstrndx];
    shstrtab = malloc(shstr_sh->sh_size + 1);
    if (!shstrtab) { err = ESP_ERR_NO_MEM; goto out; }
    if (read_at(f, shstr_sh->sh_offset, shstrtab, shstr_sh->sh_size) != ESP_OK) {
        err = ESP_ERR_INVALID_ARG;
        goto out;
    }
    shstrtab[shstr_sh->sh_size] = '\0';

    /* --- 4. Find and read .symtab and .strtab --------------------------- */
    for (int i = 0; i < hdr.e_shnum; i++) {
        if (shdrs[i].sh_type == SHT_SYMTAB) {
            symtab = malloc(shdrs[i].sh_size);
            if (!symtab) { err = ESP_ERR_NO_MEM; goto out; }
            if (read_at(f, shdrs[i].sh_offset, symtab, shdrs[i].sh_size) != ESP_OK) {
                err = ESP_ERR_INVALID_ARG;
                goto out;
            }

            /* .strtab is pointed to by sh_link */
            const elf32_shdr_t *str_sh = &shdrs[shdrs[i].sh_link];
            strtab = malloc(str_sh->sh_size + 1);
            if (!strtab) { err = ESP_ERR_NO_MEM; goto out; }
            if (read_at(f, str_sh->sh_offset, strtab, str_sh->sh_size) != ESP_OK) {
                err = ESP_ERR_INVALID_ARG;
                goto out;
            }
            strtab[str_sh->sh_size] = '\0';

            int symcount = shdrs[i].sh_size / sizeof(elf32_sym_t);
            ESP_LOGD(TAG, "symtab: %d symbols", symcount);
            break;
        }
    }

    /* --- 5. Allocate app descriptor ------------------------------------- */
    app = calloc(1, sizeof(duneos_app_t));
    if (!app) { err = ESP_ERR_NO_MEM; goto out; }

    /* --- 6. Extract embedded manifest ----------------------------------- */
    err = extract_manifest(f, &hdr, shdrs, shstrtab, &app->manifest);
    if (err != ESP_OK) goto out;

    /* ABI version check */
    if (app->manifest.required_abi_version > DUNEOS_ABI_VERSION) {
        ESP_LOGE(TAG, "app requires ABI v%lu, kernel provides v%d",
                 (unsigned long)app->manifest.required_abi_version,
                 DUNEOS_ABI_VERSION);
        err = ESP_ERR_NOT_SUPPORTED;
        goto out;
    }

    /* --- 7. Load sections into PSRAM ------------------------------------ */
    err = load_sections(f, &hdr, shdrs, shstrtab, app);
    if (err != ESP_OK) goto out;

    /* --- 8. Apply relocations (stub — Phase 3) -------------------------- */
    int symcount = symtab ? (int)(shdrs[0].sh_size / sizeof(elf32_sym_t)) : 0;
    /* Re-find symcount properly */
    for (int i = 0; i < hdr.e_shnum; i++) {
        if (shdrs[i].sh_type == SHT_SYMTAB) {
            symcount = shdrs[i].sh_size / sizeof(elf32_sym_t);
            break;
        }
    }
    err = apply_relocations(f, &hdr, shdrs, shstrtab,
                             symtab, symcount, strtab, app);
    if (err != ESP_OK) goto out;

    /* --- 9. Locate app_main --------------------------------------------- */
    if (symtab && strtab) {
        int total_syms = 0;
        for (int i = 0; i < hdr.e_shnum; i++) {
            if (shdrs[i].sh_type == SHT_SYMTAB) {
                total_syms = shdrs[i].sh_size / sizeof(elf32_sym_t);
                break;
            }
        }
        for (int i = 0; i < total_syms; i++) {
            const elf32_sym_t *sym = &symtab[i];
            if (strcmp(strtab + sym->st_name, "app_main") == 0 &&
                sym->st_shndx != SHN_UNDEF) {
                uint8_t *base = app->section_bases[sym->st_shndx];
                if (base) {
                    app->entry = (void (*)(void))(base + sym->st_value);
                    ESP_LOGI(TAG, "app_main @ %p", (void *)app->entry);
                }
                break;
            }
        }
    }

    if (!app->entry) {
        ESP_LOGE(TAG, "app_main not found — relocations not yet applied?");
        /* Non-fatal for now: loader is not yet complete */
    }

    ESP_LOGI(TAG, "load complete: .text=%zuB .data=%zuB .bss=%zuB .rodata=%zuB",
             app->text_size, app->data_size, app->bss_size, app->rodata_size);
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
    if (!app) return ESP_ERR_INVALID_ARG;

    if (!app->entry) {
        ESP_LOGE(TAG, "cannot run: app_main not resolved (Phase 3 incomplete)");
        return ESP_ERR_NOT_SUPPORTED;
    }

    ESP_LOGI(TAG, "jumping to app_main @ %p", (void *)app->entry);
    app->entry();
    return ESP_OK;
}

void duneos_loader_unload(duneos_app_t *app)
{
    if (!app) return;
    heap_caps_free(app->text);
    heap_caps_free(app->data);
    heap_caps_free(app->bss);
    heap_caps_free(app->rodata);
    free(app);
}
