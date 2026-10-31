/*
 * msx_core.h -- the C form of the py-msx-emulator core API.
 *
 * This header is the contract a native core implements and a host calls
 * (openspec/specs/machine-c-abi, allium/core-frontend.allium). It mirrors
 * msx/core_api.py's CoreApi: one msx_core_<member> function per member, with
 * the member's name, and msx/boundary.py's records and constants.
 * tests/test_core_c_header.py keeps the names and the constants equal to the
 * Python boundary.
 *
 * Ownership and threads:
 *   - Bytes the core returns are owned by the core. A frame output's video
 *     and audio stay valid until the next step_frame, reset or load_state on
 *     the same core. Other bytes (msx_bytes, and the pointer drain_trace
 *     returns) stay valid until the next call on the same core. Both end when
 *     the core is destroyed. A host that keeps them longer copies them.
 *   - Bytes and structs the host passes in are read only during the call;
 *     the core keeps no pointer to them.
 *   - One msx_core is used from one thread at a time.
 *
 * Conventions:
 *   - Every member function takes the core first.
 *   - A member that returns a status returns int32_t, one of MSX_STATUS_*.
 *     After a failure, msx_core_last_error_text gives the UTF-8 message.
 *   - Integer arguments are int32_t, so -1 can mean "none" where a member
 *     says so. Booleans are int32_t 0 or 1; a setter returns
 *     MSX_STATUS_BAD_ARGUMENT for any other value.
 *   - Lists with a fixed limit are written to a caller array, and the
 *     function returns the count.
 */
#ifndef MSX_CORE_H
#define MSX_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MSX_CORE_ABI_VERSION 1

/* -- Statuses (every control operation) ---------------------------------- */
#define MSX_STATUS_OK 0
#define MSX_STATUS_STATE_REJECTED 1
#define MSX_STATUS_NO_DRIVE 2
#define MSX_STATUS_BAD_IMAGE 3
#define MSX_STATUS_BAD_CONFIG 4
#define MSX_STATUS_NO_MEDIUM 5
#define MSX_STATUS_BAD_ARGUMENT 6

/* -- Stop reasons (msx_stop_info.reason, msx_frame_output.stop_reason) ---- */
#define MSX_STOP_NONE 0
#define MSX_STOP_BREAKPOINT 1
#define MSX_STOP_WATCHPOINT 2
#define MSX_STOP_REQUESTED 3

/* -- Break settings ------------------------------------------------------- */
#define MSX_MAX_BREAKPOINTS 4
#define MSX_MAX_WATCHPOINTS 4
#define MSX_WATCH_READ 1
#define MSX_WATCH_WRITE 2

/* -- Input ----------------------------------------------------------------- */
#define MSX_MOUSE_LEFT 1
#define MSX_MOUSE_RIGHT 2

/* -- Trace kinds (bit flags) and trace event kinds ------------------------- */
#define MSX_TRACE_VDP 0x01
#define MSX_TRACE_MAPPER 0x02
#define MSX_TRACE_DEBUG 0x04
#define MSX_TRACE_EVENT_VDP_PORT99 1
#define MSX_TRACE_EVENT_VDP_PORT9B 2
#define MSX_TRACE_EVENT_MAP_BANK 3
#define MSX_TRACE_EVENT_LOST 4
#define MSX_TRACE_EVENT_IO_READ 5
#define MSX_TRACE_EVENT_IO_WRITE 6
#define MSX_TRACE_EVENT_VDP_MODE_REG 7
#define MSX_TRACE_EVENT_UNDEFINED_OP 8
#define MSX_TRACE_EVENT_CPU_HISTORY 9
#define MSX_TRACE_EVENT_HANG_PC_LOOP 10
#define MSX_TRACE_EVENT_HANG_HALT_DI 11
#define MSX_TRACE_HISTORY_SIZE 64
#define MSX_TRACE_CAPACITY 65536
#define MSX_TRACE_LOST_MAX 0x7FFFFFFF
#define MSX_AUDIO_SAMPLE_RATE 44100
#define MSX_OUTPUT_HEIGHT 212

/* -- Media ids -------------------------------------------------------------- */
#define MSX_MEDIA_SLOT1_SRAM 0
#define MSX_MEDIA_SLOT2_SRAM 1
#define MSX_MEDIA_SLOT1_SUB0_SRAM 2
#define MSX_MEDIA_SLOT1_SUB1_SRAM 3
#define MSX_MEDIA_SLOT1_SUB2_SRAM 4
#define MSX_MEDIA_SLOT1_SUB3_SRAM 5
#define MSX_MEDIA_SLOT2_SUB0_SRAM 6
#define MSX_MEDIA_SLOT2_SUB1_SRAM 7
#define MSX_MEDIA_SLOT2_SUB2_SRAM 8
#define MSX_MEDIA_SLOT2_SUB3_SRAM 9
#define MSX_MEDIA_FMPAC_SRAM 10
#define MSX_MEDIA_RTC_CMOS 11
#define MSX_MEDIA_DRIVE_A 12
#define MSX_MEDIA_DRIVE_B 13
#define MSX_MEDIA_DRIVE_C 14
#define MSX_MEDIA_DRIVE_D 15
#define MSX_MEDIA_COUNT 16
#define MSX_FMPAC_SRAM_SIZE 8192
#define MSX_RTC_CMOS_SIZE 26
#define MSX_HALNOTE_SRAM_SIZE 16384
#define MSX_ASCII8_SRAM2_SIZE 2048
#define MSX_ASCII8_SRAM8_SIZE 8192
#define MSX_ASCII16_SRAM2_SIZE 2048
#define MSX_ASCII16_SRAM8_SIZE 8192
#define MSX_KOEI_SRAM32_SIZE 32768
#define MSX_GAME_MASTER2_SRAM_SIZE 8192

/* -- Machine properties ----------------------------------------------------- */
#define MSX_VIDEO_NTSC 0
#define MSX_VIDEO_PAL 1
#define MSX_KEYBOARD_INTERNATIONAL 0
#define MSX_KEYBOARD_JAPANESE 1
#define MSX_VDP_KIND_TMS9918A 0
#define MSX_VDP_KIND_V9938 1

/* -- Slot description (msx_slot_info) -------------------------------------- */
#define MSX_SLOT_CONTENT_EMPTY 0
#define MSX_SLOT_CONTENT_MAIN_ROM 1
#define MSX_SLOT_CONTENT_SUB_ROM 2
#define MSX_SLOT_CONTENT_CARTRIDGE 3
#define MSX_SLOT_CONTENT_CARTRIDGE_EMPTY 4
#define MSX_SLOT_CONTENT_RAM 5
#define MSX_SLOT_CONTENT_RAM_MAPPER 6
#define MSX_SLOT_CONTENT_FDC 7
#define MSX_BANK_NONE 0
#define MSX_BANK_WINDOW_16K 1
#define MSX_BANK_WINDOWS_8K 2
#define MSX_BANK_SEGMENT 3
#define MSX_BANK_SRAM_PAGE 4
#define MSX_WINDOW_NONE 0
#define MSX_WINDOW_PAGE 1
#define MSX_WINDOW_SRAM 2
#define MSX_WINDOW_OPEN 3
#define MSX_MAPPER_FLAT 0
#define MSX_MAPPER_FIXED_PAGE 1
#define MSX_MAPPER_ASCII8 2
#define MSX_MAPPER_ASCII16 3
#define MSX_MAPPER_ASCII8_SRAM2 4
#define MSX_MAPPER_ASCII8_SRAM8 5
#define MSX_MAPPER_KOEI_SRAM32 6
#define MSX_MAPPER_GAME_MASTER2 7
#define MSX_MAPPER_ASCII16_SRAM2 8
#define MSX_MAPPER_ASCII16_SRAM8 9
#define MSX_MAPPER_R_TYPE 10
#define MSX_MAPPER_KONAMI 11
#define MSX_MAPPER_MAJUTSUSHI 12
#define MSX_MAPPER_KONAMI_SCC 13
#define MSX_MAPPER_SCC_I_CART 14
#define MSX_MAPPER_HALNOTE 15
#define MSX_MAPPER_FMPAC 16
#define MSX_MAPPER_RAM_MAPPER 17

/* -- Values ----------------------------------------------------------------- */

typedef struct msx_core msx_core;

/* Bytes the core returns: see "Ownership and threads" above. */
typedef struct msx_bytes {
    const uint8_t *data;
    size_t len;
} msx_bytes;

typedef struct msx_rtc_time {
    int32_t year;
    int32_t month;
    int32_t day;
    int32_t hour;
    int32_t minute;
    int32_t second;
} msx_rtc_time;

typedef struct msx_rom_image {
    char sha1[41]; /* 40 lowercase hex digits and a NUL */
    const uint8_t *data;
    size_t len;
} msx_rom_image;

typedef struct msx_media_image {
    int32_t media_id; /* MSX_MEDIA_*: an SRAM id or MSX_MEDIA_RTC_CMOS */
    const uint8_t *data;
    size_t len;
} msx_media_image;

typedef struct msx_disk_mount {
    int32_t drive; /* 0 = A */
    const uint8_t *data;
    size_t len;
    int32_t write_protected;
} msx_disk_mount;

typedef struct msx_machine_config {
    const uint8_t *document; /* the canonical MachineConfig JSON document */
    size_t document_len;
    const msx_rom_image *roms;
    size_t rom_count;
    const msx_media_image *media;
    size_t media_count;
    const msx_disk_mount *disks;
    size_t disk_count;
    msx_rtc_time rtc_initial_time; /* always given; Python's default is 2000-01-01 */
} msx_machine_config;

typedef struct msx_input_frame {
    uint8_t key_matrix[11]; /* active-low rows 0-10 */
    int32_t joy1;           /* active-low, the core uses bits 0-5 */
    int32_t joy2;
    int32_t mouse_dx;       /* MSX counts; the core clamps to -32768..32767 */
    int32_t mouse_dy;
    int32_t mouse_buttons;  /* MSX_MOUSE_LEFT | MSX_MOUSE_RIGHT */
} msx_input_frame;

typedef struct msx_frame_output {
    int32_t video_width;   /* 256 or 512; 0 when the frame was not rendered */
    int32_t video_height;  /* 212; 0 when the frame was not rendered */
    msx_bytes video_pixels; /* RGB24, width * height * 3 bytes */
    msx_bytes audio;       /* signed 16-bit little-endian mono PCM */
    int64_t frame_number;
    int32_t stop_reason;   /* MSX_STOP_* */
    int32_t frame_complete; /* 0 when the frame continues on the next call */
} msx_frame_output;

typedef struct msx_rgb_frame {
    int32_t width;
    int32_t height;
    msx_bytes pixels;
} msx_rgb_frame;

typedef struct msx_stop_info {
    int32_t reason; /* MSX_STOP_* */
    int32_t pc;
    int32_t watch_addr;
    int32_t watch_value;
    int32_t watch_access; /* MSX_WATCH_READ or MSX_WATCH_WRITE */
} msx_stop_info;

typedef struct msx_watchpoint {
    int32_t address;
    int32_t kind; /* MSX_WATCH_READ, MSX_WATCH_WRITE or both */
} msx_watchpoint;

typedef struct msx_cpu_registers {
    int32_t AF, BC, DE, HL;
    int32_t AF_, BC_, DE_, HL_;
    int32_t IX, IY, SP, PC;
    int32_t I, R;
    int32_t IFF1, IFF2, IM;
} msx_cpu_registers;

typedef struct msx_vdp_info {
    int32_t kind;          /* MSX_VDP_KIND_* */
    uint8_t regs[28];      /* 8 on the TMS9918A, R#0-R#27 on the V9938 */
    int32_t regs_len;
    uint8_t cmd_regs[15];  /* R#32-R#46; empty on the TMS9918A */
    int32_t cmd_regs_len;
    uint8_t status[10];    /* S#0, or S#0-S#9 on the V9938 */
    int32_t status_len;
    uint16_t palette[16];  /* 9-bit entries; empty on the TMS9918A */
    int32_t palette_len;
    int32_t cmd_x, cmd_y, cmd_nx, cmd_ny, cmd_remaining;
} msx_vdp_info;

typedef struct msx_slot_layout {
    int32_t primary;      /* the port 0xA8 value */
    int32_t expanded;     /* bit n: primary slot n is expanded */
    uint8_t secondary[4]; /* each slot's secondary register, 0 if not expanded */
} msx_slot_layout;

typedef struct msx_slot_info {
    int32_t content;          /* MSX_SLOT_CONTENT_*; -1 for a rejected value */
    int32_t mapper;           /* MSX_MAPPER_* for a cartridge, else -1 */
    int32_t size_kb;          /* 0 when no size applies; -1 for a rejected value */
    int32_t bank;             /* MSX_BANK_* */
    int32_t window_kind[2];   /* MSX_WINDOW_* */
    int32_t window_value[2];  /* page index or SRAM offset */
    int32_t sub_bank_count;   /* 0 or 2 */
    int32_t sub_bank[2];
    int32_t sub_offset[2];    /* ROM offset of each sub-bank */
} msx_slot_info;

typedef struct msx_trace_event {
    int32_t kind; /* MSX_TRACE_EVENT_* */
    int64_t cycle;
    int64_t frame;
    int32_t pc;
    int32_t a, b, c, d;
} msx_trace_event;

/* -- Construction ----------------------------------------------------------- */

int32_t msx_core_abi_version(void);

/* MSX_STATUS_OK with *out_core set and *error_len 0, or MSX_STATUS_BAD_CONFIG
 * with *out_core NULL. The UTF-8 message goes to the caller's buffer: at most
 * error_cap bytes, no NUL, and *error_len is its full length, so a caller can
 * retry with a larger buffer. The core keeps no state outside an msx_core.
 * Every rejection is a status: a device size a device does not accept, and
 * an RTC start time outside year 0-9999 or not a real date and time. */
int32_t msx_core_create(const msx_machine_config *config, msx_core **out_core,
                        char *error_text, size_t error_cap, size_t *error_len);
void msx_core_destroy(msx_core *core);

/* -- Machine properties ----------------------------------------------------- */

int64_t msx_core_cycle_count(msx_core *core);
int32_t msx_core_video_standard(msx_core *core);   /* MSX_VIDEO_* */
int32_t msx_core_keyboard_layout(msx_core *core);  /* MSX_KEYBOARD_* */
int32_t msx_core_samples_per_frame(msx_core *core);

/* -- Running ------------------------------------------------------------------ */

void msx_core_step_frame(msx_core *core, const msx_input_frame *frame_input,
                         int32_t render_video, msx_frame_output *out);
int32_t msx_core_apply_input(msx_core *core, const msx_input_frame *frame_input);
int32_t msx_core_step(msx_core *core); /* the T-states of the instruction */
int32_t msx_core_reset(msx_core *core);

/* -- Media -------------------------------------------------------------------- */

int32_t msx_core_dirty_media(msx_core *core, int32_t out_ids[MSX_MEDIA_COUNT]);
/* media_id may be any integer: an id the machine does not have gives empty
 * bytes / MSX_STATUS_NO_MEDIUM. */
msx_bytes msx_core_read_media(msx_core *core, int32_t media_id); /* empty when absent */
int32_t msx_core_mark_persisted(msx_core *core, int32_t media_id);
/* write_protected 0 or 1, else MSX_STATUS_BAD_ARGUMENT and no change. */
int32_t msx_core_mount_disk(msx_core *core, int32_t drive, const uint8_t *data,
                            size_t len, int32_t write_protected);
int32_t msx_core_eject_disk(msx_core *core, int32_t drive);
int32_t msx_core_disk_mounted(msx_core *core, int32_t drive);

/* -- Save states -------------------------------------------------------------- */

msx_bytes msx_core_save_state(msx_core *core);
int32_t msx_core_load_state(msx_core *core, const uint8_t *data, size_t len);
msx_bytes msx_core_last_error_text(msx_core *core);

/* -- Attachments -------------------------------------------------------------- */

/* Once, before the first step_frame and the first load_state; port 0 = Joy1,
 * 1 = Joy2. There is no detach. */
int32_t msx_core_attach_mouse(msx_core *core, int32_t port);

/* -- Break settings ----------------------------------------------------------- */

int32_t msx_core_set_breakpoints(msx_core *core, const int32_t *addrs, size_t count);
int32_t msx_core_set_watchpoints(msx_core *core, const msx_watchpoint *entries,
                                 size_t count);
int32_t msx_core_set_temp_breakpoint(msx_core *core, int32_t addr);   /* -1 clears */
int32_t msx_core_set_step_out(msx_core *core, int32_t sp);
int32_t msx_core_set_frame_breakpoint(msx_core *core, int64_t frame); /* -1 clears */
int32_t msx_core_set_break_halt_di(msx_core *core, int32_t enabled);
int32_t msx_core_set_sprites_hidden(msx_core *core, int32_t hidden);
int32_t msx_core_set_trace_kinds(msx_core *core, int32_t kinds);
int32_t msx_core_set_sp_range(msx_core *core, int32_t low, int32_t high); /* -1, -1 clears */

/* -- Break control ------------------------------------------------------------ */

int32_t msx_core_request_break(msx_core *core); /* safe from a signal handler */
void msx_core_last_stop(msx_core *core, msx_stop_info *out);

/* -- Inspection queries ------------------------------------------------------- */

void msx_core_cpu_registers(msx_core *core, msx_cpu_registers *out);
/* length 0-0x10000, else empty bytes and an error text; addr wraps at 0xFFFF. */
msx_bytes msx_core_read_memory(msx_core *core, int32_t addr, int32_t length);
int32_t msx_core_write_memory(msx_core *core, int32_t addr, const uint8_t *data,
                              size_t len);
int32_t msx_core_vram_size(msx_core *core);
/* length 0 to vram_size, else empty bytes and an error text; addr wraps by
 * addr & (vram_size - 1). */
msx_bytes msx_core_read_vram(msx_core *core, int32_t addr, int32_t length);
void msx_core_vdp_info(msx_core *core, msx_vdp_info *out);
int64_t msx_core_frame_number(msx_core *core);
int32_t msx_core_drive_count(msx_core *core);
void msx_core_main_ram_range(msx_core *core, int32_t *low, int32_t *high);
void msx_core_slot_layout(msx_core *core, msx_slot_layout *out);
/* secondary and page: -1 for none. */
void msx_core_slot_info(msx_core *core, int32_t primary, int32_t secondary,
                        int32_t page, msx_slot_info *out);
int32_t msx_core_breakpoints(msx_core *core, int32_t out[MSX_MAX_BREAKPOINTS]);
int32_t msx_core_watchpoints(msx_core *core, msx_watchpoint out[MSX_MAX_WATCHPOINTS]);
int32_t msx_core_break_halt_di(msx_core *core);
int32_t msx_core_sprites_hidden(msx_core *core);
int32_t msx_core_trace_kinds(msx_core *core);
int32_t msx_core_trace_sources(msx_core *core);
const msx_trace_event *msx_core_drain_trace(msx_core *core, size_t *count);
void msx_core_render_preview(msx_core *core, msx_rgb_frame *out);

#ifdef __cplusplus
}
#endif

#endif /* MSX_CORE_H */
