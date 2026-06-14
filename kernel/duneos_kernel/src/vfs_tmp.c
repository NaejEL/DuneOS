/*
 * DuneOS tmpfs — RAM-backed ephemeral filesystem mounted at /tmp.
 *
 * Flat namespace (no subdirectories). Files live in heap-allocated buffers
 * that grow on write. All state is protected by a single mutex — /tmp is
 * shared across apps and accessed from any task.
 *
 * Limits are intentionally small: MCU has no swap.
 */

#include "duneos/vfs.h"

#include "esp_vfs.h"
#include "duneos/klog.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <dirent.h>

#define TMPFS_MAX_FILES  16
#define TMPFS_MAX_FDS    16
#define TMPFS_NAME_MAX   64

static const char *TAG = "duneos/tmpfs";

/* ----- inode ------------------------------------------------------------- */

typedef struct {
    char     name[TMPFS_NAME_MAX];
    uint8_t *data;
    size_t   size;
    size_t   cap;
    bool     used;
} tmpfs_inode_t;

/* ----- open file descriptor ---------------------------------------------- */

typedef struct {
    tmpfs_inode_t *inode;
    size_t         pos;
    int            oflags;
    bool           open;
} tmpfs_fd_t;

static tmpfs_inode_t     s_inodes[TMPFS_MAX_FILES];
static tmpfs_fd_t        s_fds[TMPFS_MAX_FDS];
static SemaphoreHandle_t s_lock;

/* ----- internal helpers -------------------------------------------------- */

static tmpfs_inode_t *inode_find(const char *name)
{
    for (int i = 0; i < TMPFS_MAX_FILES; i++) {
        if (s_inodes[i].used && strcmp(s_inodes[i].name, name) == 0)
            return &s_inodes[i];
    }
    return NULL;
}

static tmpfs_inode_t *inode_alloc(const char *name)
{
    for (int i = 0; i < TMPFS_MAX_FILES; i++) {
        if (!s_inodes[i].used) {
            memset(&s_inodes[i], 0, sizeof(s_inodes[i]));
            strlcpy(s_inodes[i].name, name, TMPFS_NAME_MAX);
            s_inodes[i].used = true;
            return &s_inodes[i];
        }
    }
    return NULL;
}

static int fd_alloc(void)
{
    for (int i = 0; i < TMPFS_MAX_FDS; i++) {
        if (!s_fds[i].open) return i;
    }
    return -1;
}

/* Path convention: ESP-IDF strips the mount prefix, leaving "/filename". */
static const char *strip_slash(const char *path)
{
    return (path[0] == '/') ? path + 1 : path;
}

/* ----- VFS callbacks ----------------------------------------------------- */

static int tmpfs_open(const char *path, int flags, int mode)
{
    (void)mode;
    const char *name = strip_slash(path);

    xSemaphoreTake(s_lock, portMAX_DELAY);

    tmpfs_inode_t *inode = inode_find(name);

    if (!inode) {
        if (!(flags & O_CREAT)) {
            xSemaphoreGive(s_lock);
            errno = ENOENT;
            return -1;
        }
        inode = inode_alloc(name);
        if (!inode) {
            xSemaphoreGive(s_lock);
            errno = ENOSPC;
            return -1;
        }
    } else if ((flags & O_CREAT) && (flags & O_EXCL)) {
        xSemaphoreGive(s_lock);
        errno = EEXIST;
        return -1;
    }

    if (flags & O_TRUNC) {
        free(inode->data);
        inode->data = NULL;
        inode->size = 0;
        inode->cap  = 0;
    }

    int fd = fd_alloc();
    if (fd < 0) {
        xSemaphoreGive(s_lock);
        errno = EMFILE;
        return -1;
    }

    s_fds[fd].inode  = inode;
    s_fds[fd].pos    = (flags & O_APPEND) ? inode->size : 0;
    s_fds[fd].oflags = flags;
    s_fds[fd].open   = true;

    xSemaphoreGive(s_lock);
    return fd;
}

static int tmpfs_close(int fd)
{
    if (fd < 0 || fd >= TMPFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    s_fds[fd].open  = false;
    s_fds[fd].inode = NULL;
    return 0;
}

static ssize_t tmpfs_read(int fd, void *buf, size_t len)
{
    if (fd < 0 || fd >= TMPFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    tmpfs_fd_t    *f     = &s_fds[fd];
    tmpfs_inode_t *inode = f->inode;

    xSemaphoreTake(s_lock, portMAX_DELAY);

    size_t avail = (f->pos < inode->size) ? (inode->size - f->pos) : 0;
    if (len > avail) len = avail;
    if (len > 0) {
        memcpy(buf, inode->data + f->pos, len);
        f->pos += len;
    }

    xSemaphoreGive(s_lock);
    return (ssize_t)len;
}

static ssize_t tmpfs_write(int fd, const void *buf, size_t len)
{
    if (fd < 0 || fd >= TMPFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    tmpfs_fd_t    *f     = &s_fds[fd];
    tmpfs_inode_t *inode = f->inode;

    xSemaphoreTake(s_lock, portMAX_DELAY);

    size_t end = f->pos + len;
    if (end > inode->cap) {
        /* Grow with 25% headroom to amortise reallocs */
        size_t newcap = end + (end >> 2) + 64;
        uint8_t *p = realloc(inode->data, newcap);
        if (!p) {
            xSemaphoreGive(s_lock);
            errno = ENOMEM;
            return -1;
        }
        inode->data = p;
        inode->cap  = newcap;
    }

    memcpy(inode->data + f->pos, buf, len);
    f->pos += len;
    if (f->pos > inode->size) inode->size = f->pos;

    xSemaphoreGive(s_lock);
    return (ssize_t)len;
}

static off_t tmpfs_lseek(int fd, off_t offset, int whence)
{
    if (fd < 0 || fd >= TMPFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    tmpfs_fd_t    *f     = &s_fds[fd];
    tmpfs_inode_t *inode = f->inode;

    off_t newpos;
    switch (whence) {
    case SEEK_SET: newpos = offset;                       break;
    case SEEK_CUR: newpos = (off_t)f->pos + offset;      break;
    case SEEK_END: newpos = (off_t)inode->size + offset;  break;
    default:       errno = EINVAL; return (off_t)-1;
    }
    if (newpos < 0) { errno = EINVAL; return (off_t)-1; }
    f->pos = (size_t)newpos;
    return newpos;
}

static int tmpfs_fstat(int fd, struct stat *st)
{
    if (fd < 0 || fd >= TMPFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    memset(st, 0, sizeof(*st));
    st->st_mode = S_IFREG | 0666;
    st->st_size = (off_t)s_fds[fd].inode->size;
    return 0;
}

static int tmpfs_stat(const char *path, struct stat *st)
{
    const char *name = strip_slash(path);
    if (name[0] == '\0') {                 /* the mount root "/tmp" itself */
        memset(st, 0, sizeof(*st));
        st->st_mode = S_IFDIR | 0777;
        return 0;
    }
    tmpfs_inode_t *inode = inode_find(name);
    if (!inode) { errno = ENOENT; return -1; }
    memset(st, 0, sizeof(*st));
    st->st_mode = S_IFREG | 0666;
    st->st_size = (off_t)inode->size;
    return 0;
}

static int tmpfs_unlink(const char *path)
{
    const char *name = strip_slash(path);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    tmpfs_inode_t *inode = inode_find(name);
    if (!inode) {
        xSemaphoreGive(s_lock);
        errno = ENOENT;
        return -1;
    }
    free(inode->data);
    memset(inode, 0, sizeof(*inode));
    xSemaphoreGive(s_lock);
    return 0;
}

static int tmpfs_rename(const char *src, const char *dst)
{
    const char *sname = strip_slash(src);
    const char *dname = strip_slash(dst);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    tmpfs_inode_t *inode = inode_find(sname);
    if (!inode) {
        xSemaphoreGive(s_lock);
        errno = ENOENT;
        return -1;
    }
    strlcpy(inode->name, dname, TMPFS_NAME_MAX);
    xSemaphoreGive(s_lock);
    return 0;
}

/* /tmp is a flat namespace; mkdir/rmdir are no-ops (succeed silently) */
static int tmpfs_mkdir(const char *path, mode_t mode)
{
    (void)path; (void)mode;
    return 0;
}

static int tmpfs_rmdir(const char *path)
{
    (void)path;
    return 0;
}

/* ----- directory iteration ----------------------------------------------- */

typedef struct {
    int scan_idx;
} tmpfs_dir_ctx_t;

static DIR *tmpfs_opendir(const char *path)
{
    (void)path;
    tmpfs_dir_ctx_t *ctx = calloc(1, sizeof(tmpfs_dir_ctx_t));
    if (!ctx) { errno = ENOMEM; return NULL; }
    return (DIR *)ctx;
}

static struct dirent *tmpfs_readdir(DIR *pdir)
{
    tmpfs_dir_ctx_t *ctx = (tmpfs_dir_ctx_t *)pdir;
    static struct dirent ent;

    while (ctx->scan_idx < TMPFS_MAX_FILES) {
        int i = ctx->scan_idx++;
        if (s_inodes[i].used) {
            memset(&ent, 0, sizeof(ent));
            ent.d_ino  = (ino_t)(i + 1);
            ent.d_type = DT_REG;
            strlcpy(ent.d_name, s_inodes[i].name, sizeof(ent.d_name));
            return &ent;
        }
    }
    return NULL;
}

static int tmpfs_closedir(DIR *pdir)
{
    free(pdir);
    return 0;
}

/* ----- mount ------------------------------------------------------------- */

esp_err_t duneos_vfs_mount_tmp(void)
{
    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;

    static const esp_vfs_t vfs = {
        .flags    = ESP_VFS_FLAG_DEFAULT,
        .open     = tmpfs_open,
        .close    = tmpfs_close,
        .read     = tmpfs_read,
        .write    = tmpfs_write,
        .lseek    = tmpfs_lseek,
        .fstat    = tmpfs_fstat,
        .stat     = tmpfs_stat,
        .unlink   = tmpfs_unlink,
        .rename   = tmpfs_rename,
        .mkdir    = tmpfs_mkdir,
        .rmdir    = tmpfs_rmdir,
        .opendir  = tmpfs_opendir,
        .readdir  = tmpfs_readdir,
        .closedir = tmpfs_closedir,
    };

    esp_err_t err = esp_vfs_register("/tmp", &vfs, NULL);
    if (err != ESP_OK) {
        klog_e(TAG, "esp_vfs_register failed: %s", esp_err_to_name(err));
        vSemaphoreDelete(s_lock);
        return err;
    }

    klog_i(TAG, "/tmp ready (%d file slots, heap-backed)", TMPFS_MAX_FILES);
    return ESP_OK;
}
