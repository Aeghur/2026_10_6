Imported from https://github.com/Aeghur/2026_10_6

Branch: main
Commit: 0a09a2bf6920f2ace04846687a884a358672e62d
Path: phase_board/

Local change: controller.py accepts output_command_frequency_hz to preserve
the calibrated UART frequency in Signal/ACK while fitting phase and diagnostic
source frequency in nominal physical-frequency units. All original validation
thresholds and default behavior are retained.
The original project and hardware firmware were not replaced by this import.
