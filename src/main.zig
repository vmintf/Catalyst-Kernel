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
const CatalystKernel = @import("CatalystKernel");
const ir_data = @embedFile("ir_generated.bin");

pub fn main() uefi.Status {
    const st = uefi.system_table;
    const con_out = st.con_out.?;

    _ = con_out.reset(false) catch {};

    CatalystKernel.Serial.init();

    const msg = std.unicode.utf8ToUtf16LeStringLiteral("Initializing kernel...\r\n");
    _ = con_out.outputString(msg) catch {};

    // Browse CD-ROM Explorer at the time where the boot_service
    CatalystKernel.findAndBootCdrom();
    CatalystKernel.execute_python_ir(ir_data);

    CatalystKernel.Serial.write('H');
    CatalystKernel.Serial.write('I');

    while (true) {
        asm volatile ("hlt");
    }

    return .success;
}