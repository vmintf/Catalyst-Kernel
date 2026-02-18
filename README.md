# Catalyst Kernel

## Overview

**Catalyst Kernel** is an experimental operating system kernel project that explores a unique hybrid architecture. It combines **Python**, acting as the high-level "brain" for logic generation, with **Zig**, which handles low-level hardware interfacing and execution.

The primary goal of this project is to build a functional OS kernel (and eventually a full OS) where the core logic is defined in Python and compiled into a custom Intermediate Representation (IR), which is then executed by a bare-metal Zig runtime.

## Features

- **Hybrid Architecture**:
  - **Python (`kernel.py`)**: Defines kernel logic, opcodes, and generates bytecode (IR).
  - **Zig (`src/*.zig`)**: Provides the UEFI bootloader, hardware abstraction layer (HAL), and the IR interpreter.
- **Custom IR**: A stack-based bytecode system supporting arithmetic, memory operations, loops, and hardware I/O.
- **UEFI Boot**: Boots directly on UEFI-compliant systems (x86_64).
- **Compile-Time Evaluation**: Leverages Zig's `comptime` capabilities to optimize the execution of the generated IR.

## Development Environment

This project is designed to be developed in Linux environments. Supported environments include:

- **Ubuntu**
- **WSL (Windows Subsystem for Linux)**

## Prerequisites

To build and run this project, you need the following tools installed:

- **Zig** (Latest stable or nightly)
- **Python 3.11+**
- **QEMU** (for emulation)
- **xorriso** (for ISO creation)
- **mtools** (for FAT filesystem manipulation)
- **Make**

### Installation (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install qemu-system-x86 xorriso mtools make python3
# Install Zig from https://ziglang.org/download/
```

## Build and Run

The project uses a `Makefile` to automate the build process.

1. **Build and Run (QEMU)**:
   ```bash
   make all
   ```
   This command will:
   - Generate the IR using Python.
   - Compile the Zig kernel.
   - Create a bootable UEFI ISO.
   - Launch the kernel in QEMU.

2. **Clean Build Artifacts**:
   ```bash
   make clean
   ```

## Project Structure

- `kernel.py`: The Python script that defines the kernel logic and compiles it into `src/ir_generated.bin`.
- `src/main.zig`: The main entry point for the UEFI application.
- `src/root.zig`: Contains the IR interpreter and hardware definitions (Serial, etc.).
- `build.zig`: Zig build configuration, ensuring Python runs before the Zig build.
- `Makefile`: Orchestrates the entire build and emulation workflow.

## License

This project is open source.
