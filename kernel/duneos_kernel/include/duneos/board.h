#pragma once

/*
 * <duneos/board.h> — public app-facing header for board-specific defines.
 *
 * Implementation note: there is no static content here. dbt build generates
 * a per-app `_board.h` in the app's build directory (with the right
 * DUNEOS_DISPLAY_DEV, DUNEOS_INPUT_DEV, dimensions, etc. for the active
 * board) and adds that directory to the include path. This header pulls
 * the generated one.
 *
 * Apps therefore write `#include <duneos/board.h>` and never see the
 * board's name in their source code. Moving the same app from CardPuter
 * (ST7789, /dev/disp0) to a future board (different chip on /dev/spi-2)
 * requires no source change — only the regenerated _board.h carries the
 * new device path.
 *
 * Defined as a header alias rather than a system header so that:
 *   - the SDK include path (kernel/duneos_kernel/include) carries it;
 *   - apps that don't actually need a board-specific define can still
 *     include this header without breaking on boards where boardgen
 *     hasn't run (the include simply fails at compile time with a
 *     clear "_board.h not found" message).
 *
 * See ADR 015 Pattern 2.
 */

#include "_board.h"
