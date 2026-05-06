# novelai-userscripts-example 開発環境セットアップスクリプト（Windows PowerShell 5.1+）
# 用途: 初回セットアップ時に実行
# 機能:
#   1. PowerShell プロフィールへの自動読み込み設定
#   2. .env ファイルの初期化

param(
    [switch]$SkipProfile    # プロフィール設定をスキップ
)

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "🚀 novelai-userscripts-example 開発環境セットアップ" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# ========================================
# 1. PowerShell プロフィール設定
# ========================================

if (-not $SkipProfile) {
    Write-Host "📋 ステップ 1: PowerShell プロフィール設定" -ForegroundColor Yellow

    $RepoRoot = Split-Path -Parent $PSScriptRoot

    $ProfileSection = @"

# =====================================================
# novelai-userscripts-example - 環境変数読み込み
# =====================================================
`$LoadEnvScript = Join-Path "$RepoRoot" "scripts\load-env.ps1"
if (Test-Path `$LoadEnvScript) {
    & `$LoadEnvScript
}
"@

    # CurrentUserAllHosts (profile.ps1) を PS5 / PS7 の両方に書き込む
    # → 通常ターミナル・VS Code 統合ターミナル・PowerShell Extension すべてで読まれる
    $allHostsProfiles = @(
        "$env:USERPROFILE\Documents\WindowsPowerShell\profile.ps1",  # PS5
        "$env:USERPROFILE\Documents\PowerShell\profile.ps1"           # PS7
    )

    foreach ($ProfilePath in $allHostsProfiles) {
        $ProfileDir = Split-Path -Parent $ProfilePath

        if (-not (Test-Path $ProfileDir)) {
            New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
        }

        if (Test-Path $ProfilePath) {
            $CurrentContent = Get-Content $ProfilePath -Raw
            if ($CurrentContent -match "novelai-userscripts-example") {
                Write-Host "   ℹ️  設定は既に存在します（スキップ）: $ProfilePath"
                continue
            }
            $BackupPath = "$ProfilePath.backup.$(Get-Date -Format 'yyyyMMddHHmmss')"
            Copy-Item -Path $ProfilePath -Destination $BackupPath -Force
            Write-Host "   ✅ バックアップ作成: $BackupPath"
            Add-Content -Path $ProfilePath -Value $ProfileSection
        }
        else {
            Set-Content -Path $ProfilePath -Value $ProfileSection.TrimStart()
        }

        Write-Host "   ✅ 設定を追加: $ProfilePath"
    }

    Write-Host ""
}

# ========================================
# 2. .env ファイル初期化
# ========================================

Write-Host "📋 ステップ 2: 環境変数ファイル (.env) の初期化" -ForegroundColor Yellow

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvExamplePath = Join-Path $RepoRoot ".env.example"
$EnvPath = Join-Path $RepoRoot ".env"

if (-not (Test-Path $EnvPath) -and (Test-Path $EnvExamplePath)) {
    Copy-Item -Path $EnvExamplePath -Destination $EnvPath
    Write-Host "   ✅ .env ファイルを作成: $EnvPath"
    Write-Host "   ℹ️  .env.example をコピーしました"
    Write-Host "   📝 必要に応じて .env を編集してください"
}
elseif (Test-Path $EnvPath) {
    Write-Host "   ℹ️  .env ファイルは既に存在します"
}
else {
    Write-Host "   ⚠️  .env.example が見つかりません"
}

Write-Host ""

# ========================================
# 完了
# ========================================

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "✅ セットアップ完了！" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "🎯 次のステップ:" -ForegroundColor Cyan
Write-Host "  1. PowerShell を再起動する（プロフィールを反映するため）"
Write-Host "  2. uv が使えることを確認："
Write-Host "     uv --version"
Write-Host "  3. Python 環境を初期化："
Write-Host "     uv sync"
Write-Host ""
