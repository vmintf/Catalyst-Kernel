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
const gop = uefi.protocol.GraphicsOutput;
const LinuxKernel = @import("LinuxKernel");
const ir_data = @embedFile("ir_generated.bin");

pub fn main() uefi.Status {
    const st = uefi.system_table;
    const con_out = st.con_out.?;

    _ = con_out.reset(false) catch {};

    const msg = std.unicode.utf8ToUtf16LeStringLiteral("Zig-Python Kernel Online\r\n");
    _ = con_out.outputString(msg) catch {};

    // Browse CD-ROM Explorer at the time where the boot_service
    LinuxKernel.findAndBootCdrom();
    LinuxKernel.execute_python_ir(ir_data);

    LinuxKernel.Serial.write('H');
    LinuxKernel.Serial.write('I');

    while (true) {
        asm volatile ("hlt");
    }

    return .success;
}