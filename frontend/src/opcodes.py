# Catalyst Kernel - A bare-metal UEFI kernel using Python DSL and Zig AOT compilation.
# Copyright (C) 2026  Skystarry.xyz
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# See LICENSE for details.

"""Opcode constants loaded from hardware.toml.

All OP_* names are bound dynamically so that the TOML file remains the
single source of truth.  Import this module to access opcode values;
do not hard-code numeric literals elsewhere.
"""
import tomllib

# ---------------------------------------------------------------------------
# Hardware configuration
# ---------------------------------------------------------------------------

with open("./frontend/toml/hardware.toml", "rb") as _f:
    _hw = tomllib.load(_f)

OPCODES = _hw["opcodes"]
DEVICES = _hw["devices"]
PORTS = _hw["ports"]

# Command definitions - source of truth for the shell dispatcher.
with open("./frontend/toml/commands.toml", "rb") as _f:
    COMMANDS = tomllib.load(_f)["commands"]

# ---------------------------------------------------------------------------
# Opcode constants
# ---------------------------------------------------------------------------

OP_LITERAL = OPCODES["literal"]
OP_WRITE_SERIAL = OPCODES["write_serial"]
OP_WRITE_STR = OPCODES["write_str"]
OP_WRITE_CONSOLE = OPCODES["write_console"]
OP_WRITE_CON_STR = OPCODES["write_con_str"]
OP_CLEAR_SCREEN = OPCODES["clear_screen"]
OP_WRITE_LINE = OPCODES["write_line"]
OP_ADD_U32 = OPCODES["add_u32"]
OP_SUB_U32 = OPCODES["sub_u32"]
OP_MUL_U32 = OPCODES["mul_u32"]
OP_DIV_U32 = OPCODES["div_u32"]
OP_MOD_U32 = OPCODES["mod_u32"]
OP_CMP_EQ = OPCODES["cmp_eq"]
OP_CMP_LT = OPCODES["cmp_lt"]
OP_CMP_GT = OPCODES["cmp_gt"]
OP_MEM_WRITE = OPCODES["mem_write"]
OP_MEM_READ = OPCODES["mem_read"]
OP_READ_PORT = OPCODES["read_port"]
OP_WRITE_PORT = OPCODES["write_port"]
OP_PUSH = OPCODES["push"]
OP_POP = OPCODES["pop"]
OP_MEM_COPY = OPCODES["mem_copy"]
OP_DUP = OPCODES["dup"]
OP_SWAP = OPCODES["swap"]
OP_BIT_AND = OPCODES["bit_and"]
OP_BIT_OR = OPCODES["bit_or"]
OP_BIT_XOR = OPCODES["bit_xor"]
OP_BIT_NOT = OPCODES["bit_not"]
OP_BIT_SHL = OPCODES["bit_shl"]
OP_BIT_SHR = OPCODES["bit_shr"]
OP_CMP_NEQ = OPCODES["cmp_neq"]
OP_CMP_GTE = OPCODES["cmp_gte"]
OP_CMP_LTE = OPCODES["cmp_lte"]
OP_INT_CLI = OPCODES["int_cli"]
OP_INT_STI = OPCODES["int_sti"]
OP_INT_N = OPCODES["int_n"]
OP_MAP_PAGE = OPCODES["map_page"]
OP_UNMAP_PAGE = OPCODES["unmap_page"]
OP_GET_MEM_MAP = OPCODES["get_mem_map"]
OP_MEM_INDEX = OPCODES["mem_index"]
OP_LOOP = OPCODES["loop"]
OP_JMP = OPCODES["jmp"]
OP_JMP_IF_ZERO = OPCODES["jmp_if_zero"]
OP_JMP_IF_EQ = OPCODES["jmp_if_eq"]
OP_JMP_IF_LT = OPCODES["jmp_if_lt"]
OP_POLL_KEY = OPCODES["poll_key"]
OP_READ_LINE = OPCODES["read_line"]
OP_HALT = OPCODES["halt"]

# Size of the i32 offset field appended to every jump instruction.
JMP_OFFSET_SIZE = 4

__all__ = [
    "OPCODES", "DEVICES", "PORTS", "COMMANDS",
    "JMP_OFFSET_SIZE",
    "OP_LITERAL",
    "OP_WRITE_SERIAL", "OP_WRITE_STR", "OP_WRITE_CONSOLE",
    "OP_WRITE_CON_STR", "OP_CLEAR_SCREEN", "OP_WRITE_LINE",
    "OP_ADD_U32", "OP_SUB_U32", "OP_MUL_U32", "OP_DIV_U32", "OP_MOD_U32",
    "OP_CMP_EQ", "OP_CMP_LT", "OP_CMP_GT", "OP_CMP_NEQ", "OP_CMP_GTE", "OP_CMP_LTE",
    "OP_MEM_WRITE", "OP_MEM_READ", "OP_MEM_COPY", "OP_MEM_INDEX",
    "OP_READ_PORT", "OP_WRITE_PORT",
    "OP_PUSH", "OP_POP", "OP_DUP", "OP_SWAP",
    "OP_BIT_AND", "OP_BIT_OR", "OP_BIT_XOR", "OP_BIT_NOT", "OP_BIT_SHL", "OP_BIT_SHR",
    "OP_INT_CLI", "OP_INT_STI", "OP_INT_N",
    "OP_MAP_PAGE", "OP_UNMAP_PAGE", "OP_GET_MEM_MAP",
    "OP_LOOP", "OP_JMP", "OP_JMP_IF_ZERO", "OP_JMP_IF_EQ", "OP_JMP_IF_LT",
    "OP_POLL_KEY", "OP_READ_LINE",
    "OP_HALT",
]