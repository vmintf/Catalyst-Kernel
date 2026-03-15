"""Snapshot tests for KernelDecorator IR serialization.

These tests verify that _serialize() produces byte-for-byte identical output
for a fixed set of IR nodes, catching silent regressions after structural
refactors.  Run from the repository root:

    python3 -m pytest tests/ -v
"""
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest

from frontend.src.decorator import KernelDecorator
from frontend.src.nodes import (
    bit_and_ir,
    clear_screen_ir,
    mem_write,
    u32,
    write_con_str_ir,
    write_str_ir,
)
from frontend.src.opcodes import (
    OP_BIT_AND,
    OP_CLEAR_SCREEN,
    OP_HALT,
    OP_JMP_IF_ZERO,
    OP_LITERAL,
    OP_MEM_WRITE,
    OP_WRITE_CON_STR,
    OP_WRITE_STR,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _k() -> KernelDecorator:
    """Return a fresh, empty KernelDecorator."""
    return KernelDecorator()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLiteral(unittest.TestCase):
    def test_u32_encoding(self):
        k = _k()
        k._serialize(u32(0x42))
        self.assertEqual(bytes(k.ir_buffer), bytes([OP_LITERAL, 0x42]))

    def test_u32_masks_to_byte(self):
        # u32 stores value & 0xFFFFFFFF; _serialize emits only the low byte
        k = _k()
        k._serialize(u32(0x101))
        self.assertEqual(bytes(k.ir_buffer), bytes([OP_LITERAL, 0x01]))


class TestStringNodes(unittest.TestCase):
    def test_write_str_encoding(self):
        k = _k()
        k._serialize(write_str_ir("hi"))
        # [OP_WRITE_STR] [len_lo] [len_hi] [h] [i]
        expected = bytes([OP_WRITE_STR, 0x02, 0x00, ord("h"), ord("i")])
        self.assertEqual(bytes(k.ir_buffer), expected)

    def test_write_con_str_encoding(self):
        k = _k()
        k._serialize(write_con_str_ir("ok"))
        expected = bytes([OP_WRITE_CON_STR, 0x02, 0x00, ord("o"), ord("k")])
        self.assertEqual(bytes(k.ir_buffer), expected)

    def test_empty_string(self):
        k = _k()
        k._serialize(write_str_ir(""))
        self.assertEqual(bytes(k.ir_buffer), bytes([OP_WRITE_STR, 0x00, 0x00]))


class TestNoOperandNodes(unittest.TestCase):
    def test_clear_screen(self):
        k = _k()
        k._serialize(clear_screen_ir())
        self.assertEqual(bytes(k.ir_buffer), bytes([OP_CLEAR_SCREEN]))

    def test_halt(self):
        k = _k()
        k.ir_buffer.append(OP_HALT)
        self.assertEqual(bytes(k.ir_buffer), bytes([0xFF]))


class TestMemWrite(unittest.TestCase):
    def test_encoding(self):
        k = _k()
        k._serialize(mem_write(0x1000, u32(0xFF)))
        # [OP_MEM_WRITE] [addr: u32 LE] [OP_LITERAL] [value_byte]
        expected = bytes(
            [OP_MEM_WRITE]
            + list(struct.pack("<I", 0x1000))
            + [OP_LITERAL, 0xFF]
        )
        self.assertEqual(bytes(k.ir_buffer), expected)


class TestBitwiseNodes(unittest.TestCase):
    def test_bit_and_encoding(self):
        k = _k()
        k._serialize(bit_and_ir(u32(0x0F), u32(0xF0)))
        # [OP_BIT_AND] [OP_LITERAL, 0x0F] [OP_LITERAL, 0xF0]
        expected = bytes([OP_BIT_AND, OP_LITERAL, 0x0F, OP_LITERAL, 0xF0])
        self.assertEqual(bytes(k.ir_buffer), expected)


class TestJumpPatch(unittest.TestCase):
    def test_emit_jmp_if_zero_patch(self):
        """patch_jmp fills in the correct forward distance."""
        k = _k()
        slot = k.emit_jmp_if_zero(u32(0))
        # Emit one extra byte after the placeholder.
        k.ir_buffer.append(0xAA)
        k.patch_jmp(slot)
        offset = struct.unpack_from("<i", k.ir_buffer, slot)[0]
        self.assertEqual(offset, 1)

    def test_jmp_if_zero_opcode_position(self):
        k = _k()
        slot = k.emit_jmp_if_zero(u32(1))
        # First byte must be the jump opcode.
        self.assertEqual(k.ir_buffer[0], OP_JMP_IF_ZERO)
        # slot must point past the operand bytes (OP_LITERAL + value).
        self.assertGreater(slot, 0)

    def test_patch_jmp_back(self):
        """patch_jmp_back produces a negative offset that lands on target."""
        k = _k()
        target = k.label()
        k._serialize(clear_screen_ir())
        slot = k.emit_jmp_if_zero(u32(0))
        k.patch_jmp_back(slot, target)
        offset = struct.unpack_from("<i", k.ir_buffer, slot)[0]
        # offset + (slot + 4) should equal target
        self.assertEqual(slot + 4 + offset, target)


class TestDeterminism(unittest.TestCase):
    def test_two_builds_are_identical(self):
        def build():
            k = _k()
            k._serialize(write_str_ir("hello"))
            k._serialize(clear_screen_ir())
            k.ir_buffer.append(OP_HALT)
            return bytes(k.ir_buffer)

        self.assertEqual(build(), build())

    def test_sequence_order_matters(self):
        def build_a():
            k = _k()
            k._serialize(write_str_ir("a"))
            k._serialize(write_str_ir("b"))
            return bytes(k.ir_buffer)

        def build_b():
            k = _k()
            k._serialize(write_str_ir("b"))
            k._serialize(write_str_ir("a"))
            return bytes(k.ir_buffer)

        self.assertNotEqual(build_a(), build_b())


if __name__ == "__main__":
    unittest.main()
