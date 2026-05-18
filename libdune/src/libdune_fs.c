/*
 * libdune — file-system wrappers (ABI v3).
 *
 * Every POSIX I/O call dispatches through __duneos_api_ptr->fs so that:
 *   - read() buffers are validated as app-writable before the kernel touches them.
 *   - write() buffers pass a permissive check (may point to kernel string data).
 *   - All operations route through the kernel VFS regardless of which libc
 *     the app was compiled against.
 *
 * Varargs functions (open, fcntl, ioctl, dprintf) extract the optional
 * argument before forwarding a concrete call to the non-varargs API pointer.
 */

#include <duneos/api.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>   /* vsnprintf */

extern const duneos_api_t *__duneos_api_ptr;

/* -------------------------------------------------------------------------
 * File operations
 * ---------------------------------------------------------------------- */

int open(const char *path, int flags, ...)
{
    int mode = 0;
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, int);
        va_end(ap);
    }
    return __duneos_api_ptr->fs.open(path, flags, mode);
}

int close(int fd)
{
    return __duneos_api_ptr->fs.close(fd);
}

ssize_t read(int fd, void *buf, size_t n)
{
    return __duneos_api_ptr->fs.read(fd, buf, n);
}

ssize_t write(int fd, const void *buf, size_t n)
{
    return __duneos_api_ptr->fs.write(fd, buf, n);
}

off_t lseek(int fd, off_t off, int whence)
{
    return __duneos_api_ptr->fs.lseek(fd, off, whence);
}

int fstat(int fd, struct stat *st)
{
    return __duneos_api_ptr->fs.fstat(fd, st);
}

int stat(const char *path, struct stat *st)
{
    return __duneos_api_ptr->fs.stat(path, st);
}

int unlink(const char *path)
{
    return __duneos_api_ptr->fs.unlink(path);
}

int rename(const char *old_path, const char *new_path)
{
    return __duneos_api_ptr->fs.rename(old_path, new_path);
}

int dup(int fd)
{
    return __duneos_api_ptr->fs.dup(fd);
}

int dup2(int fd, int newfd)
{
    return __duneos_api_ptr->fs.dup2(fd, newfd);
}

int fcntl(int fd, int cmd, ...)
{
    /* Extract the optional third argument.  The most common commands
     * (F_GETFL, F_SETFL, F_DUPFD, F_SETFD, F_GETFD) all take an int. */
    int arg = 0;
    va_list ap;
    va_start(ap, cmd);
    arg = va_arg(ap, int);
    va_end(ap);
    return __duneos_api_ptr->fs.fcntl(fd, cmd, arg);
}

int ioctl(int fd, unsigned long req, ...)
{
    void *arg = NULL;
    va_list ap;
    va_start(ap, req);
    arg = va_arg(ap, void *);
    va_end(ap);
    return __duneos_api_ptr->fs.ioctl(fd, req, arg);
}

/* dprintf: not in PicoLibc — implement via vsnprintf + write (which itself
 * routes through the API table's validated write path).                     */
int dprintf(int fd, const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) write(fd, buf, (size_t)n);
    return n;
}

/* -------------------------------------------------------------------------
 * Directory operations
 * ---------------------------------------------------------------------- */

DIR *opendir(const char *path)
{
    return (DIR *)__duneos_api_ptr->fs.opendir(path);
}

struct dirent *readdir(DIR *dir)
{
    return (struct dirent *)__duneos_api_ptr->fs.readdir((void *)dir);
}

int closedir(DIR *dir)
{
    return __duneos_api_ptr->fs.closedir((void *)dir);
}

int mkdir(const char *path, mode_t mode)
{
    return __duneos_api_ptr->fs.mkdir(path, (unsigned)mode);
}

int rmdir(const char *path)
{
    return __duneos_api_ptr->fs.rmdir(path);
}
