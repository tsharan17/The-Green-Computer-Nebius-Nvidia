"""Flash and RAM footprint of a linked firmware image (ELF32/ELF64, little or big endian).

Reads only the section header table, the way `arm-none-eabi-size -A` does:
  flash = allocated sections that occupy file space (code, read-only data, initialised data's load
          image, vector tables)
  RAM   = writable allocated sections (.data copied at boot, .bss zeroed, heap/stack reservations)
Instructions are never decoded or executed. Pure function of the bytes.
"""

from __future__ import annotations

import struct
from typing import Any

SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 0x1, 0x2, 0x4
SHT_NOBITS = 8
MACHINES = {0x28: "ARM (32-bit)", 0xB7: "AArch64", 0xF3: "RISC-V", 0x5E: "Xtensa", 0x03: "x86",
            0x3E: "x86-64", 0x53: "AVR"}  # fmt: skip


class ElfError(ValueError):
    pass


def elf_footprint(data: bytes) -> dict[str, Any]:
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise ElfError("not an ELF file (upload the linked .elf, not a .hex or .bin)")
    cls, enc = data[4], data[5]
    if cls not in (1, 2) or enc not in (1, 2):
        raise ElfError("unsupported ELF class or byte order")
    e = "<" if enc == 1 else ">"
    is64 = cls == 2
    try:
        if is64:
            (e_type, e_machine, _v, _entry, _phoff, shoff, _flags, _ehsize, _phes, _phn, shentsize, shnum,
             shstrndx) = struct.unpack_from(e + "HHIQQQIHHHHHH", data, 16)  # fmt: skip
        else:
            (e_type, e_machine, _v, _entry, _phoff, shoff, _flags, _ehsize, _phes, _phn, shentsize, shnum,
             shstrndx) = struct.unpack_from(e + "HHIIIIIHHHHHH", data, 16)  # fmt: skip
    except struct.error as ex:
        raise ElfError(f"truncated ELF header: {ex}") from ex
    if shoff == 0 or shnum == 0:
        raise ElfError("the ELF has no section table (stripped of sections?); upload the unstripped build output")
    if shoff + shnum * shentsize > len(data) or shnum > 5000:
        raise ElfError("the section table lies outside the file (truncated upload?)")

    def section(i: int) -> tuple[int, int, int, int, int]:
        off = shoff + i * shentsize
        if is64:
            name, typ, flags, addr, _o, size = struct.unpack_from(e + "IIQQQQ", data, off)
        else:
            name, typ, flags, addr, _o, size = struct.unpack_from(e + "IIIIII", data, off)
        return name, typ, flags, addr, size

    _, _, _, _, strsize = section(shstrndx)
    str_off = struct.unpack_from(e + ("Q" if is64 else "I"), data, shoff + shstrndx * shentsize + (24 if is64 else 16))[0]

    def name_of(idx: int) -> str:
        start = str_off + idx
        end = data.find(b"\x00", start, str_off + strsize)
        return data[start:end if end >= 0 else start].decode("ascii", "replace")

    rows: list[dict[str, Any]] = []
    flash = ram = 0
    for i in range(shnum):
        n, typ, flags, addr, size = section(i)
        if not flags & SHF_ALLOC or size == 0:
            continue
        nm = name_of(n)
        in_flash = typ != SHT_NOBITS  # has bytes in the image: lives in flash
        in_ram = bool(flags & SHF_WRITE)  # writable at run time: occupies RAM
        flash += size if in_flash else 0
        ram += size if in_ram else 0
        rows.append({"name": nm, "size": size, "address": addr,
                     "region": "flash+RAM" if in_flash and in_ram else "flash" if in_flash else "RAM",
                     "code": bool(flags & SHF_EXECINSTR)})  # fmt: skip
    if not rows:
        raise ElfError("no allocated sections found")
    rows.sort(key=lambda r: -r["size"])
    return {
        "format": "elf64" if is64 else "elf32",
        "machine": MACHINES.get(e_machine, f"machine 0x{e_machine:x}"),
        "type": {1: "relocatable object", 2: "executable", 3: "shared object"}.get(e_type, str(e_type)),
        "flash_bytes": flash,
        "ram_bytes": ram,
        "sections": rows[:40],
        "note": "Static footprint from the section table. Heap and stack use beyond what the linker "
                "reserves is not visible in an ELF; measure peak stack on the device for a full picture.",
    }


def check_against(fp: dict[str, Any], flash_kb: float | None, sram_kb: float | None) -> list[dict[str, Any]]:
    out = []
    for label, used, limit in (("flash", fp["flash_bytes"], flash_kb), ("RAM", fp["ram_bytes"], sram_kb)):
        if limit:
            share = used / (limit * 1024)
            out.append({"name": label, "used_kb": used / 1024, "limit_kb": limit, "share": share,
                        "status": "fail" if share > 1 else "warn" if share > 0.85 else "pass"})  # fmt: skip
    return out
