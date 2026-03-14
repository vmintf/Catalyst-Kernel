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

# Opcode constants – bound dynamically from hardware.toml so that the single
# source of truth remains the TOML file.
OP_LITERAL      = OPCODES["literal"]
OP_WRITE_SERIAL = OPCODES["write_serial"]
OP_ADD_U32      = OPCODES["add_u32"]
OP_SUB_U32      = OPCODES["sub_u32"]
OP_MUL_U32      = OPCODES["mul_u32"]
OP_DIV_U32      = OPCODES["div_u32"]
OP_MOD_U32      = OPCODES["mod_u32"]
OP_MEM_WRITE    = OPCODES["mem_write"]
OP_MEM_READ     = OPCODES["mem_read"]
OP_LOOP         = OPCODES["loop"]
OP_JMP          = OPCODES["jmp"]
OP_JMP_IF_ZERO  = OPCODES["jmp_if_zero"]
OP_JMP_IF_EQ    = OPCODES["jmp_if_eq"]
OP_JMP_IF_LT    = OPCODES["jmp_if_lt"]
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


class mem_write:  # noqa: N801 – intentionally lowercase for DSL ergonomics
    """Emit a MEM_WRITE instruction: store *value* at *addr*."""

    def __init__(self, addr: int, value: u32):
        self.addr = addr
        self.value = value


class mem_read:  # noqa: N801
    """Emit a MEM_READ instruction: load a byte from *addr*."""

    def __init__(self, addr: int):
        self.addr = addr


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

        slot = kernel.emit_jmp_if_zero(value_node)
        kernel._serialize(skipped_instruction)
        kernel.patch_jmp(slot)   # fills in the correct forward offset

    patch_jmp computes the distance from the end of the offset field
    (i.e. the start of the next instruction after the jump) to the current
    end of the buffer, then writes it into the placeholder as a little-endian
    i32.  This matches the Zig interpreter's PC arithmetic exactly.
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

        *slot* must be the byte index returned by one of the emit_jmp_*
        methods.  The offset is computed as the distance from the byte
        immediately after the 4-byte offset field to the current end of the
        buffer, which is exactly where the next instruction will be written.

        Raises ValueError when the computed offset would overflow i32.
        """
        # PC after consuming the offset field = slot + _JMP_OFFSET_SIZE.
        # Jump target = current end of buffer (next instruction to be emitted).
        offset = len(self.ir_buffer) - (slot + _JMP_OFFSET_SIZE)
        if not (-2**31 <= offset <= 2**31 - 1):
            raise ValueError(
                f"patch_jmp: offset {offset} does not fit in i32 "
                f"(slot={slot}, buffer length={len(self.ir_buffer)})"
            )
        struct.pack_into("<i", self.ir_buffer, slot, offset)

    def _alloc_offset_placeholder(self) -> int:
        """Append a zeroed i32 placeholder and return its byte index."""
        slot = len(self.ir_buffer)
        self.ir_buffer += b"\x00\x00\x00\x00"
        return slot

    # ------------------------------------------------------------------
    # Emit helpers – write a jump opcode + operands + placeholder offset.
    # Each returns the slot index so the caller can call patch_jmp later.
    # ------------------------------------------------------------------

    def emit_jmp(self, offset: int = 0) -> int:
        """Emit an unconditional JMP; return the slot of the offset field."""
        self.ir_buffer.append(OP_JMP)
        slot = self._alloc_offset_placeholder()
        if offset:
            struct.pack_into("<i", self.ir_buffer, slot, offset)
        return slot

    def emit_jmp_if_zero(self, value, offset: int = 0) -> int:
        """Emit JMP_IF_ZERO with *value* operand; return the offset slot."""
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
            # LITERAL opcode: 1-byte value only (fits in u8 for serial output).
            self.ir_buffer.append(OP_LITERAL)
            self.ir_buffer.append(node.value & 0xFF)

        elif isinstance(node, IRNode):
            # Generic binary / unary expression node.
            self.ir_buffer.append(node.op)
            for arg in node.args:
                self._serialize(arg)

        elif isinstance(node, mem_write):
            self.ir_buffer.append(OP_MEM_WRITE)
            self.ir_buffer += struct.pack("<I", node.addr)
            self._serialize(node.value)

        elif isinstance(node, mem_read):
            self.ir_buffer.append(OP_MEM_READ)
            self.ir_buffer += struct.pack("<I", node.addr)

        elif isinstance(node, loop_ir):
            self.ir_buffer.append(OP_LOOP)
            self.ir_buffer += struct.pack("<I", node.count)
            # Serialise body into a temporary buffer so we can prefix its length.
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

        elif isinstance(node, jmp_if_zero_ir):
            if node.offset is None:
                raise ValueError(
                    "jmp_if_zero_ir.offset is None – use kernel.emit_jmp_if_zero() "
                    "with kernel.patch_jmp() instead."
                )
            self.ir_buffer.append(OP_JMP_IF_ZERO)
            self._serialize(node.value)
            self.ir_buffer += struct.pack("<i", node.offset)

        elif isinstance(node, jmp_if_eq_ir):
            if node.offset is None:
                raise ValueError(
                    "jmp_if_eq_ir.offset is None – use kernel.emit_jmp_if_eq() "
                    "with kernel.patch_jmp() instead."
                )
            self.ir_buffer.append(OP_JMP_IF_EQ)
            self._serialize(node.left)
            self._serialize(node.right)
            self.ir_buffer += struct.pack("<i", node.offset)

        elif isinstance(node, jmp_if_lt_ir):
            if node.offset is None:
                raise ValueError(
                    "jmp_if_lt_ir.offset is None – use kernel.emit_jmp_if_lt() "
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
# Kernel IR program (AOT static graph)
# ---------------------------------------------------------------------------

@kernel
def calc_and_print(a: u32):
    # 0x41 ('A') + 5 = 0x46 ('F')
    return a + u32(5)


if __name__ == "__main__":
    # --- arithmetic demos ---------------------------------------------------

    # Static calc: emits 'F' (0x41 + 0x05)
    calc_and_print(u32(0x41))

    # Direct device write: emits 'G' (0x47)
    write_serial(u32(0x47))

    # MUL: 0x06 * 0x0B = 0x42 ('B')
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x06) * u32(0x0B)]))

    # DIV: 0x84 // 0x02 = 0x42 ('B')
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x84) // u32(0x02)]))

    # MOD: 0x45 % 0x03 = 0x00 (non-printable)
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x45) % u32(0x03)]))

    # --- loop demo ----------------------------------------------------------

    # Loop: emit 'A' three times
    kernel._serialize(loop_ir(3, [
        IRNode(OP_WRITE_SERIAL, [u32(0x41)])
    ]))

    # --- conditional jump demos (label/patch) --------------------------------
    #
    # Pattern:
    #   slot = kernel.emit_jmp_*(...) # placeholder offset written as 0x00000000
    #   kernel._serialize(skipped)    # instruction(s) to skip when jump taken
    #   kernel.patch_jmp(slot)        # auto-computes and writes the correct offset
    #   kernel._serialize(target)     # instruction at the landing site

    # JMP_IF_ZERO: 0x48 % 0x02 == 0 → skip 'X', emit 'H'
    slot = kernel.emit_jmp_if_zero(u32(0x48) % u32(0x02))
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x58)]))  # 'X' – skipped
    kernel.patch_jmp(slot)
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x48)]))  # 'H'

    # JMP_IF_EQ: 0x02 == 0x02 → skip 'X', emit 'E'
    slot = kernel.emit_jmp_if_eq(u32(0x02), u32(0x02))
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x58)]))  # 'X' – skipped
    kernel.patch_jmp(slot)
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x45)]))  # 'E'

    # JMP_IF_LT: 0x01 < 0x02 → skip 'X', emit 'L'
    slot = kernel.emit_jmp_if_lt(u32(0x01), u32(0x02))
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x58)]))  # 'X' – skipped
    kernel.patch_jmp(slot)
    kernel._serialize(IRNode(OP_WRITE_SERIAL, [u32(0x4C)]))  # 'L'

    # --- halt ---------------------------------------------------------------
    kernel.ir_buffer.append(OP_HALT)
    kernel.save()