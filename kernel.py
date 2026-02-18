# Catalyst Kernel - A bare-metal UEFI kernel using Python DSL and Zig comptime AOT compilation.
# Copyright (C) 2025  Skystarry.xyz
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# See LICENSE for details.

import struct
import tomllib

# Loading opcode and device addresses from hardware.toml
with open("toml/hardware.toml", "rb") as f:
    hw = tomllib.load(f)

OPCODES = hw["opcodes"]
DEVICES = hw["devices"]

# opcode constant dynamic binding
OP_LITERAL      = OPCODES["literal"]
OP_WRITE_SERIAL = OPCODES["write_serial"]
OP_ADD_U32      = OPCODES["add_u32"]
OP_SUB_U32      = OPCODES["sub_u32"]
OP_MEM_WRITE    = OPCODES["mem_write"]
OP_MEM_READ     = OPCODES["mem_read"]
OP_LOOP         = OPCODES["loop"]
OP_JMP          = OPCODES["jmp"]
OP_HALT         = OPCODES["halt"]


class IRNode:
    def __init__(self, op, args):
        self.op = op
        self.args = args

class u32:
    def __init__(self, value):
        self.value = value

    def __add__(self, other):
        return IRNode(OP_ADD_U32, [self, other])

    def __sub__(self, other):
        return IRNode(OP_SUB_U32, [self, other])

class mem_write:
    def __init__(self, addr: int, value: u32):
        self.addr = addr
        self.value = value

class mem_read:
    def __init__(self, addr: int):
        self.addr = addr

class loop_ir:
    """OP_LOOP: Repeat body IR as many times as count"""
    def __init__(self, count: int, body: list):
        self.count = count
        self.body = body

class jmp_ir:
    """OP_JMP: Move PC by offset without condition"""
    def __init__(self, offset: int):
        self.offset = offset


class KernelDecorator:
    def __init__(self):
        self.ir_buffer = bytearray()
        self._devices = {}

    def register(self, name: str):
        """Decorator to register physical addresses as device names"""
        addr = DEVICES[name]
        def decorator(func):
            def wrapper(value: u32):
                node = mem_write(addr, value)
                self._serialize(node)
            self._devices[name] = wrapper
            return wrapper
        return decorator

    def __call__(self, func):
        def wrapper(*args):
            result = func(*args)
            self._serialize(IRNode(OP_WRITE_SERIAL, [result]))
            return result
        return wrapper

    def _serialize(self, node):
        if isinstance(node, u32):
            self.ir_buffer.append(OP_LITERAL)
            self.ir_buffer.append(node.value & 0xFF)

        elif isinstance(node, IRNode):
            self.ir_buffer.append(node.op)
            for arg in node.args:
                self._serialize(arg)

        elif isinstance(node, mem_write):
            self.ir_buffer.append(OP_MEM_WRITE)
            self.ir_buffer += struct.pack('<I', node.addr)
            self._serialize(node.value)

        elif isinstance(node, mem_read):
            self.ir_buffer.append(OP_MEM_READ)
            self.ir_buffer += struct.pack('<I', node.addr)

        elif isinstance(node, loop_ir):
            self.ir_buffer.append(OP_LOOP)
            self.ir_buffer += struct.pack('<I', node.count)
            # body 직렬화 후 길이를 앞에 기록
            body_buf = bytearray()
            for item in node.body:
                tmp = self.ir_buffer
                self.ir_buffer = body_buf
                self._serialize(item)
                body_buf = self.ir_buffer
                self.ir_buffer = tmp
            self.ir_buffer += struct.pack('<I', len(body_buf))
            self.ir_buffer += body_buf

        elif isinstance(node, jmp_ir):
            self.ir_buffer.append(OP_JMP)
            self.ir_buffer += struct.pack('<i', node.offset)  # signed offset

    def save(self):
        with open("src/ir_generated.bin", "wb") as f:
            f.write(self.ir_buffer)


kernel = KernelDecorator()

# Device register
@kernel.register("serial")
def write_serial(value: u32): ...

@kernel.register("vga")
def write_vga(value: u32): ...


# Static Graph (AOT)
@kernel
def calc_and_print(a: u32):
    return a + u32(5)  # 0x41 + 5 = 0x46 ('F')


if __name__ == "__main__":
    # Static Calc
    calc_and_print(u32(0x41))

    # Direct Device Access
    write_serial(u32(0x47))  # 'G'

    # Loop: 'A' third times
    kernel._serialize(loop_ir(3, [
        IRNode(OP_WRITE_SERIAL, [u32(0x41)])
    ]))

    kernel.ir_buffer.append(OP_HALT)
    kernel.save()