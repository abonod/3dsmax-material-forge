# 3dsmax-material-forge 统一检查入口（PowerShell 包装）
# 用法：
#   .\run_all_checks.ps1                 全量（含 Edge 截图，约 3 分钟）
#   .\run_all_checks.ps1 -Fast           跳过 UI 检查（约 15 秒）
#   .\run_all_checks.ps1 -Json           机器可读汇总
#   .\run_all_checks.ps1 -Only evals     只跑指定项（逗号分隔）
# 退出码 0 = 全部通过。
#
# 解释器探测顺序：$env:MF_PYTHON → .venv\Scripts\python.exe → py -3 → PATH 里的 python。
# 若系统里 python 只是 Microsoft Store 占位符，请先设 $env:MF_PYTHON 指向真实解释器：
#   $env:MF_PYTHON = "C:\Python312\python.exe"
param(
    [switch]$Fast,
    [switch]$Json,
    [string]$Only
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = $null
if ($env:MF_PYTHON -and (Test-Path $env:MF_PYTHON)) {
    $py = @($env:MF_PYTHON)
} elseif (Test-Path (Join-Path $root '.venv\Scripts\python.exe')) {
    $py = @((Join-Path $root '.venv\Scripts\python.exe'))
} else {
    $cand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cand -and $cand.Source -notlike '*WindowsApps*') {
        $py = @($cand.Source)
    } elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $py = @('py', '-3')
    }
}

if (-not $py) {
    Write-Host "未找到可用的 Python 解释器。" -ForegroundColor Red
    Write-Host "请设置环境变量后重试：  `$env:MF_PYTHON = '<python.exe 完整路径>'"
    exit 3
}

$argv = @('scripts/run_all_checks.py')
if ($Fast) { $argv += '--fast' }
if ($Json) { $argv += '--json' }
if ($Only) { $argv += @('--only', $Only) }

$exe = $py[0]
$pre = @()
if ($py.Count -gt 1) { $pre = @($py[1..($py.Count - 1)]) }

& $exe @pre @argv
exit $LASTEXITCODE
