"""Generate the signed Q1.15 sine table used by the FPGA DDS."""
from __future__ import annotations

import math
from pathlib import Path


TABLE_SIZE = 1024
OUTPUT = Path(__file__).resolve().parents[1] / "rtl" / "sine_1024x16.hex"


def main() -> None:
    values = []
    for index in range(TABLE_SIZE):
        value = int(round(32767.0 * math.sin(2.0 * math.pi * index / TABLE_SIZE)))
        values.append(f"{value & 0xFFFF:04x}")
    OUTPUT.write_text("\n".join(values) + "\n", encoding="ascii")
    print(f"wrote {TABLE_SIZE} samples to {OUTPUT}")


if __name__ == "__main__":
    main()

