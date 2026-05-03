#include "duneos/abi.h"

#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <dirent.h>
#include <pthread.h>
#include <semaphore.h>

#include "duneos/task.h"

/*
 * Kernel export symbol table — the ABI contract between DuneOS and apps.
 *
 * Design rules:
 *  - Export POSIX primitives, not implementation details.
 *  - No FreeRTOS symbols (vTaskDelay, xQueue*, etc.) — apps must not know
 *    the kernel runs on FreeRTOS. Use POSIX equivalents instead.
 *  - No printf: newlib routes printf → _write() → write(1,...) → VFS.
 *    Apps get proper I/O through write/read. The kernel controls where
 *    app stdout goes (terminal, pipe, buffer) via fd redirection.
 *  - sprintf/snprintf are pure (no I/O) and safe to export.
 *  - Memory: export malloc/free/realloc from the app PSRAM heap, not the
 *    kernel heap. (TODO Phase 3: replace with heap_caps_malloc wrappers
 *    that allocate from a per-app PSRAM region.)
 *
 * Adding a symbol is backwards-compatible.
 * Removing or renaming one requires a DUNEOS_ABI_VERSION bump.
 */

void duneos_exit(int code);

static const duneos_symbol_t s_symbol_table[] = {

    /* ------------------------------------------------------------------ */
    /* POSIX — file I/O                                                    */
    /* ------------------------------------------------------------------ */
    { "open",       open       },
    { "close",      close      },
    { "read",       read       },
    { "write",      write      },
    { "lseek",      lseek      },
    { "fstat",      fstat      },
    { "stat",       stat       },
    { "unlink",     unlink     },
    { "rename",     rename     },

    /* ------------------------------------------------------------------ */
    /* POSIX — directory                                                   */
    /* ------------------------------------------------------------------ */
    { "opendir",    opendir    },
    { "readdir",    readdir    },
    { "closedir",   closedir   },

    /* ------------------------------------------------------------------ */
    /* POSIX — memory                                                      */
    /* (TODO: replace with wrappers that enforce per-app PSRAM budget)    */
    /* ------------------------------------------------------------------ */
    { "malloc",     malloc     },
    { "free",       free       },
    { "realloc",    realloc    },
    { "calloc",     calloc     },

    /* ------------------------------------------------------------------ */
    /* POSIX — threads & synchronisation                                   */
    /* ------------------------------------------------------------------ */
    { "pthread_create",           pthread_create           },
    { "pthread_join",             pthread_join             },
    { "pthread_exit",             pthread_exit             },
    { "pthread_self",             pthread_self             },
    { "pthread_mutex_init",       pthread_mutex_init       },
    { "pthread_mutex_destroy",    pthread_mutex_destroy    },
    { "pthread_mutex_lock",       pthread_mutex_lock       },
    { "pthread_mutex_trylock",    pthread_mutex_trylock    },
    { "pthread_mutex_unlock",     pthread_mutex_unlock     },
    { "sem_init",                 sem_init                 },
    { "sem_destroy",              sem_destroy              },
    { "sem_wait",                 sem_wait                 },
    { "sem_trywait",              sem_trywait              },
    { "sem_post",                 sem_post                 },

    /* ------------------------------------------------------------------ */
    /* POSIX — time                                                        */
    /* ------------------------------------------------------------------ */
    { "clock_gettime",  clock_gettime  },
    { "gettimeofday",   gettimeofday   },
    { "nanosleep",      nanosleep      },

    /* ------------------------------------------------------------------ */
    /* String & memory utilities (pure — no I/O, safe to call anywhere)   */
    /* ------------------------------------------------------------------ */
    { "memcpy",     memcpy     },
    { "memmove",    memmove    },
    { "memset",     memset     },
    { "memcmp",     memcmp     },
    { "strlen",     strlen     },
    { "strnlen",    strnlen    },
    { "strcpy",     strcpy     },
    { "strncpy",    strncpy    },
    { "strlcpy",    strlcpy    },
    { "strcat",     strcat     },
    { "strncat",    strncat    },
    { "strcmp",     strcmp     },
    { "strncmp",    strncmp    },
    { "strchr",     strchr     },
    { "strrchr",    strrchr    },
    { "strstr",     strstr     },
    { "strtol",     strtol     },
    { "strtoul",    strtoul    },
    { "atoi",       atoi       },
    { "sprintf",    sprintf    },
    { "snprintf",   snprintf   },
    { "sscanf",     sscanf     },

    /* ------------------------------------------------------------------ */
    /* DuneOS lifecycle                                                    */
    /* ------------------------------------------------------------------ */
    { "duneos_exit", duneos_exit },

    /* Sentinel */
    { NULL, NULL },
};

const duneos_symbol_t *duneos_symbol_table_get(void)
{
    return s_symbol_table;
}

/*
 * Apps call duneos_exit() to terminate cleanly.
 * TODO Phase 3: signal supervisor task via task notification, then suspend.
 */
void duneos_exit(int code)
{
    (void)code;
    vTaskSuspend(NULL);
}
