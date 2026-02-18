const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.resolveTargetQuery(.{
        .cpu_arch = .x86_64,
        .os_tag = .uefi,
        .abi = .none,
    });

    const optimize = b.standardOptimizeOption(.{});

    // 파이썬 IR 빌더 단계
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

    // 제약 사항 적용
    // exe.use_llvm = false; // Non-LLVM [cite: 18, 19]
    exe.subsystem = .EfiApplication;

    exe.step.dependOn(&python_step.step);
    b.installArtifact(exe);
}