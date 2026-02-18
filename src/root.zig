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
    mem_write    = 0x40,
    mem_read     = 0x41,
    loop         = 0x50,
    jmp          = 0x51,
    halt         = 0xff,
    _,
};

pub const Serial = struct {
    pub inline fn write(data: u8) void {
        asm volatile ("outb %[data], %[port]"
            :
            : [data] "{al}" (data),
              [port] "{dx}" (@as(u16, 0x3F8))
        );
    }
};

fn evaluate_ir(comptime ir: []const u8, comptime pc: *usize) u8 {
    const op = @as(OpCode, @enumFromInt(ir[pc.*]));
    pc.* += 1;

    return switch (op) {
        .literal => blk: {
            const val = ir[pc.*];
            pc.* += 1;
            break :blk val;
        },
        .add_u32 => {
            const left = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return left + right;
        },
        .sub_u32 => {
            const left = evaluate_ir(ir, pc);
            const right = evaluate_ir(ir, pc);
            return left - right;
        },
        else => @panic("unknown opcode in expression"),
    };
}

pub fn execute_python_ir(comptime ir_data: []const u8) void {
    comptime var pc: usize = 0;
    @setEvalBranchQuota(100000);

    inline while (pc < ir_data.len) {
        const op = @as(OpCode, @enumFromInt(ir_data[pc]));
        switch (op) {
            .write_serial => {
                pc += 1;
                const val = comptime evaluate_ir(ir_data, &pc);
                Serial.write(val);
            },
            .mem_write => {
                pc += 1;
                const addr = comptime std.mem.readInt(u32, ir_data[pc..pc+4], .little);
                pc += 4;
                const val = comptime evaluate_ir(ir_data, &pc);
                @as(*volatile u8, @ptrFromInt(addr)).* = val;
            },
            .mem_read => {
                pc += 1;
                const addr = comptime std.mem.readInt(u32, ir_data[pc..pc+4], .little);
                pc += 4;
                _ = @as(*volatile u8, @ptrFromInt(addr)).*;
            },
            .loop => {
                pc += 1;
                const count = comptime std.mem.readInt(u32, ir_data[pc..pc+4], .little);
                pc += 4;
                const body_len = comptime std.mem.readInt(u32, ir_data[pc..pc+4], .little);
                pc += 4;
                comptime var i: u32 = 0;
                inline while (i < count) : (i += 1) {
                    execute_python_ir(ir_data[pc..pc+body_len]);
                }
                pc += body_len;
            },
            .jmp => {
                pc += 1;
                const offset = comptime std.mem.readInt(i32, ir_data[pc..pc+4], .little);
                pc += 4;
                pc = @intCast(@as(i64, @intCast(pc)) + offset);
            },
            .halt => {
                while (true) { asm volatile ("hlt"); }
            },
            else => { pc += 1; },
        }
    }
}


const ElToritoCatalogEntry = extern struct {
    boot_indicator: u8,
    media_type: u8,
    load_segment: u16,
    system_type: u8,
    _unused: u8,
    sector_count: u16,
    load_lba: u32,
    _pad: [20]u8,
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
        const image_buf = bs.allocatePool(.loader_data, image_size) catch continue;

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