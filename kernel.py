# Catalyst Kernel - A bare-metal UEFI kernel using Python DSL and Zig comptime AOT compilation.
# Copyright (C) 2026  Skystarry.xyz
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# See LICENSE for details.

import struct
import tomllib


# ---------------------------------------------------------------------------
# Hardware configuration
# ---------------------------------------------------------------------------

with open("toml/hardware.toml", "rb") as f:
    hw = tomllib.load(f)

OPCODES = hw["opcodes"]
DEVICES = hw["devices"]
PORTS   = hw["ports"]

# Command definitions - source of truth for the shell dispatcher.
with open("toml/commands.toml", "rb") as f:
    COMMANDS = tomllib.load(f)["commands"]

# Opcode constants - bound dynamically from hardware.toml so that the single
# source of truth remains the TOML file.
OP_LITERAL      = OPCODES["literal"]
OP_WRITE_SERIAL = OPCODES["write_serial"]
OP_WRITE_STR     = OPCODES["write_str"]
OP_WRITE_CONSOLE = OPCODES["write_console"]
OP_WRITE_CON_STR = OPCODES["write_con_str"]
OP_CLEAR_SCREEN  = OPCODES["clear_screen"]
OP_WRITE_LINE    = OPCODES["write_line"]
OP_ADD_U32      = OPCODES["add_u32"]
OP_SUB_U32      = OPCODES["sub_u32"]
OP_MUL_U32      = OPCODES["mul_u32"]
OP_DIV_U32      = OPCODES["div_u32"]
OP_MOD_U32      = OPCODES["mod_u32"]
OP_CMP_EQ       = OPCODES["cmp_eq"]
OP_CMP_LT       = OPCODES["cmp_lt"]
OP_CMP_GT       = OPCODES["cmp_gt"]
OP_MEM_WRITE    = OPCODES["mem_write"]
OP_MEM_READ     = OPCODES["mem_read"]
OP_READ_PORT    = OPCODES["read_port"]
OP_WRITE_PORT   = OPCODES["write_port"]
OP_PUSH         = OPCODES["push"]
OP_POP          = OPCODES["pop"]
OP_MEM_COPY     = OPCODES["mem_copy"]
OP_MEM_INDEX    = OPCODES["mem_index"]
OP_LOOP         = OPCODES["loop"]
OP_JMP          = OPCODES["jmp"]
OP_JMP_IF_ZERO  = OPCODES["jmp_if_zero"]
OP_JMP_IF_EQ    = OPCODES["jmp_if_eq"]
OP_JMP_IF_LT    = OPCODES["jmp_if_lt"]
OP_POLL_KEY     = OPCODES["poll_key"]
OP_READ_LINE    = OPCODES["read_line"]
OP_HALT         = OPCODES["halt"]

# Size of the i32 offset field appended to every jump instruction.
_JMP_OFFSET_SIZE = 4


# ---------------------------------------------------------------------------
# IR node types
# ---------------------------------------------------------------------------

class IRNode:
    """A generic IR instruction node carrying an opcode and its operands."""

    def __init__(self, op, args):
        self.op = op
        self.args = args


class u32:
    """Wraps a literal u32 value and supports operator overloading into IR."""

    def __init__(self, value: int):
        self.value = value & 0xFFFF_FFFF

    def __add__(self, other: "u32") -> IRNode:
        return IRNode(OP_ADD_U32, [self, other])

    def __sub__(self, other: "u32") -> IRNode:
        return IRNode(OP_SUB_U32, [self, other])

    def __mul__(self, other: "u32") -> IRNode:
        return IRNode(OP_MUL_U32, [self, other])

    def __floordiv__(self, other: "u32") -> IRNode:
        return IRNode(OP_DIV_U32, [self, other])

    def __mod__(self, other: "u32") -> IRNode:
        return IRNode(OP_MOD_U32, [self, other])

    def __eq__(self, other: "u32") -> IRNode:  # type: ignore[override]
        return IRNode(OP_CMP_EQ, [self, other])

    def __lt__(self, other: "u32") -> IRNode:
        return IRNode(OP_CMP_LT, [self, other])

    def __gt__(self, other: "u32") -> IRNode:
        return IRNode(OP_CMP_GT, [self, other])



class write_str_ir:  # noqa: N801
    """Emit a WRITE_STR instruction: write a raw ASCII string to serial at runtime.

    Encoding: [write_str] [length: u16 LE] [bytes...]

    Unlike repeated write_serial nodes, this is handled entirely at runtime by
    the Zig interpreter and does not consume any comptime branch quota.
    """

    def __init__(self, text: str):
        self.text = text


class write_console_ir:  # noqa: N801
    """Emit a WRITE_CONSOLE instruction: write a single byte to the UEFI console.

    Encoding: [write_console] [value node bytes]
    """

    def __init__(self, value):
        self.value = value


class write_con_str_ir:  # noqa: N801
    """Emit a WRITE_CON_STR instruction: write an ASCII string to the UEFI console.

    Encoding: [write_con_str] [length: u16 LE] [bytes...]

    The Zig runtime converts each byte to UTF-16LE before passing it to
    con_out.outputString.
    """

    def __init__(self, text: str):
        self.text = text


class clear_screen_ir:  # noqa: N801
    """Emit a CLEAR_SCREEN instruction: clear both UEFI con_out and the serial terminal.

    No operands.  On the UEFI side the Simple Text Output clearScreen()
    protocol call is used, which correctly fills the display and resets the
    cursor to (0, 0) regardless of firmware ANSI support.  On the serial
    side, ANSI CSI 2J + CSI H is sent so that a connected terminal emulator
    (e.g. minicom, screen) mirrors the clear.

    Encoding: [clear_screen]
    """


class write_line_ir:  # noqa: N801
    """Emit a WRITE_LINE instruction: echo line_buf[0..line_len] to serial and con_out.

    No operands.  The Zig runtime writes the contents of the global line_buf
    (populated by the most recent read_line) to both output channels, followed
    by a CR+LF pair.  Use this in echo-style commands to avoid re-encoding
    the input string in the IR byte stream.

    Encoding: [write_line]
    """

class mem_write:  # noqa: N801 - intentionally lowercase for DSL ergonomics
    """Emit a MEM_WRITE instruction: store *value* at *addr*."""

    def __init__(self, addr: int, value: u32):
        self.addr = addr
        self.value = value


class mem_read:  # noqa: N801
    """Emit a MEM_READ instruction: load a byte from *addr*."""

    def __init__(self, addr: int):
        self.addr = addr


class read_port_ir:  # noqa: N801
    """Emit a READ_PORT instruction: runtime inb from *port* (u16).

    As an expression node, the byte read from the port is returned as a u8
    result, allowing it to be composed with cmp_* and jmp_if_zero:

        slot = kernel.emit_jmp_if_zero(read_port_ir(PORTS["ps2_data"]))

    As a standalone statement the byte is echoed to serial by the Zig runtime.
    """

    def __init__(self, port: int):
        self.port = port & 0xFFFF


class write_port_ir:  # noqa: N801
    """Emit a WRITE_PORT instruction: runtime outb to *port* (u16).

    Acquires hardware control by writing *value* to the specified I/O port.
    Required before any device register sequence that needs port ownership
    (e.g. PIC remapping, PIT channel programming, UART setup).

    Encoding: [write_port] [port: u16 LE] [value node bytes]

    Example::

        kernel._serialize(write_port_ir(0x43, u32(0x36)))  # PIT mode register
    """

    def __init__(self, port: int, value):
        self.port = port & 0xFFFF
        self.value = value


class push_ir:  # noqa: N801
    """Emit a PUSH instruction: save *value* onto the scratch stack.

    The scratch stack is a 64-entry LIFO buffer managed entirely by the Zig
    runtime.  push/pop pairs provide temporary storage when writing complex
    DSL logic (bit manipulation, multi-step comparisons) without allocating
    named registers or global variables.

    Encoding: [push] [value node bytes]

    Example (save a port read for later comparison)::

        kernel._serialize(push_ir(read_port_ir(PORTS["ps2_status"])))
        # ... intervening instructions ...
        kernel._serialize(pop_ir())
    """

    def __init__(self, value):
        self.value = value


class pop_ir:  # noqa: N801
    """Emit a POP instruction: discard the top byte from the scratch stack.

    pop acts as a cleanup instruction; the popped value is not forwarded to
    serial or console.  Calling pop on an empty stack is a safe no-op in the
    Zig runtime.

    Encoding: [pop]

    Keep push/pop calls balanced within a logical block to avoid stack drift
    across loop iterations.
    """


class mem_copy_ir:  # noqa: N801
    """Emit a MEM_COPY instruction: copy *count* bytes from *src_addr* to *dst_addr*.

    All three fields are absolute 32-bit physical addresses / byte counts.
    The Zig runtime performs a volatile byte loop so that copies targeting
    MMIO regions (e.g. the VGA frame buffer at 0xB8000) are not elided by
    the optimizer.

    Encoding: [mem_copy] [dst_addr: u32 LE] [src_addr: u32 LE] [count: u32 LE]

    Overlapping source and destination windows are not supported.

    Example (blit a 80x25 VGA text-mode screen)::

        VGA_BUF  = DEVICES["vga"]
        BACK_BUF = 0x0010_0000  # 1 MiB scratch area
        kernel._serialize(mem_copy_ir(VGA_BUF, BACK_BUF, 80 * 25 * 2))
    """

    def __init__(self, dst_addr: int, src_addr: int, count: int):
        self.dst_addr = dst_addr & 0xFFFF_FFFF
        self.src_addr = src_addr & 0xFFFF_FFFF
        self.count    = count    & 0xFFFF_FFFF



class mem_index_ir:  # noqa: N801
    """Emit a MEM_INDEX instruction: read line_buf[index] as an expression value.

    Encoding: [mem_index] [base_addr: u32 LE] [index node bytes]

    base_addr=0 reads from the global line_buf written by read_line.
    The result is a u8 that can be composed with cmp_* and jmp_if_zero.
    """

    def __init__(self, index, base_addr: int = 0):
        self.index = index
        self.base_addr = base_addr

class loop_ir:
    """OP_LOOP: repeat *body* IR nodes exactly *count* times."""

    def __init__(self, count: int, body: list):
        self.count = count
        self.body = body


class jmp_ir:
    """OP_JMP: unconditionally advance PC by a signed *offset* bytes."""

    def __init__(self, offset: int):
        self.offset = offset


class jmp_if_zero_ir:
    """OP_JMP_IF_ZERO: advance PC by *offset* when *value* evaluates to 0.

    Pass offset=None to use the label/patch API instead of a manual value.

    Encoding: [opcode] [value node bytes] [offset: i32 LE]
    """

    def __init__(self, value, offset: int | None = None):
        self.value = value
        self.offset = offset


class jmp_if_eq_ir:
    """OP_JMP_IF_EQ: advance PC by *offset* when *left* == *right*.

    Pass offset=None to use the label/patch API instead of a manual value.

    Encoding: [opcode] [left node bytes] [right node bytes] [offset: i32 LE]
    """

    def __init__(self, left, right, offset: int | None = None):
        self.left = left
        self.right = right
        self.offset = offset


class jmp_if_lt_ir:
    """OP_JMP_IF_LT: advance PC by *offset* when *left* < *right* (unsigned).

    Pass offset=None to use the label/patch API instead of a manual value.

    Encoding: [opcode] [left node bytes] [right node bytes] [offset: i32 LE]
    """

    def __init__(self, left, right, offset: int | None = None):
        self.left = left
        self.right = right
        self.offset = offset


class poll_key_ir:
    """OP_POLL_KEY: block until a printable PS/2 key is pressed, echo to serial.

    No operands.  The Zig runtime spins on port 0x60/0x64 until a printable
    ASCII make-code arrives, then writes it to COM1.
    """

class read_line_ir:
    """OP_READ_LINE: read a line of input from the UEFI console into an internal
    buffer, echoing each character as it is typed.  Backspace erases the last
    character.  The line is committed when Enter is pressed.

    Encoding: [read_line] [buf_addr: u32 LE] [max_len: u16 LE]

    The Zig runtime writes the null-terminated result into the IR memory region
    at *buf_addr* (relative to the ir_data base pointer is NOT used here -
    buf_addr is an absolute address in the kernel's static buffer).
    For simplicity, buf_addr=0 uses the runtime's internal scratch buffer.
    """

    def __init__(self, buf_addr: int = 0, max_len: int = 128):
        self.buf_addr = buf_addr
        self.max_len = max_len


# ---------------------------------------------------------------------------
# Kernel decorator / IR compiler
# ---------------------------------------------------------------------------

class KernelDecorator:
    """Accumulates IR nodes and serialises them into a flat binary buffer.

    Label / patch workflow
    ----------------------
    Use emit_jmp_* to write a jump instruction whose offset field is
    temporarily set to zero.  The method returns the byte index of that
    placeholder so it can be back-patched later:

        slot = kernel.emit_jmp_if_zero(cmp_node)
        kernel._serialize(skipped_instruction)
        kernel.patch_jmp(slot)   # fills in the correct forward offset

    patch_jmp computes the distance from the end of the offset field
    (i.e. the start of the next instruction after the jump) to the current
    end of the buffer, then writes it as a little-endian i32.

    cmp + jmp_if_zero idioms
    ------------------------
    Since cmp_* returns 0 or 1, combining with jmp_if_zero gives readable
    conditional patterns without a dedicated jmp_if_neq etc.:

        # Jump when a == b  ->  cmp_eq returns 1, jmp_if_zero NOT taken
        # Jump when a != b  ->  cmp_eq returns 0, jmp_if_zero IS  taken
        slot = kernel.emit_jmp_if_zero(u32(a) == u32(b))

        # Jump when a >= b  ->  cmp_lt returns 0, jmp_if_zero IS  taken
        slot = kernel.emit_jmp_if_zero(u32(a) < u32(b))
    """

    def __init__(self):
        self.ir_buffer = bytearray()
        self._devices: dict = {}

    # ------------------------------------------------------------------
    # Device registration
    # ------------------------------------------------------------------

    def register(self, name: str):
        """Decorator: bind a physical MMIO address to a Python helper function."""
        addr = DEVICES[name]

        def decorator(func):
            def wrapper(value: u32):
                node = mem_write(addr, value)
                self._serialize(node)
            self._devices[name] = wrapper
            return wrapper

        return decorator

    # ------------------------------------------------------------------
    # Function-level decorator (wraps return value in write_serial)
    # ------------------------------------------------------------------

    def __call__(self, func):
        def wrapper(*args):
            result = func(*args)
            self._serialize(IRNode(OP_WRITE_SERIAL, [result]))
            return result
        return wrapper

    # ------------------------------------------------------------------
    # Label / patch helpers
    # ------------------------------------------------------------------

    def label(self) -> int:
        """Return the current write position as a forward-jump target label."""
        return len(self.ir_buffer)

    def patch_jmp(self, slot: int) -> None:
        """Back-patch the i32 offset placeholder written at *slot*.

        The offset is the distance from the byte immediately after the 4-byte
        field to the current end of the buffer (the next instruction site).

        Raises ValueError when the computed offset would overflow i32.
        """
        offset = len(self.ir_buffer) - (slot + _JMP_OFFSET_SIZE)
        if not (-2**31 <= offset <= 2**31 - 1):
            raise ValueError(
                f"patch_jmp: offset {offset} does not fit in i32 "
                f"(slot={slot}, buffer length={len(self.ir_buffer)})"
            )
        struct.pack_into("<i", self.ir_buffer, slot, offset)

    def patch_jmp_back(self, slot: int, target: int) -> None:
        """Back-patch *slot* to jump backward to *target*.

        *target* must be a label returned by label() before the jump was
        emitted.  The offset is computed so that after the Zig interpreter
        consumes the 4-byte field, PC lands exactly on *target*.

        Raises ValueError when the computed offset would overflow i32.
        """
        offset = target - (slot + _JMP_OFFSET_SIZE)
        if not (-2**31 <= offset <= 2**31 - 1):
            raise ValueError(
                f"patch_jmp_back: offset {offset} does not fit in i32 "
                f"(slot={slot}, target={target})"
            )
        struct.pack_into("<i", self.ir_buffer, slot, offset)

    def _alloc_offset_placeholder(self) -> int:
        """Append a zeroed i32 placeholder and return its byte index."""
        slot = len(self.ir_buffer)
        self.ir_buffer += b"\x00\x00\x00\x00"
        return slot

    # ------------------------------------------------------------------
    # Emit helpers - write opcode + operands + placeholder offset.
    # Each returns the slot index for a subsequent patch_jmp call.
    # ------------------------------------------------------------------

    def emit_jmp(self, offset: int = 0) -> int:
        """Emit an unconditional JMP; return the slot of the offset field."""
        self.ir_buffer.append(OP_JMP)
        slot = self._alloc_offset_placeholder()
        if offset:
            struct.pack_into("<i", self.ir_buffer, slot, offset)
        return slot

    def emit_jmp_if_zero(self, value, offset: int = 0) -> int:
        """Emit JMP_IF_ZERO with *value* operand; return the offset slot.

        *value* may be any serialisable node, including cmp_* IRNodes and
        read_port_ir instances.
        """
        self.ir_buffer.append(OP_JMP_IF_ZERO)
        self._serialize(value)
        slot = self._alloc_offset_placeholder()
        if offset:
            struct.pack_into("<i", self.ir_buffer, slot, offset)
        return slot

    def emit_jmp_if_eq(self, left, right, offset: int = 0) -> int:
        """Emit JMP_IF_EQ with *left*, *right* operands; return the offset slot."""
        self.ir_buffer.append(OP_JMP_IF_EQ)
        self._serialize(left)
        self._serialize(right)
        slot = self._alloc_offset_placeholder()
        if offset:
            struct.pack_into("<i", self.ir_buffer, slot, offset)
        return slot

    def emit_jmp_if_lt(self, left, right, offset: int = 0) -> int:
        """Emit JMP_IF_LT with *left*, *right* operands; return the offset slot."""
        self.ir_buffer.append(OP_JMP_IF_LT)
        self._serialize(left)
        self._serialize(right)
        slot = self._alloc_offset_placeholder()
        if offset:
            struct.pack_into("<i", self.ir_buffer, slot, offset)
        return slot

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def _serialize(self, node) -> None:
        """Recursively serialise an IR node tree into *self.ir_buffer*."""

        if isinstance(node, u32):
            self.ir_buffer.append(OP_LITERAL)
            self.ir_buffer.append(node.value & 0xFF)

        elif isinstance(node, IRNode):
            self.ir_buffer.append(node.op)
            for arg in node.args:
                self._serialize(arg)

        elif isinstance(node, write_str_ir):
            # Encoding: [write_str] [length: u16 LE] [bytes...]
            encoded = node.text.encode("ascii")
            self.ir_buffer.append(OP_WRITE_STR)
            self.ir_buffer += struct.pack("<H", len(encoded))
            self.ir_buffer += encoded

        elif isinstance(node, write_console_ir):
            # Encoding: [write_console] [value node bytes]
            self.ir_buffer.append(OP_WRITE_CONSOLE)
            self._serialize(node.value)

        elif isinstance(node, write_con_str_ir):
            # Encoding: [write_con_str] [length: u16 LE] [bytes...]
            encoded = node.text.encode("ascii")
            self.ir_buffer.append(OP_WRITE_CON_STR)
            self.ir_buffer += struct.pack("<H", len(encoded))
            self.ir_buffer += encoded

        elif isinstance(node, clear_screen_ir):
            # Encoding: [clear_screen]  (no operands)
            self.ir_buffer.append(OP_CLEAR_SCREEN)

        elif isinstance(node, write_line_ir):
            # Encoding: [write_line]  (no operands)
            self.ir_buffer.append(OP_WRITE_LINE)

        elif isinstance(node, mem_write):
            self.ir_buffer.append(OP_MEM_WRITE)
            self.ir_buffer += struct.pack("<I", node.addr)
            self._serialize(node.value)

        elif isinstance(node, mem_read):
            self.ir_buffer.append(OP_MEM_READ)
            self.ir_buffer += struct.pack("<I", node.addr)

        elif isinstance(node, read_port_ir):
            # Encoding: [read_port] [port: u16 LE]
            self.ir_buffer.append(OP_READ_PORT)
            self.ir_buffer += struct.pack("<H", node.port)

        elif isinstance(node, write_port_ir):
            # Encoding: [write_port] [port: u16 LE] [value node bytes]
            self.ir_buffer.append(OP_WRITE_PORT)
            self.ir_buffer += struct.pack("<H", node.port)
            self._serialize(node.value)

        elif isinstance(node, push_ir):
            # Encoding: [push] [value node bytes]
            self.ir_buffer.append(OP_PUSH)
            self._serialize(node.value)

        elif isinstance(node, pop_ir):
            # Encoding: [pop]  (no operands)
            self.ir_buffer.append(OP_POP)

        elif isinstance(node, mem_copy_ir):
            # Encoding: [mem_copy] [dst_addr: u32 LE] [src_addr: u32 LE] [count: u32 LE]
            self.ir_buffer.append(OP_MEM_COPY)
            self.ir_buffer += struct.pack("<I", node.dst_addr)
            self.ir_buffer += struct.pack("<I", node.src_addr)
            self.ir_buffer += struct.pack("<I", node.count)

        elif isinstance(node, mem_index_ir):
            # Encoding: [mem_index] [base_addr: u32 LE] [index node bytes]
            self.ir_buffer.append(OP_MEM_INDEX)
            self.ir_buffer += struct.pack("<I", node.base_addr)
            self._serialize(node.index)

        elif isinstance(node, loop_ir):
            self.ir_buffer.append(OP_LOOP)
            self.ir_buffer += struct.pack("<I", node.count)
            body_buf = bytearray()
            for item in node.body:
                saved = self.ir_buffer
                self.ir_buffer = body_buf
                self._serialize(item)
                body_buf = self.ir_buffer
                self.ir_buffer = saved
            self.ir_buffer += struct.pack("<I", len(body_buf))
            self.ir_buffer += body_buf

        elif isinstance(node, jmp_ir):
            self.ir_buffer.append(OP_JMP)
            self.ir_buffer += struct.pack("<i", node.offset)

        elif isinstance(node, poll_key_ir):
            self.ir_buffer.append(OP_POLL_KEY)

        elif isinstance(node, read_line_ir):
            # Encoding: [read_line] [buf_addr: u32 LE] [max_len: u16 LE]
            self.ir_buffer.append(OP_READ_LINE)
            self.ir_buffer += struct.pack("<I", node.buf_addr)
            self.ir_buffer += struct.pack("<H", node.max_len)


        elif isinstance(node, jmp_if_zero_ir):
            if node.offset is None:
                raise ValueError(
                    "jmp_if_zero_ir.offset is None - use kernel.emit_jmp_if_zero() "
                    "with kernel.patch_jmp() instead."
                )
            self.ir_buffer.append(OP_JMP_IF_ZERO)
            self._serialize(node.value)
            self.ir_buffer += struct.pack("<i", node.offset)

        elif isinstance(node, jmp_if_eq_ir):
            if node.offset is None:
                raise ValueError(
                    "jmp_if_eq_ir.offset is None - use kernel.emit_jmp_if_eq() "
                    "with kernel.patch_jmp() instead."
                )
            self.ir_buffer.append(OP_JMP_IF_EQ)
            self._serialize(node.left)
            self._serialize(node.right)
            self.ir_buffer += struct.pack("<i", node.offset)

        elif isinstance(node, jmp_if_lt_ir):
            if node.offset is None:
                raise ValueError(
                    "jmp_if_lt_ir.offset is None - use kernel.emit_jmp_if_lt() "
                    "with kernel.patch_jmp() instead."
                )
            self.ir_buffer.append(OP_JMP_IF_LT)
            self._serialize(node.left)
            self._serialize(node.right)
            self.ir_buffer += struct.pack("<i", node.offset)

    def save(self) -> None:
        """Write the accumulated IR buffer to disk."""
        with open("src/ir_generated.bin", "wb") as f:
            f.write(self.ir_buffer)


# ---------------------------------------------------------------------------
# Device and function declarations
# ---------------------------------------------------------------------------

kernel = KernelDecorator()


@kernel.register("serial")
def write_serial(value: u32): ...


@kernel.register("vga")
def write_vga(value: u32): ...



# ---------------------------------------------------------------------------
# Shell compiler
#
# emit_strcmp_token emits runtime byte comparisons using mem_index_ir.
# ShellCompiler wires up: prompt -> read_line -> dispatch -> loop.
# ---------------------------------------------------------------------------

_SHELL_INPUT_MAX = 128


def emit_strcmp_token(kernel_obj, cmd_name: str) -> list:
    """Emit IR that compares the first token of line_buf against cmd_name.

    For each character at position i, emit:
        jmp_if_zero(cmp_eq(mem_index(i), literal(expected)))  ->  mismatch

    After all characters match, also verify the token boundary:
        line_buf[n] must be space or null.

    Returns a list of forward-jump slots to patch on mismatch.
    """
    encoded = cmd_name.encode("ascii")
    mismatch_slots = []

    # Per-character comparison
    for i, expected in enumerate(encoded):
        cmp_node = mem_index_ir(u32(i)) == u32(expected)
        slot = kernel_obj.emit_jmp_if_zero(cmp_node)
        mismatch_slots.append(slot)

    # Token boundary: buf[n] == ' ' OR buf[n] == 0
    # No OR opcode, so use add: (cmp_eq(space) + cmp_eq(null)) == 0 means mismatch
    boundary = IRNode(
        OP_ADD_U32,
        [
            mem_index_ir(u32(len(encoded))) == u32(ord(' ')),
            mem_index_ir(u32(len(encoded))) == u32(0),
            ]
    )
    slot = kernel_obj.emit_jmp_if_zero(boundary)
    mismatch_slots.append(slot)

    return mismatch_slots


class ShellCompiler:
    """Compiles commands.toml into a shell dispatch loop in the IR buffer."""

    def __init__(self, kernel_obj, commands: dict):
        self._kernel   = kernel_obj
        self._commands = commands
        self._handlers = {}

    def command(self, name: str):
        """Decorator: register fn as the IR emitter for command name."""
        def decorator(fn):
            self._handlers[name] = fn
            return fn
        return decorator

    def compile(self) -> None:
        """Emit the full shell loop.

        Structure per iteration:
            loop_start:
                write_con_str("> ")
                read_line()
                write_con_str("\r\n")
                for each command:
                    strcmp_token  ->  mismatch: skip to next command
                    handler IR
                    jmp end_dispatch
                unknown command fallback
                jmp loop_start
            end_dispatch: <- patched by each successful match
        """
        loop_start = self._kernel.label()

        # Prompt
        self._kernel._serialize(write_con_str_ir("> "))
        self._kernel._serialize(write_str_ir("> "))

        # Read input into global line_buf
        self._kernel._serialize(read_line_ir(max_len=_SHELL_INPUT_MAX))

        # Newline after input
        self._kernel._serialize(write_con_str_ir("\r\n"))
        self._kernel._serialize(write_str_ir("\r\n"))

        end_slots = []

        for name, meta in self._commands.items():
            handler = self._handlers.get(name)
            if handler is None:
                continue

            # Emit strcmp; on mismatch jump past this handler
            mismatch_slots = emit_strcmp_token(self._kernel, name)

            # Match: emit handler body
            handler()

            # After handler, jump to end of dispatch table
            end_slot = self._kernel.emit_jmp()
            end_slots.append(end_slot)

            # Patch all mismatch jumps to land here (start of next command)
            for slot in mismatch_slots:
                self._kernel.patch_jmp(slot)

        # Unknown command fallback
        self._kernel._serialize(write_con_str_ir("Unknown command\r\n"))
        self._kernel._serialize(write_str_ir("Unknown command\r\n"))

        # Patch all end-of-dispatch jumps to land here
        for slot in end_slots:
            self._kernel.patch_jmp(slot)

        # Loop back to prompt
        back_slot = self._kernel.emit_jmp()
        self._kernel.patch_jmp_back(back_slot, loop_start)

if __name__ == "__main__":
    # ---------------------------------------------------------------------------
    # Boot banner
    # ---------------------------------------------------------------------------

    kernel._serialize(write_con_str_ir("Catalyst Kernel\r\n"))
    kernel._serialize(write_str_ir("Catalyst Kernel\r\n"))

    # ---------------------------------------------------------------------------
    # Shell command handlers
    # ---------------------------------------------------------------------------

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
        # Reflect the argument portion of line_buf (everything after "echo ").
        # line_buf holds the full input line; write_line_ir outputs it as-is so
        # the user sees exactly what they typed after the command name.
        # A future revision can strip the leading token once arg-parsing lands.
        kernel._serialize(write_line_ir())

    @shell.command("clear")
    def cmd_clear():
        # clear_screen_ir drives con_out.clearScreen() on the UEFI side and
        # sends ANSI CSI 2J + CSI H to serial.  ANSI escapes alone are not
        # sufficient because UEFI firmware does not interpret them.
        kernel._serialize(clear_screen_ir())

    @shell.command("version")
    def cmd_version():
        kernel._serialize(write_con_str_ir("Catalyst Kernel v0.1.0\r\n"))
        kernel._serialize(write_str_ir("Catalyst Kernel v0.1.0\r\n"))

    # ---------------------------------------------------------------------------
    # Compile shell dispatch loop into IR
    # ---------------------------------------------------------------------------

    shell.compile()

    kernel.ir_buffer.append(OP_HALT)
    kernel.save()