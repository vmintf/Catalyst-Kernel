// Catalyst Kernel - A bare-metal UEFI kernel using Python DSL and Zig comptime AOT compilation.
// Copyright (C) 2026  Skystarry.xyz
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// See LICENSE for details.

const std = @import("std");
const uefi = std.os.uefi;
const BlockIo = uefi.protocol.BlockIo;

pub const OpCode = enum(u8) {
    // 0x01 – data
    literal       = 0x01,

    // 0x10–0x1F – I/O
    write_serial  = 0x10,
    write_str     = 0x11, // runtime string write; no comptime branch cost
    write_console = 0x12, // write single byte to UEFI con_out
    write_con_str = 0x13, // write ASCII string to UEFI con_out
    clear_screen  = 0x14, // clear UEFI con_out and reset cursor; ANSI on serial
    write_line    = 0x15, // write line_buf[0..line_len] to serial + con_out

    // 0x20–0x2F – bitwise
    bit_and       = 0x20, // left & right
    bit_or        = 0x21, // left | right
    bit_xor       = 0x22, // left ^ right
    bit_not       = 0x23, // ~operand (single operand)
    bit_shl       = 0x24, // left << (right & 7)
    bit_shr       = 0x25, // left >> (right & 7)

    // 0x30–0x3F – arithmetic + comparison
    add_u32       = 0x30,
    sub_u32       = 0x31,
    mul_u32       = 0x32,
    div_u32       = 0x33,
    mod_u32       = 0x34,
    cmp_eq        = 0x35, // 1 if left == right, else 0
    cmp_lt        = 0x36, // 1 if left <  right, else 0
    cmp_gt        = 0x37, // 1 if left >  right, else 0
    cmp_neq       = 0x38, // 1 if left != right, else 0
    cmp_gte       = 0x39, // 1 if left >= right, else 0
    cmp_lte       = 0x3A, // 1 if left <= right, else 0

    // 0x40–0x4F – memory + stack
    mem_write     = 0x40,
    mem_read      = 0x41,
    read_port     = 0x42, // runtime inb; result echoed to serial
    mem_index     = 0x43, // read byte at base_addr + index expression
    write_port    = 0x44, // runtime outb; acquires hardware control
    push          = 0x45, // push expression result onto the scratch stack
    pop           = 0x46, // pop top of scratch stack (discard)
    mem_copy      = 0x47, // bulk byte copy: src_addr -> dst_addr, count bytes
    dup           = 0x48, // duplicate top of scratch stack
    swap          = 0x49, // swap top two entries on scratch stack
    map_page      = 0x4A, // map physical page into virtual address space
    unmap_page    = 0x4B, // unmap virtual page
    get_mem_map   = 0x4C, // read UEFI memory map into scratch buffer

    // 0x50–0x5F – control flow
    loop          = 0x50,
    jmp           = 0x51,
    jmp_if_zero   = 0x52,
    jmp_if_eq     = 0x53,
    jmp_if_lt     = 0x54,

    // 0x60–0x6F – keyboard / console input
    poll_key      = 0x60, // block until a key is pressed, echo to serial + console
    read_line     = 0x61, // read a line until Enter, echo chars, handle backspace

    // 0x70–0x7F – interrupt control
    int_cli       = 0x70, // disable interrupts (cli)
    int_sti       = 0x71, // enable interrupts (sti)
    int_n         = 0x72, // software interrupt: int imm8

    // 0xFF – system
    halt          = 0xff,
    _,
};

// ---------------------------------------------------------------------------
// Serial (COM1, 0x3F8, 115200 8N1)
// ---------------------------------------------------------------------------

pub const Serial = struct {
    const PORT: u16 = 0x3F8;

    /// Initialise the 8250/16550 UART at COM1 (0x3F8, 115200 8N1).
    /// Must be called once before any write().
    pub fn init() void {
        outb(PORT + 1, 0x00); // Disable all interrupts.
        outb(PORT + 3, 0x80); // Enable DLAB to access baud rate divisor.
        outb(PORT + 0, 0x01); // Divisor low byte: 1 -> 115200 baud.
        outb(PORT + 1, 0x00); // Divisor high byte.
        outb(PORT + 3, 0x03); // 8N1; clears DLAB.
        outb(PORT + 2, 0xC7); // Enable + clear FIFO, 14-byte threshold.
        outb(PORT + 4, 0x03); // RTS + DTR asserted.
    }

    /// Block until the transmit holding register is empty, then send one byte.
    pub fn write(data: u8) void {
        while (inb(PORT + 5) & 0x20 == 0) {} // Wait for THRE (bit 5 of LSR).
        outb(PORT, data);
    }

    inline fn outb(port: u16, data: u8) void {
        asm volatile ("outb %[data], %[port]"
            :
            : [data] "{al}" (data),
              [port] "{dx}" (port)
        );
    }

    inline fn inb(port: u16) u8 {
        return asm volatile ("inb %[port], %[ret]"
            : [ret] "={al}" (-> u8)
            : [port] "{dx}" (port)
        );
    }
};


// ---------------------------------------------------------------------------
// Global line buffer – accessible from IR via read_line's buf_addr field.
// The kernel exposes a single 256-byte scratch buffer at a fixed address.
// ---------------------------------------------------------------------------

pub var line_buf: [256]u8 = undefined;
pub var line_len: usize   = 0;

// ---------------------------------------------------------------------------
// Scratch stack – used by push/pop opcodes for temporary value storage.
// A fixed 64-entry stack is sufficient for the DSL's bit-manipulation idioms.
// ---------------------------------------------------------------------------

const SCRATCH_STACK_DEPTH = 64;
var scratch_stack: [SCRATCH_STACK_DEPTH]u8 = undefined;
var scratch_sp: usize = 0; // index of next free slot (grows upward)

// ---------------------------------------------------------------------------
// Console (UEFI Simple Text Output)
// ---------------------------------------------------------------------------

pub const Console = struct {
    /// Write a single ASCII byte to the UEFI con_out.
    /// Converts to a null-terminated UTF-16LE buffer on the stack.
    pub fn write_byte(ch: u8) void {
        const con_out = uefi.system_table.con_out orelse return;
        // Stack buffer: [char, null-terminator] in UTF-16LE (2 bytes each).
        var buf = [_:0]u16{ ch };
        _ = con_out.outputString(&buf) catch {};
    }

    /// Write a slice of ASCII bytes to the UEFI con_out one character at a time.
    pub fn write_str(s: []const u8) void {
        for (s) |ch| {
            write_byte(ch);
        }
    }

    /// Clear the UEFI con_out and reset the cursor to (0, 0).
    /// Uses the Simple Text Output clearScreen() protocol call, which works
    /// correctly regardless of whether the firmware supports ANSI sequences.
    pub fn clear() void {
        const con_out = uefi.system_table.con_out orelse return;
        _ = con_out.clearScreen() catch {};
    }
};

// ---------------------------------------------------------------------------
// Keyboard – UEFI Simple Text Input Protocol
//
// Using UEFI con_in instead of direct PS/2 port I/O avoids firmware and
// chipset compatibility issues across different QEMU machine types.
// ---------------------------------------------------------------------------

pub const Keyboard = struct {
    /// Block until a printable key is pressed via UEFI con_in.
    /// Returns the ASCII byte of the pressed key (guaranteed non-zero).
    pub fn read_ascii() u8 {
        const con_in = uefi.system_table.con_in orelse return 0;
        const bs     = uefi.system_table.boot_services orelse return 0;

        // Create a single-event wait array for WaitForEvent.
        var events = [_]uefi.Event{con_in.wait_for_key};

        while (true) {
            // Block until the key event fires.
            _ = bs.waitForEvent(&events) catch continue;

            // readKeyStroke returns Key.Input directly (0.15.x API).
            const key = con_in.readKeyStroke() catch continue;

            // unicode_char holds the printable character; scan_code is for
            // special keys (arrows, F-keys, etc.) which we ignore for now.
            const ch: u8 = @truncate(key.unicode_char);
            if (ch >= 0x20 and ch <= 0x7E) return ch; // printable ASCII range
            if (ch == 0x08 or ch == 0x7F) return 0x08;  // backspace + DEL → unified as BS
            if (ch == '\r') return '\n';                // normalise Enter
        }
    }
};

// ---------------------------------------------------------------------------
// Expression evaluator (runtime)
//
// Reads opcodes from *ir* starting at *pc and returns a u8 result.
// cmp_* opcodes return 0 or 1.  pc is advanced past all consumed bytes.
// ---------------------------------------------------------------------------

fn evaluate_ir(ir: []const u8, pc: *usize) u8 {
    const op: OpCode = @enumFromInt(ir[pc.*]);
    pc.* += 1;

    switch (op) {
        .literal => {
            const val = ir[pc.*];
            pc.* += 1;
            return val;
        },
        .add_u32 => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return left +% right;
        },
        .sub_u32 => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return left -% right;
        },
        .mul_u32 => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return left *% right;
        },
        .div_u32 => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            if (right == 0) return 0; // runtime: treat div-by-zero as 0
            return left / right;
        },
        .mod_u32 => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            if (right == 0) return 0;
            return left % right;
        },
        .cmp_eq => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return if (left == right) 1 else 0;
        },
        .cmp_lt => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return if (left < right) 1 else 0;
        },
        .cmp_gt => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return if (left > right) 1 else 0;
        },
        .cmp_neq => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return if (left != right) 1 else 0;
        },
        .cmp_gte => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return if (left >= right) 1 else 0;
        },
        .cmp_lte => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return if (left <= right) 1 else 0;
        },
        // Bitwise – all operators consume two sub-expressions except bit_not.
        .bit_and => {
        const left  = evaluate_ir(ir, pc);
        const right = evaluate_ir(ir, pc);
        return left & right;
    },
        .bit_or => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return left | right;
        },
        .bit_xor => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return left ^ right;
        },
        .bit_not => {
            const operand = evaluate_ir(ir, pc);
            return ~operand;
        },
        .bit_shl => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            // Mask shift amount to 0–7 to avoid undefined behaviour on u8.
            return left << @truncate(right & 7);
        },
        .bit_shr => {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return left >> @truncate(right & 7);
        },
        // Stack peek helpers – dup/swap are usable as expression nodes so
        // that DSL authors can read the scratch stack inside evaluate_ir.
        .dup => {
        // Return a copy of the top without consuming it.
            if (scratch_sp > 0) return scratch_stack[scratch_sp - 1];
        return 0;
    },
        .swap => {
            // Swap top two entries; return the new top (formerly second).
            if (scratch_sp >= 2) {
                const a = scratch_stack[scratch_sp - 1];
                const b = scratch_stack[scratch_sp - 2];
                scratch_stack[scratch_sp - 1] = b;
                scratch_stack[scratch_sp - 2] = a;
                return b;
            }
            return 0;
        },
        .mem_index => {
            // Encoding: [mem_index] [base_addr: u32 LE] [index node bytes]
            // Reads one byte from the global line_buf at position *index*.
            // base_addr is reserved for future use (0 = line_buf).
            const base = std.mem.readInt(u32, ir[pc.*..][0..4], .little);
            pc.* += 4;
            const idx = evaluate_ir(ir, pc);
            _ = base; // reserved – currently always reads from line_buf
            if (idx < line_buf.len) return line_buf[idx];
            return 0;
        },

        else => return 0,
    }
}

// ---------------------------------------------------------------------------
// Top-level IR executor (runtime)
// ---------------------------------------------------------------------------

pub fn execute_python_ir(ir_data: []const u8) void {
    var pc: usize = 0;

    while (pc < ir_data.len) {
        const op: OpCode = @enumFromInt(ir_data[pc]);
        switch (op) {

        // ----------------------------------------------------------------
        // I/O – serial write (single byte expression)
        // ----------------------------------------------------------------

            .write_serial => {
                pc += 1;
                const val = evaluate_ir(ir_data, &pc);
                Serial.write(val);
            },

            // ----------------------------------------------------------------
            // I/O – serial write string (runtime, no comptime branch cost)
            //
            // Encoding: [write_str] [length: u16 LE] [bytes...]
            // ----------------------------------------------------------------

            .write_str => {
                pc += 1;
                const len = std.mem.readInt(u16, ir_data[pc..][0..2], .little);
                pc += 2;
                for (ir_data[pc..pc + len]) |byte| {
                    Serial.write(byte);
                }
                pc += len;
            },


            // ----------------------------------------------------------------
            // I/O – UEFI console write (single byte)
            //
            // Encoding: [write_console] [value node bytes]
            // ----------------------------------------------------------------

            .write_console => {
                pc += 1;
                const val = evaluate_ir(ir_data, &pc);
                Console.write_byte(val);
            },

            // ----------------------------------------------------------------
            // I/O – UEFI console write string
            //
            // Encoding: [write_con_str] [length: u16 LE] [bytes...]
            // ----------------------------------------------------------------

            .write_con_str => {
                pc += 1;
                const len = std.mem.readInt(u16, ir_data[pc..][0..2], .little);
                pc += 2;
                Console.write_str(ir_data[pc..pc + len]);
                pc += len;
            },

            // ----------------------------------------------------------------
            // I/O – clear screen (clear_screen)
            //
            // Encoding: [clear_screen]  (no operands)
            //
            // Clears the UEFI con_out via the Simple Text Output clearScreen()
            // protocol call, which resets the cursor to (0, 0) and fills the
            // display with the current background attribute.  ANSI CSI 2J + H
            // is also sent to serial so that a connected terminal emulator
            // mirrors the clear.
            // ----------------------------------------------------------------

            .clear_screen => {
                pc += 1;
                // UEFI side: use the protocol's native clear, not ANSI escapes.
                Console.clear();
                // Serial side: standard ANSI erase-display + cursor-home.
                for ("\x1B[2J\x1B[H") |ch| Serial.write(ch);
            },

            // ----------------------------------------------------------------
            // I/O – write line buffer (write_line)
            //
            // Encoding: [write_line]  (no operands)
            //
            // Writes line_buf[0..line_len] to both serial and UEFI con_out,
            // followed by a CR+LF pair.  Intended for echo and similar commands
            // that want to reflect the most recently read input line without
            // re-encoding it in the IR byte stream.
            // ----------------------------------------------------------------

            .write_line => {
                pc += 1;
                // Write the live content of line_buf up to line_len.
                for (line_buf[0..line_len]) |ch| {
                    Serial.write(ch);
                    Console.write_byte(ch);
                }
                // Terminate with CR+LF to match the shell's newline convention.
                Serial.write('\r');
                Serial.write('\n');
                Console.write_str("\r\n");
            },

            .mem_write => {
                pc += 1;
                const addr = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const val = evaluate_ir(ir_data, &pc);
                @as(*volatile u8, @ptrFromInt(addr)).* = val;
            },

            .mem_read => {
                pc += 1;
                const addr = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                _ = @as(*volatile u8, @ptrFromInt(addr)).*;
            },

            // ----------------------------------------------------------------
            // I/O – runtime port read
            //
            // Encoding: [read_port] [port: u16 LE]
            // ----------------------------------------------------------------

            .read_port => {
                pc += 1;
                const port = std.mem.readInt(u16, ir_data[pc..][0..2], .little);
                pc += 2;
                const val = asm volatile ("inb %[port], %[ret]"
                    : [ret] "={al}" (-> u8)
                    : [port] "{dx}" (@as(u16, port))
                );
                Serial.write(val);
            },

            // ----------------------------------------------------------------
            // I/O – runtime port write (write_port)
            //
            // Encoding: [write_port] [port: u16 LE] [value node bytes]
            //
            // Executes a single x86 outb instruction, transferring hardware
            // control to the device at *port*.  This is required before any
            // device-specific register sequence (e.g. PIC remapping, PIT
            // programming) to establish ownership of that I/O port range.
            // ----------------------------------------------------------------

            .write_port => {
                pc += 1;
                const port = std.mem.readInt(u16, ir_data[pc..][0..2], .little);
                pc += 2;
                const val = evaluate_ir(ir_data, &pc);
                asm volatile ("outb %[data], %[port]"
                    :
                    : [data] "{al}" (val),
                      [port] "{dx}" (@as(u16, port))
                );
            },

            // ----------------------------------------------------------------
            // Stack – push
            //
            // Encoding: [push] [value node bytes]
            //
            // Evaluates *value* and pushes the result onto the scratch stack.
            // Silently drops the value when the stack is full, preventing a
            // kernel panic at the cost of a lost intermediate result.  DSL
            // authors should keep push/pop pairs balanced.
            // ----------------------------------------------------------------

            .push => {
                pc += 1;
                const val = evaluate_ir(ir_data, &pc);
                if (scratch_sp < SCRATCH_STACK_DEPTH) {
                    scratch_stack[scratch_sp] = val;
                    scratch_sp += 1;
                }
                // Silently discard if overflow; avoids a fatal fault in kernel.
            },

            // ----------------------------------------------------------------
            // Stack – pop
            //
            // Encoding: [pop]
            //
            // Pops the top byte off the scratch stack and discards it.  A pop
            // on an empty stack is a no-op; the DSL emitter is responsible for
            // balanced usage.  The popped value is intentionally not forwarded
            // to Serial/Console so that pop acts as a pure cleanup instruction.
            // ----------------------------------------------------------------

            .pop => {
                pc += 1;
                if (scratch_sp > 0) {
                    scratch_sp -= 1;
                }
                // No-op on underflow; consistent with push overflow policy.
            },

            // ----------------------------------------------------------------
            // Stack – dup
            //
            // Encoding: [dup]
            //
            // Pushes a copy of the current top byte onto the scratch stack.
            // Safe no-op when the stack is empty or full (overflow policy
            // mirrors push: silently discard rather than fault).
            // ----------------------------------------------------------------

            .dup => {
                pc += 1;
                if (scratch_sp > 0 and scratch_sp < SCRATCH_STACK_DEPTH) {
                    scratch_stack[scratch_sp] = scratch_stack[scratch_sp - 1];
                    scratch_sp += 1;
                }
            },

            // ----------------------------------------------------------------
            // Stack – swap
            //
            // Encoding: [swap]
            //
            // Exchanges the top two bytes on the scratch stack in-place.
            // No-op when fewer than two entries are present.
            // ----------------------------------------------------------------

            .swap => {
                pc += 1;
                if (scratch_sp >= 2) {
                    const a = scratch_stack[scratch_sp - 1];
                    scratch_stack[scratch_sp - 1] = scratch_stack[scratch_sp - 2];
                    scratch_stack[scratch_sp - 2] = a;
                }
            },

            // ----------------------------------------------------------------
            // Memory – mem_copy
            //
            // Encoding: [mem_copy] [dst_addr: u32 LE] [src_addr: u32 LE]
            //                      [count: u32 LE]
            //
            // Copies *count* bytes from *src_addr* to *dst_addr* using a
            // volatile byte loop so that the compiler does not elide or reorder
            // accesses to MMIO regions (e.g. the VGA frame buffer at 0xB8000).
            // Overlapping regions are not supported; use two sequential copies
            // if source and destination windows may overlap.
            // ----------------------------------------------------------------

            .mem_copy => {
                pc += 1;
                const dst_addr = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const src_addr = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const count    = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                var i: u32 = 0;
                while (i < count) : (i += 1) {
                    // Volatile accesses prevent the optimizer from collapsing
                    // this loop when src or dst are MMIO-mapped addresses.
                    const byte = @as(*volatile u8, @ptrFromInt(src_addr + i)).*;
                    @as(*volatile u8, @ptrFromInt(dst_addr + i)).* = byte;
                }
            },

            // ----------------------------------------------------------------
            // Control flow – loop
            //
            // Encoding: [loop] [count: u32 LE] [body_len: u32 LE] [body...]
            // ----------------------------------------------------------------

            .loop => {
                pc += 1;
                const count    = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const body_len = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                var i: u32 = 0;
                while (i < count) : (i += 1) {
                    execute_python_ir(ir_data[pc..pc + body_len]);
                }
                pc += body_len;
            },

            // ----------------------------------------------------------------
            // Control flow – unconditional jump
            // ----------------------------------------------------------------

            .jmp => {
                pc += 1;
                const offset = std.mem.readInt(i32, ir_data[pc..][0..4], .little);
                pc += 4;
                pc = @intCast(@as(i64, @intCast(pc)) + offset);
            },

            // ----------------------------------------------------------------
            // Control flow – conditional jumps
            // ----------------------------------------------------------------

            .jmp_if_zero => {
                pc += 1;
                const val    = evaluate_ir(ir_data, &pc);
                const offset = std.mem.readInt(i32, ir_data[pc..][0..4], .little);
                pc += 4;
                if (val == 0) {
                    pc = @intCast(@as(i64, @intCast(pc)) + offset);
                }
            },

            .jmp_if_eq => {
                pc += 1;
                const left   = evaluate_ir(ir_data, &pc);
                const right  = evaluate_ir(ir_data, &pc);
                const offset = std.mem.readInt(i32, ir_data[pc..][0..4], .little);
                pc += 4;
                if (left == right) {
                    pc = @intCast(@as(i64, @intCast(pc)) + offset);
                }
            },

            .jmp_if_lt => {
                pc += 1;
                const left   = evaluate_ir(ir_data, &pc);
                const right  = evaluate_ir(ir_data, &pc);
                const offset = std.mem.readInt(i32, ir_data[pc..][0..4], .little);
                pc += 4;
                if (left < right) {
                    pc = @intCast(@as(i64, @intCast(pc)) + offset);
                }
            },

            // ----------------------------------------------------------------
            // Keyboard – poll_key
            // ----------------------------------------------------------------

            .poll_key => {
                pc += 1;
                const ascii = Keyboard.read_ascii();
                if (ascii != 0) {
                    Serial.write(ascii);
                    Console.write_byte(ascii);
                }
            },


            // ----------------------------------------------------------------
            // Keyboard – read_line
            //
            // Encoding: [read_line] [buf_addr: u32 LE] [max_len: u16 LE]
            //
            // Reads characters from UEFI con_in until Enter is pressed,
            // echoing each printable character to both serial and con_out.
            // Backspace erases the last character from the display and buffer.
            // The null-terminated result is written into the kernel's static
            // scratch buffer (buf_addr=0 uses the internal line_buf).
            // ----------------------------------------------------------------

            .read_line => {
                pc += 1;
                _ = std.mem.readInt(u32, ir_data[pc..][0..4], .little); // buf_addr reserved
                pc += 4;
                const max_len = std.mem.readInt(u16, ir_data[pc..][0..2], .little);
                pc += 2;

                // Write result into the global line_buf.
                const limit = @min(@as(usize, max_len), line_buf.len - 1);
                line_len = 0;

                while (true) {
                    const ch = Keyboard.read_ascii();
                    if (ch == 0) continue;

                    if (ch == '\n') {
                        // Enter pressed – commit the line.
                        break;
                    } else if (ch == 8) {
                        // Backspace – erase last character if any.
                        if (line_len > 0) {
                            line_len -= 1;
                            Serial.write(8);
                            Serial.write(' ');
                            Serial.write(8);
                            Console.write_str("\x08 \x08");
                        }
                    } else if (line_len < limit) {
                        line_buf[line_len] = ch;
                        line_len += 1;
                        Serial.write(ch);
                        Console.write_byte(ch);
                    }
                }
                line_buf[line_len] = 0;
            },

            // ----------------------------------------------------------------
            // Interrupt control – int_cli / int_sti / int_n
            //
            // int_cli and int_sti map directly to the x86 cli/sti instructions.
            // They must be used with care: leaving interrupts disabled across a
            // long IR sequence will block all hardware IRQs.
            //
            // int_n encoding: [int_n] [vector: u8]
            // Fires a software interrupt via the `int imm8` instruction.
            // Useful for BIOS legacy calls and testing IDT entries during
            // early kernel bring-up before ExitBootServices.
            // ----------------------------------------------------------------

            .int_cli => {
                pc += 1;
                asm volatile ("cli");
            },

            .int_sti => {
                pc += 1;
                asm volatile ("sti");
            },

            .int_n => {
                pc += 1;
                const vector = ir_data[pc];
                pc += 1;
                // Zig does not support variable int imm8 directly; use a
                // look-up over the common vectors used in early OS bring-up.
                // Extend this table as new vectors are needed.
                switch (vector) {
                    0x03 => asm volatile ("int $0x03"), // breakpoint
                    0x04 => asm volatile ("int $0x04"), // overflow
                    0x10 => asm volatile ("int $0x10"), // BIOS video (legacy)
                    0x13 => asm volatile ("int $0x13"), // BIOS disk  (legacy)
                    0x15 => asm volatile ("int $0x15"), // BIOS misc  (legacy)
                    else => {},                          // unsupported – no-op
                }
            },

            // ----------------------------------------------------------------
            // Memory management – map_page / unmap_page / get_mem_map
            //
            // These are thin wrappers over UEFI Boot Services memory calls.
            // They are only valid before ExitBootServices; once the firmware
            // hands off memory ownership, the DSL must manage mappings itself
            // using mem_write/mem_read on the page tables directly.
            //
            // map_page encoding:   [map_page]   [phys: u32 LE] [virt: u32 LE]
            // unmap_page encoding: [unmap_page] [virt: u32 LE]
            // get_mem_map encoding:[get_mem_map] [buf_addr: u32 LE] [buf_size: u32 LE]
            // ----------------------------------------------------------------

            .map_page => {
                // Allocate one 4 KiB page of loader data via UEFI pool allocator.
                // *phys* is treated as a hint recorded in the virt slot so the
                // DSL can track the intended physical target; actual placement is
                // decided by the firmware (AllocateAnyPages policy).
                // Physical-address-fixed allocation (AllocateAddress) requires the
                // raw function-pointer ABI that changed in 0.15.x and is better
                // handled post-ExitBootServices via direct page-table writes.
                pc += 1;
                const phys = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const virt = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const bs = uefi.system_table.boot_services orelse break;
                const page_size: usize = 0x1000;
                const buf = bs.allocatePool(.loader_data, page_size) catch break;
                // allocatePool returns []align(8) u8 in Zig 0.15.x; extract
                // the raw address via .ptr before passing to @intFromPtr.
                // Write the firmware-assigned address into the virt slot so the
                // DSL can read it back via mem_read and build its own page tables.
                @as(*volatile u32, @ptrFromInt(virt)).* = @truncate(@intFromPtr(buf.ptr));
                _ = phys; // physical hint – used by DSL for page table mapping
            },

            .unmap_page => {
                pc += 1;
                const virt = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const bs = uefi.system_table.boot_services orelse break;
                // Read back the pointer that map_page stored at the virt slot.
                // freePool requires [*]align(8) u8; the allocatePool guarantee
                // ensures the stored address satisfies that alignment.
                const raw_addr: usize = @as(*volatile u32, @ptrFromInt(virt)).*;
                const ptr: [*]align(8) u8 = @ptrFromInt(raw_addr);
                bs.freePool(ptr) catch {};
            },

            .get_mem_map => {
                // Read the UEFI memory map into a DSL-managed buffer.
                // Encoding: [get_mem_map] [buf_addr: u32 LE] [buf_size: u32 LE]
                //
                // Writes raw MemoryDescriptor entries starting at buf_addr.
                // The DSL is responsible for allocating a buffer large enough;
                // 4 KiB covers typical firmware maps (~40 entries * ~48 bytes).
                //
                // Zig 0.15.x API: getMemoryMap takes a single
                // []align(@alignOf(MemoryDescriptor)) u8 buffer and returns
                // a MemoryMapSlice.  We reconstruct the slice from the raw
                // DSL-managed address and size operands.
                pc += 1;
                const buf_addr = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const buf_size = std.mem.readInt(u32, ir_data[pc..][0..4], .little);
                pc += 4;
                const bs = uefi.system_table.boot_services orelse break;
                // @alignCast is required because @ptrFromInt yields *u8
                // with unknown alignment; we assert the DSL caller has placed
                // buf_addr on a MemoryDescriptor-aligned boundary.
                const raw: [*]align(@alignOf(uefi.tables.MemoryDescriptor)) u8 =
                    @alignCast(@as([*]u8, @ptrFromInt(buf_addr)));
                const buf: []align(@alignOf(uefi.tables.MemoryDescriptor)) u8 =
                    raw[0..buf_size];
                _ = bs.getMemoryMap(buf) catch {};
            },

            // ----------------------------------------------------------------
            // Halt
            // ----------------------------------------------------------------

            .halt => {
                while (true) {
                    asm volatile ("hlt");
                }
            },

            else => {
                pc += 1;
            },
        }
    }
}


// ---------------------------------------------------------------------------
// El Torito CD-ROM boot helper
// ---------------------------------------------------------------------------

const ElToritoCatalogEntry = extern struct {
    boot_indicator: u8,
    media_type:     u8,
    load_segment:   u16,
    system_type:    u8,
    _unused:        u8,
    sector_count:   u16,
    load_lba:       u32,
    _pad:           [20]u8,
};

pub fn findAndBootCdrom() void {
    const bs = uefi.system_table.boot_services orelse return;

    const handles = (bs.locateHandleBuffer(
        .{ .by_protocol = &BlockIo.guid },
    ) catch return) orelse return;

    for (handles) |handle| {
        const bio = (bs.openProtocol(
            BlockIo,
            handle,
            .{ .by_handle_protocol = .{} },
        ) catch continue) orelse continue;

        if (bio.media.block_size != 2048) continue;
        if (!bio.media.media_present) continue;

        var sector: [2048]u8 align(8) = undefined;
        bio.readBlocks(bio.media.media_id, 17, &sector) catch continue;

        if (sector[0] != 0x00) continue;
        if (!std.mem.eql(u8, sector[1..6], "CD001")) continue;

        const catalog_lba = std.mem.readInt(u32, sector[71..][0..4], .little);

        var catalog: [2048]u8 align(8) = undefined;
        bio.readBlocks(bio.media.media_id, catalog_lba, &catalog) catch continue;

        const entry: *ElToritoCatalogEntry = @ptrCast(&catalog[32]);
        if (entry.boot_indicator != 0x88) continue;

        Serial.write('C');

        const image_size = @as(usize, entry.sector_count) * 2048;
        const image_buf  = bs.allocatePool(.loader_data, image_size) catch continue;

        bio.readBlocks(bio.media.media_id, entry.load_lba, image_buf) catch continue;

        const loaded = bs.loadImage(
            false,
            uefi.handle,
            .{ .buffer = image_buf },
        ) catch continue;

        _ = bs.startImage(loaded) catch continue;
        break;
    }
}