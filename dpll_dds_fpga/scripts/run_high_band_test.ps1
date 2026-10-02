$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$output = Join-Path $env:TEMP "tb_dpll_high_band.out"

& iverilog -g2012 -o $output `
    "$root\rtl\unsigned_serial_divider.v" `
    "$root\rtl\coarse_frequency_estimator.v" `
    "$root\rtl\sine_rom_dp.v" `
    "$root\rtl\cordic_atan2.v" `
    "$root\rtl\iq_phase_detector.v" `
    "$root\rtl\dpll_controller.v" `
    "$root\rtl\dds_output.v" `
    "$root\rtl\dpll_dds_core.v" `
    "$root\sim\tb_dpll_high_band.v"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location "$root\sim"
try {
    & vvp $output
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
    Remove-Item -LiteralPath $output -Force -ErrorAction SilentlyContinue
}
