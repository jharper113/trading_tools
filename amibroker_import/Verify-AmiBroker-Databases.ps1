# Windows PowerShell 5.1+. Verifies imported counts, ranges, and ES season coverage.
[CmdletBinding()]
param(
    [string]$MarketDataDirectory = "Z:\04_Code\Python\trading_tools\data\market_data",
    [string]$DailyDatabase = "Z:\04_Code\Amibroker\Databases\Harp_Daily",
    [string]$IntradayDatabase = "Z:\04_Code\Amibroker\Databases\Harp_Intraday",
    [string]$ExportManifest,
    [string]$FixturePath,
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
if (-not $ExportManifest) { $ExportManifest = Join-Path $MarketDataDirectory "amibroker\export_complete.json" }
if (-not $OutputPath) { $OutputPath = Join-Path $MarketDataDirectory "amibroker\amibroker_database_verification.json" }

function Quote-Date([object]$Quote) {
    if ($Quote.PSObject.Properties.Name -contains "DateTime") { return [datetime]$Quote.DateTime }
    return [datetime]$Quote.Date
}

function Read-LiveDatabase([object]$Ab, [string]$Path, [object]$Expected, [bool]$CheckSeasons) {
    if (-not $Ab.LoadDatabase($Path)) { throw "AmiBroker could not load database: $Path" }
    $symbols = @()
    $winter = $false
    $summer = $false
    foreach ($item in @($Expected.symbols)) {
        $stock = $Ab.Stocks.Item([string]$item.ticker)
        $count = [int]$stock.Quotations.Count
        $first = $null
        $last = $null
        if ($count -gt 0) {
            $first = (Quote-Date $stock.Quotations.Item(0)).ToString("yyyy-MM-dd HH:mm:ss")
            $last = (Quote-Date $stock.Quotations.Item($count - 1)).ToString("yyyy-MM-dd HH:mm:ss")
        }
        if ($CheckSeasons -and $item.ticker -eq "ES") {
            for ($index = 0; $index -lt $count; $index++) {
                $date = Quote-Date $stock.Quotations.Item($index)
                if ($date -ge [datetime]"2009-01-01" -and $date -lt [datetime]"2019-01-01") {
                    if ($date.Month -in @(12, 1, 2)) { $winter = $true }
                    if ($date.Month -in @(6, 7, 8)) { $summer = $true }
                    if ($winter -and $summer) { break }
                }
            }
        }
        $symbols += [ordered]@{ ticker = [string]$item.ticker; rows = $count; first = $first; last = $last }
    }
    return [ordered]@{
        symbols = $symbols
        es_research_sessions = [ordered]@{ winter = $winter; summer = $summer }
    }
}

function Compare-Snapshot([string]$Label, [object]$Expected, [object]$Actual) {
    $failures = @()
    foreach ($expectedSymbol in @($Expected.symbols)) {
        $actualSymbol = @($Actual.symbols | Where-Object { $_.ticker -eq $expectedSymbol.ticker }) | Select-Object -First 1
        if (-not $actualSymbol) {
            $failures += "$Label missing symbol $($expectedSymbol.ticker)"
            continue
        }
        if ([int64]$actualSymbol.rows -lt [int64]$expectedSymbol.rows) {
            $failures += "$Label $($expectedSymbol.ticker) is truncated: expected $($expectedSymbol.rows) rows, found $($actualSymbol.rows)"
        }
        elseif ([int64]$actualSymbol.rows -ne [int64]$expectedSymbol.rows) {
            $failures += "$Label $($expectedSymbol.ticker) row count differs: expected $($expectedSymbol.rows), found $($actualSymbol.rows)"
        }
        if ($expectedSymbol.first -and ([datetime]$actualSymbol.first -gt [datetime]$expectedSymbol.first)) {
            $failures += "$Label $($expectedSymbol.ticker) first date is truncated"
        }
        if ($expectedSymbol.last -and ([datetime]$actualSymbol.last -lt [datetime]$expectedSymbol.last)) {
            $failures += "$Label $($expectedSymbol.ticker) last date is truncated"
        }
    }
    foreach ($season in @("winter", "summer")) {
        if ($Expected.es_research_sessions.$season -and -not $Actual.es_research_sessions.$season) {
            $failures += "$Label ES is missing $season research-window evidence"
        }
    }
    return $failures
}

$manifest = Get-Content -LiteralPath $ExportManifest -Raw | ConvertFrom-Json
$ab = $null
try {
    if ($FixturePath) {
        $actual = Get-Content -LiteralPath $FixturePath -Raw | ConvertFrom-Json
    }
    else {
        $ab = New-Object -ComObject "Broker.Application"
        $actual = [ordered]@{
            daily = Read-LiveDatabase $ab $DailyDatabase $manifest.daily $false
            intraday = Read-LiveDatabase $ab $IntradayDatabase $manifest.intraday $true
        }
    }
    $failures = @()
    $failures += @(Compare-Snapshot "daily" $manifest.daily $actual.daily)
    $failures += @(Compare-Snapshot "intraday" $manifest.intraday $actual.intraday)
    $result = [ordered]@{
        status = $(if ($failures.Count -eq 0) { "PASS" } else { "FAIL" })
        checked_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        failures = $failures
        actual = $actual
    }
    $parent = Split-Path -Parent $OutputPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    if ($failures.Count -gt 0) { throw ($failures -join "; ") }
    Write-Host "AmiBroker database verification PASS: $OutputPath"
}
finally {
    if ($null -ne $ab) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($ab) }
}
