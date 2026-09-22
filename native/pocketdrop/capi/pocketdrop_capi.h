/* C interface to PocketDrop's shared UI (src/ui/ui.h) for hosts in other
 * languages. Image Triage loads it with ctypes (image_triage/pocketdrop).
 *
 * The host owns a surface and forwards input to it; PocketDrop calls back into
 * the host to draw (pd_gfx) and for OS services (pd_shell). Every pd_* call
 * and every callback happens on the host's UI thread, except pd_shell.wake,
 * which PocketDrop's worker threads call to ask the host to run
 * pd_run_posted() on its UI thread soon.
 *
 * Strings are UTF-8. Rectangles are float[4] {left, top, right, bottom} and
 * colours float[4] {r, g, b, a} in 0..1, all in device-independent pixels.
 * Callbacks that return strings or lists write them into an opaque sink with
 * pd_sink_add / pd_sink_set before returning.
 */
#pragma once
#include <stdint.h>

#ifdef _WIN32
#define PD_API __declspec(dllexport)
#else
#define PD_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define PD_ABI_VERSION 1

typedef struct pd_sink pd_sink;
typedef struct pd_host pd_host;

typedef struct pd_gfx {
    void* ctx;
    void (*clear)(void* ctx, const float* color);
    void (*fill_rect)(void* ctx, const float* rect, const float* color);
    void (*fill_round)(void* ctx, const float* rect, float radius, const float* color);
    void (*stroke_round)(void* ctx, const float* rect, float radius, const float* color, float width, int dashed);
    void (*fill_circle)(void* ctx, float x, float y, float radius, const float* color);
    /* Linear from the top-left corner to the bottom-right. */
    void (*gradient_round)(void* ctx, const float* rect, float radius, const float* from, const float* to);
    /* points: x0, y0, x1, y1, ...; round caps and joins. */
    void (*stroke_polyline)(void* ctx, const float* points, int count, int closed, const float* color, float width);
    /* One line, vertically centred in rect, ellipsized to fit. font: see Font in src/ui/gfx.h;
     * align: 0 left, 1 centre, 2 right. */
    void (*text)(void* ctx, const char* text, const float* rect, int font, const float* color, int align);
    float (*measure)(void* ctx, const char* text, int font);
    void (*set_aliased)(void* ctx, int aliased);
    void (*push_clip)(void* ctx, const float* rect);
    void (*pop_clip)(void* ctx);
    /* The system icon for a file or folder. */
    void (*file_icon)(void* ctx, const char* path, const float* rect);
} pd_gfx;

typedef struct pd_shell {
    void* ctx;
    void (*invalidate)(void* ctx);
    /* While on, call pd_tick about every 33 ms; otherwise every 500 ms. */
    void (*set_animating)(void* ctx, int on);
    /* Any thread: work is queued for pd_run_posted. */
    void (*wake)(void* ctx);
    void (*client_size)(void* ctx, float* width, float* height);

    void (*copy_text)(void* ctx, const char* text);
    /* Tightly packed RGBA. Returns nonzero when copied. */
    int (*copy_image)(void* ctx, const uint8_t* rgba, int width, int height);
    /* Clipboard files -> pd_sink_add each path; else an image saved as a PNG -> pd_sink_add;
     * else text -> pd_sink_set. */
    void (*read_clipboard)(void* ctx, pd_sink* sink);
    /* Chosen paths -> pd_sink_add. Nothing added means cancelled. */
    void (*browse)(void* ctx, int folders, pd_sink* sink);
    /* Chosen folder -> pd_sink_set. */
    void (*choose_folder)(void* ctx, const char* title, pd_sink* sink);
    void (*open_url)(void* ctx, const char* url);
    void (*open_bluetooth_setup)(void* ctx);
    /* Returns nonzero to go ahead; sets *dont_show_again. */
    int (*confirm_anywhere_risk)(void* ctx, int* dont_show_again);
    void (*reveal_path)(void* ctx, const char* path);
    void (*attention)(void* ctx);
    /* items: JSON array of {id, label, checked, enabled, separator, submenu}. The menu's
     * top-right corner goes at (x, y). Returns the chosen id or 0. */
    int (*popup_menu)(void* ctx, const char* items_json, float x, float y);
    void (*alert)(void* ctx, const char* title, const char* message);

    int (*load_setting)(void* ctx, const char* key, int fallback);
    void (*save_setting)(void* ctx, const char* key, int value);
    /* Value -> pd_sink_set; leave the sink empty to use the fallback. */
    void (*load_string)(void* ctx, const char* key, pd_sink* sink);
    void (*save_string)(void* ctx, const char* key, const char* value);
    void (*set_topmost)(void* ctx, int on);
    /* Absolute path without trailing separators -> pd_sink_set; nothing if it doesn't exist. */
    void (*clean_path)(void* ctx, const char* path, pd_sink* sink);
    /* The top-level native window (HWND on Windows) for OS dialogs such as the share sheet, or 0. */
    intptr_t (*native_window)(void* ctx);
} pd_shell;

PD_API int pd_abi_version(void);

/* The callback tables are copied. Returns NULL on failure. */
PD_API pd_host* pd_create(const pd_shell* shell, const pd_gfx* gfx);
PD_API void pd_start(pd_host* host);
/* Stops sharing and every background thread, then frees the host. */
PD_API void pd_destroy(pd_host* host);

/* scale: device pixels per DIP. */
PD_API void pd_paint(pd_host* host, float scale);
PD_API void pd_tick(pd_host* host);
PD_API void pd_run_posted(pd_host* host);

PD_API void pd_mouse_move(pd_host* host, float x, float y);
PD_API void pd_mouse_leave(pd_host* host);
PD_API void pd_mouse_down(pd_host* host, float x, float y);
PD_API void pd_mouse_up(pd_host* host, float x, float y);
/* Positive scrolls toward the top, in lines. */
PD_API void pd_wheel(pd_host* host, float lines);
PD_API int pd_wants_pointer(pd_host* host);

PD_API void pd_set_drag_over(pd_host* host, int on);
PD_API void pd_add_paths(pd_host* host, const char* const* paths, int count);
PD_API void pd_add_text(pd_host* host, const char* text);
PD_API void pd_paste(pd_host* host);
PD_API void pd_browse(pd_host* host, int folders);
PD_API void pd_copy_link(pd_host* host);
PD_API void pd_share_link(pd_host* host);
PD_API void pd_refresh_network(pd_host* host);

PD_API void pd_sink_add(pd_sink* sink, const char* text);
PD_API void pd_sink_set(pd_sink* sink, const char* text);

#ifdef __cplusplus
}
#endif
