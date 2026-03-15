# Catalyst Kernel

## Overview

**Catalyst Kernel** is an experimental operating system kernel with a hybrid architecture: **Python** handles all compile-time logic generation, and **Zig** provides a minimal bare-metal runtime that executes the result.

Kernel logic is defined entirely in Python and compiled into a custom Intermediate Representation (IR) binary. The Zig runtime is a thin interpreter — a single dispatch loop — that maps opcodes to hardware operations. Zig has no knowledge of kernel policy; all of that lives in Python.

This separation keeps the Zig binary small and stable (184 KB EFI, 1.1 MB ISO), while OS behaviour is controlled from the Python side without recompiling the runtime.

## Architecture

```
frontend/entry.py  (Python)
    │
    │  compile-time: opcode emission, control flow, shell dispatch,
    │  IR serialisation, label/patch, command table generation
    │
    ▼
backend/src/kernel/ir_generated.bin  (~1 KB)
    │
    ▼
backend/src/root.zig  (Zig)
    │
    │  runtime-only: opcode dispatch loop, UEFI HAL,
    │  serial I/O, keyboard, memory, port I/O, interrupt control
    │
    ▼
BOOTX64.EFI  (181 KB)  →  output/kernel.iso  (1.03 MB)
```

## Opcode Set

Opcodes are defined in `frontend/toml/hardware.toml` and loaded dynamically by `frontend/src/opcodes.py`. The IR supports the following groups:

- **I/O**: serial write, UEFI console write, clear screen, echo line
- **Bitwise**: `and`, `or`, `xor`, `not`, `shl`, `shr`
- **Arithmetic**: `add`, `sub`, `mul`, `div`, `mod`
- **Comparison**: `eq`, `neq`, `lt`, `gt`, `gte`, `lte`
- **Memory**: `mem_read`, `mem_write`, `mem_copy`, `mem_index`
- **Stack**: `push`, `pop`, `dup`, `swap`
- **Port I/O**: `read_port`, `write_port`
- **Control flow**: `loop`, `jmp`, `jmp_if_zero`, `jmp_if_eq`, `jmp_if_lt`
- **Input**: `poll_key`, `read_line`
- **Interrupt**: `int_cli`, `int_sti`, `int_n`
- **Memory management**: `map_page`, `unmap_page`, `get_mem_map`
- **System**: `halt`

## Python Frontend

### DSL Layer

`frontend/src/nodes.py` defines IR node types used to build the instruction tree. The `u32` type supports operator overloading that produces comparison and arithmetic IR nodes directly:

```python
# Arithmetic and comparison produce IRNode trees
result = u32(0x0F) + u32(0x01)        # -> IRNode(OP_ADD_U32, [...])
cond   = u32(a) == u32(b)             # -> IRNode(OP_CMP_EQ,  [...])
```

`frontend/src/decorator.py` provides `KernelDecorator`, which accumulates IR nodes and serialises them to a flat binary buffer via `_serialize()`. It also provides label/patch helpers for forward and backward jumps:

```python
slot = kernel.emit_jmp_if_zero(cmp_node)   # write placeholder
kernel._serialize(skipped_instruction)
kernel.patch_jmp(slot)                     # back-fill correct offset
```

### Shell Compiler

`ShellCompiler` in `decorator.py` reads `frontend/toml/commands.toml` and compiles an interactive shell dispatch loop into the IR buffer. For each registered command it emits a character-by-character token comparison (using `mem_index_ir`) followed by a conditional jump chain, then the handler body.

### Entry Point

`frontend/entry.py` is the top-level build script. It instantiates `KernelDecorator`, registers device helpers, defines command handlers via the `@shell.command(...)` decorator, compiles the shell loop, appends `OP_HALT`, and writes the binary to `backend/src/kernel/ir_generated.bin`.

## Zig Backend

`backend/src/main.zig` is the UEFI entry point. It initialises the serial port, prints a boot banner, and calls `execute_python_ir(ir_data)`, where `ir_data` is the binary embedded at compile time via `@embedFile`.

`backend/src/root.zig` is the IR interpreter and hardware abstraction layer. It contains the opcode dispatch loop, UEFI console/serial drivers, port I/O helpers (`inb`/`outb`), memory operations, keyboard input, interrupt control, and UEFI memory map access.

## Features

- UEFI boot on x86_64 (q35 machine)
- Interactive shell with compile-time generated dispatch table
- Hardware port I/O (`outb`/`inb`) for device control
- Interrupt enable/disable and software interrupts
- UEFI memory map access
- Volatile `mem_copy` for MMIO regions (e.g. VGA frame buffer at 0xB8000)
- Scratch stack for multi-step DSL expressions

## Prerequisites

- **Zig 0.15.2** (API is version-sensitive; other versions may require changes)
- **Python 3.11+**
- **QEMU**
- **xorriso**
- **mtools**
- **make**
- **OVMF** (`/usr/share/ovmf/OVMF.fd`)

### Installation (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install qemu-system-x86 xorriso mtools make python3 ovmf
# Install Zig 0.15.2 from https://ziglang.org/download/
```

## Build and Run

```bash
make all
```

This will:

1. Run `zig build`, which first invokes `python3 frontend/entry.py` to generate `backend/src/kernel/ir_generated.bin`
2. Compile the Zig runtime into `zig-out/bin/BOOTX64.efi`
3. Package a bootable UEFI ISO at `output/kernel.iso`
4. Launch in QEMU with OVMF firmware

```bash
make clean
```

Removes `iso_root/`, `zig-out/`, `output/`, `.zig-cache/`, and `backend/src/kernel/`.

## Project Structure

```
.
├── LICENSE
├── Makefile
├── README.md
├── backend
│   └── src
│       ├── main.zig              # UEFI entry point; embeds ir_generated.bin
│       └── root.zig              # IR interpreter, hardware abstraction layer
├── build.zig                     # Zig build config; invokes Python as a build step
├── build.zig.zon
├── frontend
│   ├── entry.py                  # Top-level build script; emits IR and saves binary
│   ├── src
│   │   ├── __init__.py
│   │   ├── decorator.py          # KernelDecorator, ShellCompiler, label/patch API
│   │   ├── nodes.py              # IR node types and u32 DSL value type
│   │   └── opcodes.py            # OP_* constants loaded from hardware.toml
│   └── toml
│       ├── commands.toml         # Shell command definitions
│       └── hardware.toml         # Opcode assignments, device addresses, port map
└── tests
    ├── conftest.py               # Sets working directory to repo root for pytest
    └── test_ir_snapshot.py       # Snapshot tests for IR serialisation
```

## Tests

Snapshot tests verify that `_serialize()` produces byte-for-byte identical output for a fixed set of IR nodes, catching silent regressions after structural refactors.

```bash
python3 -m pytest tests/ -v
```

Tests must be run from the repository root; `tests/conftest.py` handles this automatically.

## Development Credits

Concept & Architecture: Skystarry.xyz  
Implementation Support: Claude Sonnet 4.6 & Gemini 3.1 Pro (LLM-assisted development)

## License

This project is licensed under the GNU General Public License v3.0.  
See LICENSE for details.