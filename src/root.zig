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
    literal      = 0x01,
    write_serial = 0x10,
    add_u32      = 0x30,
    sub_u32      = 0x31,
    mul_u32      = 0x32,
    div_u32      = 0x33,
    mod_u32      = 0x34,
    mem_write    = 0x40,
    mem_read     = 0x41,
    loop         = 0x50,
    jmp          = 0x51,
    jmp_if_zero  = 0x52,
    jmp_if_eq    = 0x53,
    jmp_if_lt    = 0x54,
    halt         = 0xff,
    _,
};

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
// Expression evaluator (runs at comptime, returns a u8 result)
// ---------------------------------------------------------------------------

fn evaluate_ir(comptime ir: []const u8, comptime pc: *usize) u8 {
    const op = @as(OpCode, @enumFromInt(ir[pc.*]));
    pc.* += 1;

    return switch (op) {
        .literal => blk: {
            const val = ir[pc.*];
            pc.* += 1;
            break :blk val;
        },

        .add_u32 => blk: {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            break :blk left +% right;
        },

        .sub_u32 => blk: {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            break :blk left -% right;
        },

        .mul_u32 => blk: {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            break :blk left *% right;
        },

        .div_u32 => blk: {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            // Trap division-by-zero at compile time.
            if (right == 0) @compileError("IR div_u32: division by zero");
            break :blk left / right;
        },

        .mod_u32 => blk: {
            const left  = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            if (right == 0) @compileError("IR mod_u32: modulo by zero");
            break :blk left % right;
        },

        else => @panic("unknown opcode in expression context"),
    };
}

// ---------------------------------------------------------------------------
// Top-level IR executor (comptime statement dispatch)
// ---------------------------------------------------------------------------

pub fn execute_python_ir(comptime ir_data: []const u8) void {
    comptime var pc: usize = 0;
    @setEvalBranchQuota(100_000);

    inline while (pc < ir_data.len) {
        const op = @as(OpCode, @enumFromInt(ir_data[pc]));
        switch (op) {

        // ----------------------------------------------------------------
        // I/O
        // ----------------------------------------------------------------

            .write_serial => {
                pc += 1;
                const val = comptime evaluate_ir(ir_data, &pc);
                Serial.write(val);
            },

            // ----------------------------------------------------------------
            // Memory
            // ----------------------------------------------------------------

            .mem_write => {
                pc += 1;
                const addr = comptime std.mem.readInt(u32, ir_data[pc..pc + 4], .little);
                pc += 4;
                const val = comptime evaluate_ir(ir_data, &pc);
                @as(*volatile u8, @ptrFromInt(addr)).* = val;
            },

            .mem_read => {
                pc += 1;
                const addr = comptime std.mem.readInt(u32, ir_data[pc..pc + 4], .little);
                pc += 4;
                _ = @as(*volatile u8, @ptrFromInt(addr)).*;
            },

            // ----------------------------------------------------------------
            // Control flow – loop
            // ----------------------------------------------------------------

            .loop => {
                pc += 1;
                const count    = comptime std.mem.readInt(u32, ir_data[pc..pc + 4], .little);
                pc += 4;
                const body_len = comptime std.mem.readInt(u32, ir_data[pc..pc + 4], .little);
                pc += 4;
                comptime var i: u32 = 0;
                inline while (i < count) : (i += 1) {
                    execute_python_ir(ir_data[pc..pc + body_len]);
                }
                pc += body_len;
            },

            // ----------------------------------------------------------------
            // Control flow – unconditional jump
            // ----------------------------------------------------------------

            .jmp => {
                pc += 1;
                // Offset is relative to the byte immediately after the field.
                const offset = comptime std.mem.readInt(i32, ir_data[pc..pc + 4], .little);
                pc += 4;
                pc = @intCast(@as(i64, @intCast(pc)) + offset);
            },

            // ----------------------------------------------------------------
            // Control flow – conditional jumps
            //
            // Encoding for all three variants:
            //   [opcode] [operand(s) – variable length] [offset: i32 LE]
            //
            // The signed offset is relative to the byte immediately after the
            // 4-byte offset field (i.e. the start of the next instruction).
            // A positive offset skips forward; a negative offset loops back.
            // ----------------------------------------------------------------

            .jmp_if_zero => {
                pc += 1;
                // Evaluate the single value operand, then read the offset.
                const val    = comptime evaluate_ir(ir_data, &pc);
                const offset = comptime std.mem.readInt(i32, ir_data[pc..pc + 4], .little);
                pc += 4;
                if (val == 0) {
                    pc = @intCast(@as(i64, @intCast(pc)) + offset);
                }
            },

            .jmp_if_eq => {
                pc += 1;
                const left   = comptime evaluate_ir(ir_data, &pc);
                const right  = comptime evaluate_ir(ir_data, &pc);
                const offset = comptime std.mem.readInt(i32, ir_data[pc..pc + 4], .little);
                pc += 4;
                if (left == right) {
                    pc = @intCast(@as(i64, @intCast(pc)) + offset);
                }
            },

            .jmp_if_lt => {
                pc += 1;
                // Unsigned less-than comparison.
                const left   = comptime evaluate_ir(ir_data, &pc);
                const right  = comptime evaluate_ir(ir_data, &pc);
                const offset = comptime std.mem.readInt(i32, ir_data[pc..pc + 4], .little);
                pc += 4;
                if (left < right) {
                    pc = @intCast(@as(i64, @intCast(pc)) + offset);
                }
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

        const catalog_lba = std.mem.readInt(u32, sector[71..75], .little);

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