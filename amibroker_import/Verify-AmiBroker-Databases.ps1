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

function Get-ObjectProperty([object]$Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    if ([System.Runtime.InteropServices.Marshal]::IsComObject($Object)) {
        $value = $Object.GetType().InvokeMember(
            $Name,
            [System.Reflection.BindingFlags]::GetProperty,
            $null,
            $Object,
            [object[]]@()
        )
        return ,$value
    }
    return ,($Object.$Name)
}

function Get-ObjectItem([object]$Object, [object]$Index) {
    if ($null -eq $Object) { return $null }
    if ([System.Runtime.InteropServices.Marshal]::IsComObject($Object)) {
        $value = $Object.GetType().InvokeMember(
            "Item",
            [System.Reflection.BindingFlags]::GetProperty,
            $null,
            $Object,
            [object[]]@($Index)
        )
        return ,$value
    }
    return ,($Object.Item($Index))
}

function Quote-Date([object]$Quote) {
    if ($null -eq $Quote) { throw "quotation is null" }
    $value = Get-ObjectProperty $Quote "Date"
    if ($null -eq $value) { throw "quotation has no date" }
    return [datetime]$value
}

function Read-LiveDatabase([object]$Ab, [string]$Path, [object]$Expected, [bool]$CheckSeasons) {
    if (-not $Ab.LoadDatabase($Path)) { throw "AmiBroker could not load database: $Path" }
    $stocks = Get-ObjectProperty $Ab "Stocks"
    if ($null -eq $stocks) {
        throw "AmiBroker Stocks collection is unavailable after loading $Path (active database: $(Get-ObjectProperty $Ab "DatabasePath"))"
    }
    if ([int](Get-ObjectProperty $stocks "Count") -eq 0) {
        throw "AmiBroker Stocks collection is empty after loading $Path (active database: $(Get-ObjectProperty $Ab "DatabasePath"))"
    }
    $symbols = @()
    $diagnostics = @()
    $winter = $false
    $summer = $false
    foreach ($item in @($Expected.symbols)) {
        $ticker = [string]$item.ticker
        $count = 0
        $first = $null
        $last = $null
        try {
            $stage = "stock lookup"
            $stock = Get-ObjectItem $stocks $ticker
            if ($null -eq $stock) { throw "stock lookup returned null" }
            $stage = "quotation collection"
            $quotations = Get-ObjectProperty $stock "Quotations"
            if ($null -eq $quotations) { throw "quotation collection is null" }
            $stage = "quotation count"
            $count = [int](Get-ObjectProperty $quotations "Count")
            if ($count -gt 0) {
                $stage = "first quotation"
                $firstQuote = Get-ObjectItem $quotations 0
                if ($null -eq $firstQuote) { throw "quotation 0 is null" }
                $first = (Quote-Date $firstQuote).ToString("yyyy-MM-dd HH:mm:ss")
                $lastIndex = $count - 1
                $stage = "last quotation"
                $lastQuote = Get-ObjectItem $quotations $lastIndex
                if ($null -eq $lastQuote) { throw "quotation $lastIndex is null" }
                $last = (Quote-Date $lastQuote).ToString("yyyy-MM-dd HH:mm:ss")
            }
            if ($CheckSeasons -and $ticker -eq "ES") {
                $stage = "ES season quotations"
                for ($index = 0; $index -lt $count; $index++) {
                    $quote = Get-ObjectItem $quotations $index
                    if ($null -eq $quote) { throw "quotation $index is null" }
                    $date = Quote-Date $quote
                    if ($date -ge [datetime]"2009-01-01" -and $date -lt [datetime]"2019-01-01") {
                        if ($date.Month -in @(12, 1, 2)) { $winter = $true }
                        if ($date.Month -in @(6, 7, 8)) { $summer = $true }
                        if ($winter -and $summer) { break }
                    }
                }
            }
        }
        catch {
            $diagnostics += "$Path ticker ${ticker} at ${stage}: $($_.Exception.Message)"
        }
        $symbols += [ordered]@{ ticker = $ticker; rows = $count; first = $first; last = $last }
    }
    return [ordered]@{
        symbols = $symbols
        diagnostics = $diagnostics
        es_research_sessions = [ordered]@{ winter = $winter; summer = $summer }
    }
}

function Compare-Snapshot([string]$Label, [object]$Expected, [object]$Actual) {
    $failures = @()
    $failures += @($Actual.diagnostics | Where-Object { $_ })
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
        if ($expectedSymbol.first) {
            if (-not $actualSymbol.first) {
                $failures += "$Label $($expectedSymbol.ticker) first date is unavailable"
            }
            elseif ([datetime]$actualSymbol.first -gt [datetime]$expectedSymbol.first) {
                $failures += "$Label $($expectedSymbol.ticker) first date is truncated"
            }
        }
        if ($expectedSymbol.last) {
            if (-not $actualSymbol.last) {
                $failures += "$Label $($expectedSymbol.ticker) last date is unavailable"
            }
            elseif ([datetime]$actualSymbol.last -lt [datetime]$expectedSymbol.last) {
                $failures += "$Label $($expectedSymbol.ticker) last date is truncated"
            }
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
        $ab.Visible = 1
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
    if ($failures.Count -gt 0) {
        $preview = @($failures | Select-Object -First 5) -join "; "
        throw "AmiBroker verification FAIL ($($failures.Count) findings). Report: $OutputPath. First findings: $preview"
    }
    Write-Host "AmiBroker database verification PASS: $OutputPath"
}
finally {
    if ($null -ne $ab) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($ab) }
}
