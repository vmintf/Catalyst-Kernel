from src.decorator import KernelDecorator, ShellCompiler
from src.opcodes import *
from src.nodes import *

kernel = KernelDecorator()


@kernel.register("serial")
def write_serial(value: u32): ...


@kernel.register("vga")
def write_vga(value: u32): ...


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Boot banner.
    kernel._serialize(write_con_str_ir("Catalyst Kernel\r\n"))
    kernel._serialize(write_str_ir("Catalyst Kernel\r\n"))

    # Shell command handlers.
    shell = ShellCompiler(kernel, COMMANDS)

    @shell.command("help")
    def cmd_help():
        # Print each command name and description from commands.toml.
        for name, meta in COMMANDS.items():
            line = f"  {name:<12}{meta['description']}\r\n"
            kernel._serialize(write_con_str_ir(line))
            kernel._serialize(write_str_ir(line))

    @shell.command("echo")
    def cmd_echo():
        # Reflect line_buf contents via write_line_ir.
        # A future revision can strip the leading token once arg-parsing lands.
        kernel._serialize(write_line_ir())

    @shell.command("clear")
    def cmd_clear():
        # clear_screen_ir drives con_out.clearScreen() on the UEFI side and
        # sends ANSI CSI 2J + CSI H to serial.
        kernel._serialize(clear_screen_ir())

    @shell.command("version")
    def cmd_version():
        kernel._serialize(write_con_str_ir("Catalyst Kernel v0.1.0\r\n"))
        kernel._serialize(write_str_ir("Catalyst Kernel v0.1.0\r\n"))

    # Compile shell dispatch loop into IR.
    shell.compile()

    kernel.ir_buffer.append(OP_HALT)
    kernel.save()
