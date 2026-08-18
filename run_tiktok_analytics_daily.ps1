$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$logDirectory = Join-Path $PSScriptRoot "data\logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logPath = Join-Path $logDirectory ("tiktok-analytics-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

try {
    "[$(Get-Date -Format o)] Importando Affiliate Orders" | Add-Content -LiteralPath $logPath
    & python "import_affiliate_reports.py" *>> $logPath
    $affiliateExitCode = $LASTEXITCODE

    "[$(Get-Date -Format o)] Coletando TikTok Studio" | Add-Content -LiteralPath $logPath
    & python "collect_tiktok_analytics.py" --scheduled *>> $logPath
    $analyticsExitCode = $LASTEXITCODE

    if ($affiliateExitCode -ne 0) {
        exit $affiliateExitCode
    }
    exit $analyticsExitCode
}
catch {
    $_ | Out-String | Add-Content -LiteralPath $logPath
    exit 1
}
