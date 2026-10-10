# novelai-userscripts-example 開発用サーバー起動/停止スクリプト
#
# 使い方:
#   .\scripts\dev-ctl.ps1 start   [-Target backend|frontend|all]
#   .\scripts\dev-ctl.ps1 stop    [-Target backend|frontend|all]
#   .\scripts\dev-ctl.ps1 restart [-Target backend|frontend|all]
#   .\scripts\dev-ctl.ps1 status  [-Target backend|frontend|all]
#   .\scripts\dev-ctl.ps1 restart -Http   # 証明書があっても http で動かす(デバッグ用)
#
# https: data/certs/server.* (scripts/make_lan_cert.py) があれば https で起動する。-Http を付けるか、
#   環境変数 NAI_HTTP=1 なら http。どちらで起動したかはサーバーごとに data/run/<名前>.scheme に残し、
#   status と MCP のツール(backend.scheme)が使う。
#   フロント(Vite)とバックエンドは同じ方式でそろえること(片方だけ変えると中継や直接の呼び出しが合わない)。
#
# なぜこのスクリプトが要るか:
#   `uv run ... uvicorn --reload` をポート番号から逆引きしたPIDでtaskkillすると、
#   Windows環境で「所有プロセスが存在しないのにLISTEN状態のまま残るソケット」が
#   複数回発生することを実機で確認した(--reloadのリローダー/ワーカー分離、および
#   `uv run`のラッパー越しにプロセスを起動していたことが原因と推測される)。
#   一度この状態になると、たとえプロセスをすべて終了しても該当ポートが
#   解放されず、新しいコードを反映したサーバーを起動できなくなる。
#
#   対策として、このスクリプトは:
#     1. `uv run` を経由せず venv の python.exe / node.exe を直接起動する
#        (ラッパー越しだと Start-Process の PID が実際のサーバープロセスと
#        一致しない可能性があるため)
#     2. バックエンドは --reload を付けない(コード変更の反映は明示的に
#        restart で行う)
#     3. 起動時にPIDファイルへ実プロセスIDを記録し、停止時は必ずそのPIDだけを
#        対象にする(ポート番号からのPID逆引きに頼らない)
#   という3点でソケットの残留を避ける。

param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action = 'status',

    [ValidateSet('backend', 'frontend', 'all')]
    [string]$Target = 'all',

    # 証明書があっても http で動かす(デバッグ用。ブラウザの開発ツールやプロキシで中身を見たいときなど)
    [switch]$Http
)

$WorkspaceRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { Get-Location }
$RunDir = Join-Path $WorkspaceRoot 'data\run'
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

# LAN 用の証明書(scripts/make_lan_cert.py)があれば、バックエンドもフロントも https で開く。
# -Http か環境変数 NAI_HTTP=1 なら、証明書があっても http(フロントの Vite にも NAI_HTTP で伝える)
$CertFile = Join-Path $WorkspaceRoot 'data\certs\server.crt'
$KeyFile = Join-Path $WorkspaceRoot 'data\certs\server.key'
$ForceHttp = $Http -or ($env:NAI_HTTP -eq '1')
$UseHttps = (Test-Path $CertFile) -and (Test-Path $KeyFile) -and (-not $ForceHttp)
if ($ForceHttp) { $env:NAI_HTTP = '1' } else { Remove-Item Env:NAI_HTTP -ErrorAction SilentlyContinue }
$Scheme = if ($UseHttps) { 'https' } else { 'http' }

function Get-StartedScheme($svc) {
    # そのサーバーを起動したときの方式(記録が無ければ空)
    if (-not (Test-Path $svc.SchemeFile)) { return '' }
    return (Get-Content $svc.SchemeFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
}
$BackendArgs = @('-m', 'uvicorn', 'python.server:app', '--app-dir', 'src', '--host', '0.0.0.0', '--port', '8000')
if ($UseHttps) { $BackendArgs += @('--ssl-certfile', $CertFile, '--ssl-keyfile', $KeyFile) }

$Services = @{
    backend  = @{
        PidFile          = Join-Path $RunDir 'backend.pid'
        LogFile          = Join-Path $RunDir 'backend.log'
        ErrFile          = Join-Path $RunDir 'backend.err.log'
        FilePath         = Join-Path $WorkspaceRoot '.venv\Scripts\python.exe'
        # 0.0.0.0 で待ち受け、同じLAN上の端末(スマホ等)からもアクセスできるようにする。
        ArgumentList     = $BackendArgs
        WorkingDirectory = $WorkspaceRoot
        SchemeFile       = Join-Path $RunDir 'backend.scheme'
        # {0} に http / https が入る
        DisplayUrl       = '{0}://0.0.0.0:8000 (LAN reachable)'
    }
    frontend = @{
        PidFile          = Join-Path $RunDir 'frontend.pid'
        LogFile          = Join-Path $RunDir 'frontend.log'
        ErrFile          = Join-Path $RunDir 'frontend.err.log'
        FilePath         = (Get-Command node).Source
        ArgumentList     = @('node_modules\vite\bin\vite.js')
        WorkingDirectory = $WorkspaceRoot
        SchemeFile       = Join-Path $RunDir 'frontend.scheme'
        DisplayUrl       = '{0}://localhost:5173 / {0}://novelai.lan:5173'
    }
}

function Get-RunningProcess($svc) {
    if (-not (Test-Path $svc.PidFile)) { return $null }
    $storedId = Get-Content $svc.PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $storedId) { return $null }
    return Get-Process -Id $storedId -ErrorAction SilentlyContinue
}

function Start-Service([string]$Name, [hashtable]$Svc) {
    $existing = Get-RunningProcess $Svc
    if ($existing) {
        Write-Host "⚠️  $Name は既に起動中です (PID $($existing.Id))" -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path $Svc.FilePath)) {
        Write-Host "❌ ${Name}: 実行ファイルが見つかりません: $($Svc.FilePath)" -ForegroundColor Red
        return
    }

    $proc = Start-Process -FilePath $Svc.FilePath -ArgumentList $Svc.ArgumentList `
        -WorkingDirectory $Svc.WorkingDirectory `
        -RedirectStandardOutput $Svc.LogFile -RedirectStandardError $Svc.ErrFile `
        -WindowStyle Hidden -PassThru

    Set-Content -Path $Svc.PidFile -Value $proc.Id
    Set-Content -Path $Svc.SchemeFile -Value $Scheme
    Write-Host "✅ $Name を起動しました (PID $($proc.Id)) → $($Svc.DisplayUrl -f $Scheme)" -ForegroundColor Green
    Write-Host "   ログ: $($Svc.LogFile)"
}

function Stop-Service([string]$Name, [hashtable]$Svc) {
    $proc = Get-RunningProcess $Svc
    if (-not $proc) {
        Write-Host "ℹ️  $Name は起動していません" -ForegroundColor DarkGray
        Remove-Item $Svc.PidFile -ErrorAction SilentlyContinue
        return
    }
    Stop-Process -Id $proc.Id -Force
    Remove-Item $Svc.PidFile -ErrorAction SilentlyContinue
    Write-Host "🛑 $Name を停止しました (PID $($proc.Id))" -ForegroundColor Cyan
}

function Show-Status([string]$Name, [hashtable]$Svc) {
    $proc = Get-RunningProcess $Svc
    if ($proc) {
        $started = Get-StartedScheme $Svc
        if (-not $started) { $started = $Scheme }
        Write-Host "🟢 $Name : 起動中 (PID $($proc.Id)) → $($Svc.DisplayUrl -f $started)"
    }
    else {
        Write-Host "⚪ $Name : 停止中"
    }
}

$targets = if ($Target -eq 'all') { @('backend', 'frontend') } else { @($Target) }

# 片方だけ方式を変えて起動すると、フロントの中継やバックエンドへの直接の呼び出しが合わなくなる
if ($Target -ne 'all' -and $Action -in @('start', 'restart')) {
    $other = if ($Target -eq 'backend') { 'frontend' } else { 'backend' }
    $otherSvc = $Services[$other]
    $previous = Get-StartedScheme $otherSvc
    if ((Get-RunningProcess $otherSvc) -and $previous -and $previous -ne $Scheme) {
        Write-Host "⚠️  $other は $previous で動いています。方式を変えるときは -Target all でそろえてください" -ForegroundColor Yellow
    }
}

foreach ($t in $targets) {
    $svc = $Services[$t]
    switch ($Action) {
        'start' { Start-Service $t $svc }
        'stop' { Stop-Service $t $svc }
        'restart' { Stop-Service $t $svc; Start-Sleep -Seconds 1; Start-Service $t $svc }
        'status' { Show-Status $t $svc }
    }
}
