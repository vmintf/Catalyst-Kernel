.PHONY: all build iso run clean

EFI_SRC = iso_root/EFI/BOOT/BOOTX64.EFI
EFI_BIN = zig-out/bin/BOOTX64.efi
EFI_IMG = iso_root/EFI/BOOT/efi.img
ISO     = output/kernel.iso
OVMF    = /usr/share/ovmf/OVMF.fd

all: run

build:
	zig build --cache-dir /tmp/zig-cache --global-cache-dir /tmp/zig-global-cache
	mkdir -p iso_root/EFI/BOOT output
	cp $(EFI_BIN) $(EFI_SRC)
	dd if=/dev/zero of=$(EFI_IMG) bs=512 count=1024
	mkfs.fat -F 12 $(EFI_IMG)
	mmd -i $(EFI_IMG) ::/EFI ::/EFI/BOOT
	mcopy -i $(EFI_IMG) $(EFI_SRC) ::/EFI/BOOT/

iso: build
	xorriso -as mkisofs \
	   -o $(ISO) \
	   -eltorito-alt-boot \
	   -e EFI/BOOT/efi.img \
	   -no-emul-boot \
	   -isohybrid-gpt-basdat \
	   iso_root

run: iso
	qemu-system-x86_64 \
	   -bios $(OVMF) \
	   -cdrom $(ISO) \
	   -machine q35 \
	   -display gtk \
	   -serial stdio

clean:
	rm -rf iso_root zig-out $(ISO) .zig-cache /tmp/zig-cache /tmp/zig-global-cache output backend/src/kernel