/*
 * test_crash — intentionally exits with code 1 on every launch.
 *
 * Used to validate the supervisor's circuit breaker (Phase 24.7).
 *
 * Drop it into init.yaml with `restart: always`:
 *   services:
 *     - path: /flash/bin/test_crash.dap
 *       restart: always
 *
 * Expected klog after boot:
 *   E ... 'test_crash' exited (code 1) — restarting    [×3]
 *   E ... 'test_crash' exited (code 1) — circuit breaker tripped, restart disabled
 *
 * If the restart storm keeps going past 4 cycles, the breaker is broken.
 */

extern void duneos_exit(int code);

void app_main(void)
{
    duneos_exit(1);
}
