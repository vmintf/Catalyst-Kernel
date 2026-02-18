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