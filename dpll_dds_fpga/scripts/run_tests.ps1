$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$rtl = Join-Path $projectRoot 'rtl'
$sim = Join-Path $projectRoot 'sim'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('dpll_dds_tests_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null

function Invoke-Test {
    param(
        [string]$Name,
        [string[]]$Sources
    )
    $output = Join-Path $testRoot ($Name + '.out')
    & iverilog -g2012 -o $output @Sources
    if ($LASTEXITCODE -ne 0) { throw "iverilog failed: $Name" }
    & vvp $output
    if ($LASTEXITCODE -ne 0) { throw "vvp failed: $Name" }
}

Push-Location $sim
try {
    Invoke-Test 'coarse' @(
        (Join-Path $rtl 'unsigned_serial_divider.v'),
        (Join-Path $rtl 'coarse_frequency_estimator.v'),
        (Join-Path $sim 'tb_coarse_frequency_estimator.v'))
    Invoke-Test 'dds' @(
        (Join-Path $rtl 'dds_output.v'),
        (Join-Path $sim 'tb_dds_output.v'))
    Invoke-Test 'dpll' @(
        (Join-Path $rtl 'unsigned_serial_divider.v'),
        (Join-Path $rtl 'coarse_frequency_estimator.v'),
        (Join-Path $rtl 'sine_rom_dp.v'),
        (Join-Path $rtl 'cordic_atan2.v'),
        (Join-Path $rtl 'iq_phase_detector.v'),
        (Join-Path $rtl 'dpll_controller.v'),
        (Join-Path $rtl 'dds_output.v'),
        (Join-Path $rtl 'dpll_dds_core.v'),
        (Join-Path $sim 'tb_dpll_dds_core.v'))
    Invoke-Test 'uart' @(
        (Join-Path $rtl 'dpll_uart_rx.v'),
        (Join-Path $rtl 'dpll_uart_tx.v'),
        (Join-Path $rtl 'dpll_uart_bridge.v'),
        (Join-Path $sim 'tb_uart_bridge.v'))

    $topOutput = Join-Path $testRoot 'top_syntax.out'
    $allRtl = Get-ChildItem -LiteralPath $rtl -Filter '*.v' | ForEach-Object FullName
    & iverilog -g2012 -s dpll_dds_top -o $topOutput @allRtl
    if ($LASTEXITCODE -ne 0) { throw 'top-level elaboration failed' }
    Write-Output 'PASS: standalone top-level elaboration'
    Invoke-Test 'top_smoke' @($allRtl + (Join-Path $sim 'tb_top_smoke.v'))
}
finally {
    Pop-Location
    $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $resolvedTest = [System.IO.Path]::GetFullPath($testRoot)
    if ($resolvedTest.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedTest -Recurse -Force
    }
}

