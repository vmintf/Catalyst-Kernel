# Catalyst Kernel - A bare-metal UEFI kernel using Python DSL and Zig AOT compilation.
# Copyright (C) 2026  Skystarry.xyz
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# See LICENSE for details.

"""IR node types for the Catalyst Kernel DSL.

Each class represents one instruction in the IR byte stream.  Instances are
passed to KernelDecorator._serialize(), which recursively encodes the tree
into a flat binary buffer consumed by the Zig runtime.

Naming convention:
  - Nodes that wrap a single opcode carry the ``_ir`` suffix.
  - DSL value types (``u32``) and generic containers (``IRNode``) are plain names.
  - All lowercase class names are intentional DSL ergonomics; N801 is suppressed
    where necessary.
"""

from .opcodes import (
    OP_ADD_U32,
    OP_CMP_EQ,
    OP_CMP_GT,
    OP_CMP_LT,
    OP_DIV_U32,
    OP_MOD_U32,
    OP_MUL_U32,
    OP_SUB_U32,
)


# ---------------------------------------------------------------------------
# Generic container
# ---------------------------------------------------------------------------

class IRNode:
    """A generic IR instruction node carrying an opcode and its operands."""

    def __init__(self, op: int, args: list) -> None:
        self.op = op
        self.args = args


# ---------------------------------------------------------------------------
# DSL value type
# ---------------------------------------------------------------------------

class u32:  # noqa: N801
    """Wraps a literal u32 value and supports operator overloading into IR."""

    def __init__(self, value: int) -> None:
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


# ---------------------------------------------------------------------------
# I/O nodes
# ---------------------------------------------------------------------------

class write_str_ir:  # noqa: N801
    """Emit a WRITE_STR instruction: write a raw ASCII string to serial at runtime.

    Encoding: [write_str] [length: u16 LE] [bytes...]

    Unlike repeated write_serial nodes, this is handled entirely at runtime by
    the Zig interpreter and does not consume any comptime branch quota.
    """

    def __init__(self, text: str) -> None:
        self.text = text


class write_console_ir:  # noqa: N801
    """Emit a WRITE_CONSOLE instruction: write a single byte to the UEFI console.

    Encoding: [write_console] [value node bytes]
    """

    def __init__(self, value) -> None:
        self.value = value


class write_con_str_ir:  # noqa: N801
    """Emit a WRITE_CON_STR instruction: write an ASCII string to the UEFI console.

    Encoding: [write_con_str] [length: u16 LE] [bytes...]

    The Zig runtime converts each byte to UTF-16LE before passing it to
    con_out.outputString.
    """

    def __init__(self, text: str) -> None:
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


class read_port_ir:  # noqa: N801
    """Emit a READ_PORT instruction: runtime inb from *port* (u16).

    As an expression node, the byte read from the port is returned as a u8
    result, allowing it to be composed with cmp_* and jmp_if_zero:

        slot = kernel.emit_jmp_if_zero(read_port_ir(PORTS["ps2_data"]))

    As a standalone statement the byte is echoed to serial by the Zig runtime.
    """

    def __init__(self, port: int) -> None:
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

    def __init__(self, port: int, value) -> None:
        self.port = port & 0xFFFF
        self.value = value


class poll_key_ir:  # noqa: N801
    """OP_POLL_KEY: block until a printable PS/2 key is pressed, echo to serial.

    No operands.  The Zig runtime spins on port 0x60/0x64 until a printable
    ASCII make-code arrives, then writes it to COM1.
    """


class read_line_ir:  # noqa: N801
    """OP_READ_LINE: read a line of input from the UEFI console into an internal
    buffer, echoing each character as it is typed.  Backspace erases the last
    character.  The line is committed when Enter is pressed.

    Encoding: [read_line] [buf_addr: u32 LE] [max_len: u16 LE]

    The Zig runtime writes the null-terminated result into the IR memory region
    at *buf_addr* (relative to the ir_data base pointer is NOT used here -
    buf_addr is an absolute address in the kernel's static buffer).
    For simplicity, buf_addr=0 uses the runtime's internal scratch buffer.
    """

    def __init__(self, buf_addr: int = 0, max_len: int = 128) -> None:
        self.buf_addr = buf_addr
        self.max_len = max_len


# ---------------------------------------------------------------------------
# Memory nodes
# ---------------------------------------------------------------------------

class mem_write:  # noqa: N801 - intentionally lowercase for DSL ergonomics
    """Emit a MEM_WRITE instruction: store *value* at *addr*."""

    def __init__(self, addr: int, value: u32) -> None:
        self.addr = addr
        self.value = value


class mem_read:  # noqa: N801
    """Emit a MEM_READ instruction: load a byte from *addr*."""

    def __init__(self, addr: int) -> None:
        self.addr = addr


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

    def __init__(self, dst_addr: int, src_addr: int, count: int) -> None:
        self.dst_addr = dst_addr & 0xFFFF_FFFF
        self.src_addr = src_addr & 0xFFFF_FFFF
        self.count = count & 0xFFFF_FFFF


class mem_index_ir:  # noqa: N801
    """Emit a MEM_INDEX instruction: read line_buf[index] as an expression value.

    Encoding: [mem_index] [base_addr: u32 LE] [index node bytes]

    base_addr=0 reads from the global line_buf written by read_line.
    The result is a u8 that can be composed with cmp_* and jmp_if_zero.
    """

    def __init__(self, index, base_addr: int = 0) -> None:
        self.index = index
        self.base_addr = base_addr


class map_page_ir:  # noqa: N801
    """Emit a MAP_PAGE instruction: allocate and map a physical page.

    Encoding: [map_page] [phys: u32 LE] [virt: u32 LE]

    Only valid before ExitBootServices.  After handoff, page table
    manipulation must be done directly via mem_write on the PML4/PDPT/PD/PT
    structures.
    """

    def __init__(self, phys: int, virt: int) -> None:
        self.phys = phys & 0xFFFF_FFFF
        self.virt = virt & 0xFFFF_FFFF


class unmap_page_ir:  # noqa: N801
    """Emit an UNMAP_PAGE instruction: free a previously mapped virtual page.

    Encoding: [unmap_page] [virt: u32 LE]
    """

    def __init__(self, virt: int) -> None:
        self.virt = virt & 0xFFFF_FFFF


class get_mem_map_ir:  # noqa: N801
    """Emit a GET_MEM_MAP instruction: read the UEFI memory map into a buffer.

    Encoding: [get_mem_map] [buf_addr: u32 LE] [buf_size: u32 LE]

    The Zig runtime calls UEFI GetMemoryMap and writes raw
    EFI_MEMORY_DESCRIPTOR entries starting at *buf_addr*.  A 4 KiB buffer
    is sufficient for typical firmware maps (~40 entries).  Must be called
    before ExitBootServices.
    """

    def __init__(self, buf_addr: int, buf_size: int) -> None:
        self.buf_addr = buf_addr & 0xFFFF_FFFF
        self.buf_size = buf_size & 0xFFFF_FFFF


# ---------------------------------------------------------------------------
# Stack nodes
# ---------------------------------------------------------------------------

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

    def __init__(self, value) -> None:
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


class dup_ir:  # noqa: N801
    """Emit a DUP instruction: duplicate the top of the scratch stack.

    Encoding: [dup]
    """


class swap_ir:  # noqa: N801
    """Emit a SWAP instruction: exchange the top two entries on the scratch stack.

    Encoding: [swap]
    """


# ---------------------------------------------------------------------------
# Bitwise nodes
# ---------------------------------------------------------------------------

class bit_and_ir:  # noqa: N801
    """Emit a BIT_AND instruction: bitwise AND of *left* and *right*.

    Encoding: [bit_and] [left node] [right node]

    Useful for port masking, flag testing, and page-alignment calculations
    (e.g. ``bit_and_ir(addr, u32(~0xFFF))`` to align to a 4 KiB boundary).
    """

    def __init__(self, left, right) -> None:
        self.left = left
        self.right = right


class bit_or_ir:  # noqa: N801
    """Emit a BIT_OR instruction: bitwise OR of *left* and *right*.

    Encoding: [bit_or] [left node] [right node]
    """

    def __init__(self, left, right) -> None:
        self.left = left
        self.right = right


class bit_xor_ir:  # noqa: N801
    """Emit a BIT_XOR instruction: bitwise XOR of *left* and *right*.

    Encoding: [bit_xor] [left node] [right node]
    """

    def __init__(self, left, right) -> None:
        self.left = left
        self.right = right


class bit_not_ir:  # noqa: N801
    """Emit a BIT_NOT instruction: bitwise complement of *operand*.

    Encoding: [bit_not] [operand node]
    """

    def __init__(self, operand) -> None:
        self.operand = operand


class bit_shl_ir:  # noqa: N801
    """Emit a BIT_SHL instruction: logical left shift *left* by *right* bits.

    The shift amount is masked to 0-7 by the Zig runtime to avoid undefined
    behaviour on u8 values.

    Encoding: [bit_shl] [left node] [right node]
    """

    def __init__(self, left, right) -> None:
        self.left = left
        self.right = right


class bit_shr_ir:  # noqa: N801
    """Emit a BIT_SHR instruction: logical right shift *left* by *right* bits.

    Encoding: [bit_shr] [left node] [right node]
    """

    def __init__(self, left, right) -> None:
        self.left = left
        self.right = right


# ---------------------------------------------------------------------------
# Comparison nodes
# ---------------------------------------------------------------------------

class cmp_neq_ir:  # noqa: N801
    """Emit a CMP_NEQ instruction: 1 if *left* != *right*, else 0.

    Encoding: [cmp_neq] [left node] [right node]
    """

    def __init__(self, left, right) -> None:
        self.left = left
        self.right = right


class cmp_gte_ir:  # noqa: N801
    """Emit a CMP_GTE instruction: 1 if *left* >= *right*, else 0.

    Encoding: [cmp_gte] [left node] [right node]
    """

    def __init__(self, left, right) -> None:
        self.left = left
        self.right = right


class cmp_lte_ir:  # noqa: N801
    """Emit a CMP_LTE instruction: 1 if *left* <= *right*, else 0.

    Encoding: [cmp_lte] [left node] [right node]
    """

    def __init__(self, left, right) -> None:
        self.left = left
        self.right = right


# ---------------------------------------------------------------------------
# Interrupt nodes
# ---------------------------------------------------------------------------

class int_cli_ir:  # noqa: N801
    """Emit an INT_CLI instruction: disable hardware interrupts (x86 cli).

    Encoding: [int_cli]

    Must be paired with int_sti_ir to re-enable interrupts.  Leaving
    interrupts disabled across slow I/O sequences will block all IRQs.
    """


class int_sti_ir:  # noqa: N801
    """Emit an INT_STI instruction: enable hardware interrupts (x86 sti).

    Encoding: [int_sti]
    """


class int_n_ir:  # noqa: N801
    """Emit an INT_N instruction: fire software interrupt *vector* (0x00-0xFF).

    Encoding: [int_n] [vector: u8]

    Only a subset of vectors is handled by the Zig runtime; unsupported
    vectors are silently ignored.  Supported vectors: 0x03, 0x04, 0x10,
    0x13, 0x15.
    """

    def __init__(self, vector: int) -> None:
        self.vector = vector & 0xFF


# ---------------------------------------------------------------------------
# Control flow nodes
# ---------------------------------------------------------------------------

class loop_ir:  # noqa: N801
    """OP_LOOP: repeat *body* IR nodes exactly *count* times."""

    def __init__(self, count: int, body: list) -> None:
        self.count = count
        self.body = body


class jmp_ir:  # noqa: N801
    """OP_JMP: unconditionally advance PC by a signed *offset* bytes."""

    def __init__(self, offset: int) -> None:
        self.offset = offset


class jmp_if_zero_ir:  # noqa: N801
    """OP_JMP_IF_ZERO: advance PC by *offset* when *value* evaluates to 0.

    Pass offset=None to use the label/patch API instead of a manual value.

    Encoding: [opcode] [value node bytes] [offset: i32 LE]
    """

    def __init__(self, value, offset: int | None = None) -> None:
        self.value = value
        self.offset = offset


class jmp_if_eq_ir:  # noqa: N801
    """OP_JMP_IF_EQ: advance PC by *offset* when *left* == *right*.

    Pass offset=None to use the label/patch API instead of a manual value.

    Encoding: [opcode] [left node bytes] [right node bytes] [offset: i32 LE]
    """

    def __init__(self, left, right, offset: int | None = None) -> None:
        self.left = left
        self.right = right
        self.offset = offset


class jmp_if_lt_ir:  # noqa: N801
    """OP_JMP_IF_LT: advance PC by *offset* when *left* < *right* (unsigned).

    Pass offset=None to use the label/patch API instead of a manual value.

    Encoding: [opcode] [left node bytes] [right node bytes] [offset: i32 LE]
    """

    def __init__(self, left, right, offset: int | None = None) -> None:
        self.left = left
        self.right = right
        self.offset = offset


__all__ = [
    "IRNode",
    "u32",
    "write_str_ir",
    "write_console_ir",
    "write_con_str_ir",
    "clear_screen_ir",
    "write_line_ir",
    "read_port_ir",
    "write_port_ir",
    "poll_key_ir",
    "read_line_ir",
    "mem_write",
    "mem_read",
    "mem_copy_ir",
    "mem_index_ir",
    "map_page_ir",
    "unmap_page_ir",
    "get_mem_map_ir",
    "push_ir",
    "pop_ir",
    "dup_ir",
    "swap_ir",
    "bit_and_ir",
    "bit_or_ir",
    "bit_xor_ir",
    "bit_not_ir",
    "bit_shl_ir",
    "bit_shr_ir",
    "cmp_neq_ir",
    "cmp_gte_ir",
    "cmp_lte_ir",
    "int_cli_ir",
    "int_sti_ir",
    "int_n_ir",
    "loop_ir",
    "jmp_ir",
    "jmp_if_zero_ir",
    "jmp_if_eq_ir",
    "jmp_if_lt_ir",
]