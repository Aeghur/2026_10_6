"""Generate the Quartus MIF used by the switchable zero-crossing DDS."""
from __future__ import annotations

import math
from pathlib import Path


OUTPUT = Path(__file__).with_name("sine_1024x16.mif")


def main() -> None:
    lines = [
        "WIDTH=16;",
        "DEPTH=1024;",
        "ADDRESS_RADIX=HEX;",
        "DATA_RADIX=HEX;",
        "CONTENT BEGIN",
    ]
    for index in range(1024):
        value = round(32767.0 * math.sin(2.0 * math.pi * index / 1024))
        lines.append(f"    {index:03X} : {value & 0xFFFF:04X};")
    lines.append("END;")
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
