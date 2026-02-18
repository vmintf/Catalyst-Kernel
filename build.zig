// Catalyst Kernel - A bare-metal UEFI kernel using Python DSL and Zig comptime AOT compilation.
// Copyright (C) 2025  Skystarry.xyz
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// See LICENSE for details.

const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.resolveTargetQuery(.{
        .cpu_arch = .x86_64,
        .os_tag = .uefi,
        .abi = .none,
    });

    const optimize = b.standardOptimizeOption(.{});

    // Python IR builder stage
    const python_step = b.addSystemCommand(&.{ "python", "kernel.py" });

    const mod = b.addModule("LinuxKernel", .{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
    });

    const exe = b.addExecutable(.{
        .name = "BOOTX64",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/main.zig"),
            .target = target,
            .optimize = optimize,
            .imports = &.{ .{ .name = "LinuxKernel", .module = mod } },
        }),
    });

    // constraint apply
    exe.subsystem = .EfiApplication;

    exe.step.dependOn(&python_step.step);
    b.installArtifact(exe);
}