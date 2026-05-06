# novelai-userscripts-example 環境変数読み込みスクリプト
# ロード順: .env.public (共有設定) → .env (プライベート設定)
#
# 対応フォーマット:
#   KEY=VALUE
#   KEY="VALUE"
#   KEY='VALUE'
#   PATH_ADD_KEY=$env:USERPROFILE\some\bin  # PATH への追記（$env: 展開あり）
#   # コメント行

$WorkspaceRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { Get-Location }

function Invoke-LoadEnvFile {
    param(
        [string]$FilePath,
        [bool]$Required = $false
    )

    if (-not (Test-Path $FilePath)) {
        if ($Required) {
            Write-Host "⚠️  .env.public が見つかりません: $FilePath" -ForegroundColor Yellow
        }
        return
    }

    try {
        $EnvContent = Get-Content -Path $FilePath -ErrorAction Stop
        $Count = 0

        foreach ($line in $EnvContent) {
            $trimmedLine = $line.Trim()
            if ($trimmedLine.StartsWith('#') -or [string]::IsNullOrWhiteSpace($trimmedLine)) {
                continue
            }

            if ($trimmedLine -match '^([^=]+)=(.*)$') {
                $key = $matches[1].Trim()
                $value = $matches[2].Trim()

                if ($value.StartsWith('"') -and $value.EndsWith('"')) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                elseif ($value.StartsWith("'") -and $value.EndsWith("'")) {
                    $value = $value.Substring(1, $value.Length - 2)
                }

                # $env:XXX などの PowerShell 変数を展開
                $value = $ExecutionContext.InvokeCommand.ExpandString($value)

                # PATH_ADD_* キーは PATH に追記（重複チェックあり）
                if ($key -like 'PATH_ADD_*') {
                    if ($env:Path -notlike "*$value*") {
                        $env:Path = "$value;$env:Path"
                        $Count++
                    }
                }
                else {
                    [Environment]::SetEnvironmentVariable($key, $value, "Process")
                    $Count++
                }
            }
        }

        if ($Count -gt 0) {
            $FileName = Split-Path -Leaf $FilePath
            Write-Host "📋 $FileName を読み込みました ($Count 個の設定)" -ForegroundColor Cyan
        }
    }
    catch {
        $FileName = Split-Path -Leaf $FilePath
        Write-Host "❌ $FileName の読み込みに失敗しました: $($_.Exception.Message)" -ForegroundColor Red
    }
}

# .env.public (共有設定) → .env (プライベート設定) の順に読み込み
Invoke-LoadEnvFile -FilePath (Join-Path $WorkspaceRoot ".env.public") -Required $true
Invoke-LoadEnvFile -FilePath (Join-Path $WorkspaceRoot ".env")
