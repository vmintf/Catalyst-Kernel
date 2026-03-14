import struct

from .nodes import (
    IRNode,
    bit_and_ir,
    bit_not_ir,
    bit_or_ir,
    bit_shl_ir,
    bit_shr_ir,
    bit_xor_ir,
    clear_screen_ir,
    cmp_gte_ir,
    cmp_lte_ir,
    cmp_neq_ir,
    dup_ir,
    get_mem_map_ir,
    int_cli_ir,
    int_n_ir,
    int_sti_ir,
    jmp_if_eq_ir,
    jmp_if_lt_ir,
    jmp_if_zero_ir,
    jmp_ir,
    loop_ir,
    map_page_ir,
    mem_copy_ir,
    mem_index_ir,
    mem_read,
    mem_write,
    poll_key_ir,
    pop_ir,
    push_ir,
    read_line_ir,
    read_port_ir,
    swap_ir,
    u32,
    unmap_page_ir,
    write_con_str_ir,
    write_console_ir,
    write_line_ir,
    write_port_ir,
    write_str_ir,
)
from .opcodes import (
    COMMANDS,
    DEVICES,
    JMP_OFFSET_SIZE,
    OP_BIT_AND,
    OP_BIT_NOT,
    OP_BIT_OR,
    OP_BIT_SHL,
    OP_BIT_SHR,
    OP_BIT_XOR,
    OP_CLEAR_SCREEN,
    OP_CMP_GTE,
    OP_CMP_LTE,
    OP_CMP_NEQ,
    OP_DUP,
    OP_GET_MEM_MAP,
    OP_HALT,
    OP_INT_CLI,
    OP_INT_N,
    OP_INT_STI,
    OP_JMP,
    OP_JMP_IF_EQ,
    OP_JMP_IF_LT,
    OP_JMP_IF_ZERO,
    OP_LITERAL,
    OP_MAP_PAGE,
    OP_MEM_COPY,
    OP_MEM_INDEX,
    OP_MEM_READ,
    OP_MEM_WRITE,
    OP_POLL_KEY,
    OP_POP,
    OP_PUSH,
    OP_READ_LINE,
    OP_READ_PORT,
    OP_SWAP,
    OP_UNMAP_PAGE,
    OP_WRITE_CON_STR,
    OP_WRITE_CONSOLE,
    OP_WRITE_LINE,
    OP_WRITE_PORT,
    OP_WRITE_SERIAL,
    OP_WRITE_STR,
)

# ---------------------------------------------------------------------------
# Shell configuration
# ---------------------------------------------------------------------------

_SHELL_INPUT_MAX = 128


# ---------------------------------------------------------------------------
# String comparison helper
# ---------------------------------------------------------------------------

def emit_strcmp_token(kernel_obj: "KernelDecorator", cmd_name: str) -> list:
    """Emit IR that compares the first token of line_buf against cmd_name.

    For each character at position i, emit:
        jmp_if_zero(cmp_eq(mem_index(i), literal(expected)))  ->  mismatch

    After all characters match, also verify the token boundary:
        line_buf[n] must be space or null.

    Returns a list of forward-jump slots to patch on mismatch.
    """
    encoded = cmd_name.encode("ascii")
    mismatch_slots = []

    # Per-character comparison.
    for i, expected in enumerate(encoded):
        cmp_node = mem_index_ir(u32(i)) == u32(expected)
        slot = kernel_obj.emit_jmp_if_zero(cmp_node)
        mismatch_slots.append(slot)

    # Token boundary: buf[n] == ' ' OR buf[n] == 0.
    # No OR opcode available, so use ADD_U32: if neither comparison returns 1,
    # the sum is 0 and jmp_if_zero fires, indicating a mismatch.
    from .opcodes import OP_ADD_U32
    boundary = IRNode(
        OP_ADD_U32,
        [
            mem_index_ir(u32(len(encoded))) == u32(ord(" ")),
            mem_index_ir(u32(len(encoded))) == u32(0),
            ],
    )
    slot = kernel_obj.emit_jmp_if_zero(boundary)
    mismatch_slots.append(slot)

    return mismatch_slots


# ---------------------------------------------------------------------------
# Shell compiler
# ---------------------------------------------------------------------------

class ShellCompiler:
    """Compiles commands.toml into a shell dispatch loop in the IR buffer."""

    def __init__(self, kernel_obj: "KernelDecorator", commands: dict) -> None:
        self._kernel = kernel_obj
        self._commands = commands
        self._handlers: dict = {}

    def command(self, name: str):
        """Decorator: register fn as the IR emitter for command name."""
        def decorator(fn):
            self._handlers[name] = fn
            return fn
        return decorator

    def compile(self) -> None:
        """Emit the full shell loop.

        Structure per iteration::

            loop_start:
                write_con_str("> ")
                read_line()
                write_con_str("\\r\\n")
                for each command:
                    strcmp_token  ->  mismatch: skip to next command
                    handler IR
                    jmp end_dispatch
                unknown command fallback
                jmp loop_start
            end_dispatch: <- patched by each successful match
        """
        loop_start = self._kernel.label()

        # Prompt.
        self._kernel._serialize(write_con_str_ir("> "))
        self._kernel._serialize(write_str_ir("> "))

        # Read input into global line_buf.
        self._kernel._serialize(read_line_ir(max_len=_SHELL_INPUT_MAX))

        # Newline after input.
        self._kernel._serialize(write_con_str_ir("\r\n"))
        self._kernel._serialize(write_str_ir("\r\n"))

        end_slots = []

        for name in self._commands:
            handler = self._handlers.get(name)
            if handler is None:
                continue

            # Emit strcmp; on mismatch jump past this handler.
            mismatch_slots = emit_strcmp_token(self._kernel, name)

            # Match: emit handler body.
            handler()

            # After handler, jump to end of dispatch table.
            end_slot = self._kernel.emit_jmp()
            end_slots.append(end_slot)

            # Patch all mismatch jumps to land here (start of next command).
            for slot in mismatch_slots:
                self._kernel.patch_jmp(slot)

        # Unknown command fallback.
        self._kernel._serialize(write_con_str_ir("Unknown command\r\n"))
        self._kernel._serialize(write_str_ir("Unknown command\r\n"))

        # Patch all end-of-dispatch jumps to land here.
        for slot in end_slots:
            self._kernel.patch_jmp(slot)

        # Loop back to prompt.
        back_slot = self._kernel.emit_jmp()
        self._kernel.patch_jmp_back(back_slot, loop_start)


# ---------------------------------------------------------------------------
# Kernel decorator / IR compiler
# ---------------------------------------------------------------------------

class KernelDecorator:
    """Accumulates IR nodes and serialises them into a flat binary buffer.

    Label / patch workflow
    ----------------------
    Use emit_jmp_* to write a jump instruction whose offset field is
    temporarily set to zero.  The method returns the byte index of that
    placeholder so it can be back-patched later::

        slot = kernel.emit_jmp_if_zero(cmp_node)
        kernel._serialize(skipped_instruction)
        kernel.patch_jmp(slot)   # fills in the correct forward offset

    patch_jmp computes the distance from the end of the offset field
    (i.e. the start of the next instruction after the jump) to the current
    end of the buffer, then writes it as a little-endian i32.

    cmp + jmp_if_zero idioms
    ------------------------
    Since cmp_* returns 0 or 1, combining with jmp_if_zero gives readable
    conditional patterns without a dedicated jmp_if_neq etc.::

        # Jump when a == b  ->  cmp_eq returns 1, jmp_if_zero NOT taken
        # Jump when a != b  ->  cmp_eq returns 0, jmp_if_zero IS  taken
        slot = kernel.emit_jmp_if_zero(u32(a) == u32(b))

        # Jump when a >= b  ->  cmp_lt returns 0, jmp_if_zero IS  taken
        slot = kernel.emit_jmp_if_zero(u32(a) < u32(b))
    """

    def __init__(self) -> None:
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
        """Decorator: wrap a function so its return value is emitted to serial."""
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
        offset = len(self.ir_buffer) - (slot + JMP_OFFSET_SIZE)
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
        offset = target - (slot + JMP_OFFSET_SIZE)
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
    # Emit helpers
    # Each method writes the opcode + operands + a zeroed offset field,
    # then returns the slot index for a subsequent patch_jmp call.
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
            # Encoding: [mem_write] [addr: u32 LE] [value node bytes]
            self.ir_buffer.append(OP_MEM_WRITE)
            self.ir_buffer += struct.pack("<I", node.addr)
            self._serialize(node.value)

        elif isinstance(node, mem_read):
            # Encoding: [mem_read] [addr: u32 LE]
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

        elif isinstance(node, dup_ir):
            # Encoding: [dup]  (no operands)
            self.ir_buffer.append(OP_DUP)

        elif isinstance(node, swap_ir):
            # Encoding: [swap]  (no operands)
            self.ir_buffer.append(OP_SWAP)

        elif isinstance(node, bit_and_ir):
            # Encoding: [bit_and] [left node] [right node]
            self.ir_buffer.append(OP_BIT_AND)
            self._serialize(node.left)
            self._serialize(node.right)

        elif isinstance(node, bit_or_ir):
            # Encoding: [bit_or] [left node] [right node]
            self.ir_buffer.append(OP_BIT_OR)
            self._serialize(node.left)
            self._serialize(node.right)

        elif isinstance(node, bit_xor_ir):
            # Encoding: [bit_xor] [left node] [right node]
            self.ir_buffer.append(OP_BIT_XOR)
            self._serialize(node.left)
            self._serialize(node.right)

        elif isinstance(node, bit_not_ir):
            # Encoding: [bit_not] [operand node]
            self.ir_buffer.append(OP_BIT_NOT)
            self._serialize(node.operand)

        elif isinstance(node, bit_shl_ir):
            # Encoding: [bit_shl] [left node] [right node]
            self.ir_buffer.append(OP_BIT_SHL)
            self._serialize(node.left)
            self._serialize(node.right)

        elif isinstance(node, bit_shr_ir):
            # Encoding: [bit_shr] [left node] [right node]
            self.ir_buffer.append(OP_BIT_SHR)
            self._serialize(node.left)
            self._serialize(node.right)

        elif isinstance(node, cmp_neq_ir):
            # Encoding: [cmp_neq] [left node] [right node]
            self.ir_buffer.append(OP_CMP_NEQ)
            self._serialize(node.left)
            self._serialize(node.right)

        elif isinstance(node, cmp_gte_ir):
            # Encoding: [cmp_gte] [left node] [right node]
            self.ir_buffer.append(OP_CMP_GTE)
            self._serialize(node.left)
            self._serialize(node.right)

        elif isinstance(node, cmp_lte_ir):
            # Encoding: [cmp_lte] [left node] [right node]
            self.ir_buffer.append(OP_CMP_LTE)
            self._serialize(node.left)
            self._serialize(node.right)

        elif isinstance(node, int_cli_ir):
            # Encoding: [int_cli]  (no operands)
            self.ir_buffer.append(OP_INT_CLI)

        elif isinstance(node, int_sti_ir):
            # Encoding: [int_sti]  (no operands)
            self.ir_buffer.append(OP_INT_STI)

        elif isinstance(node, int_n_ir):
            # Encoding: [int_n] [vector: u8]
            self.ir_buffer.append(OP_INT_N)
            self.ir_buffer.append(node.vector)

        elif isinstance(node, map_page_ir):
            # Encoding: [map_page] [phys: u32 LE] [virt: u32 LE]
            self.ir_buffer.append(OP_MAP_PAGE)
            self.ir_buffer += struct.pack("<I", node.phys)
            self.ir_buffer += struct.pack("<I", node.virt)

        elif isinstance(node, unmap_page_ir):
            # Encoding: [unmap_page] [virt: u32 LE]
            self.ir_buffer.append(OP_UNMAP_PAGE)
            self.ir_buffer += struct.pack("<I", node.virt)

        elif isinstance(node, get_mem_map_ir):
            # Encoding: [get_mem_map] [buf_addr: u32 LE] [buf_size: u32 LE]
            self.ir_buffer.append(OP_GET_MEM_MAP)
            self.ir_buffer += struct.pack("<I", node.buf_addr)
            self.ir_buffer += struct.pack("<I", node.buf_size)

        elif isinstance(node, mem_index_ir):
            # Encoding: [mem_index] [base_addr: u32 LE] [index node bytes]
            self.ir_buffer.append(OP_MEM_INDEX)
            self.ir_buffer += struct.pack("<I", node.base_addr)
            self._serialize(node.index)

        elif isinstance(node, loop_ir):
            # Encoding: [loop] [count: u32 LE] [body_len: u32 LE] [body bytes...]
            # Serialize body into a temporary buffer to measure its length before
            # emitting the header, so that body_len is known at write time.
            from opcodes import OP_LOOP as _OP_LOOP
            self.ir_buffer.append(_OP_LOOP)
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
            # Encoding: [jmp] [offset: i32 LE]
            self.ir_buffer.append(OP_JMP)
            self.ir_buffer += struct.pack("<i", node.offset)

        elif isinstance(node, poll_key_ir):
            # Encoding: [poll_key]  (no operands)
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
        with open("backend/src/kernel/ir_generated.bin", "wb") as f:
            f.write(self.ir_buffer)