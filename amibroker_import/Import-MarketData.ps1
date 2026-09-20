[CmdletBinding()]
param(
    [string]$MarketDataDirectory = "Z:\04_code\python\trading_tools\data\market_data",
    [string]$AmiBrokerExecutable = "C:\Program Files (x86)\AmiBroker\Broker.exe",
    [string]$DailyDatabase = "Z:\04_code\amibroker\databases\Harp_daily",
    [string]$IntradayDatabase = "Z:\04_code\amibroker\databases\Harp_intraday"
)

$ErrorActionPreference = "Stop"
$ImportDirectory = Join-Path $MarketDataDirectory "amibroker"
$DailyFile = Join-Path $ImportDirectory "daily.csv"
$IntradayFile = Join-Path $ImportDirectory "5min.csv"
$InstrumentDetailsFile = Join-Path $ImportDirectory "instrument_details.csv"
$PointValuesFile = Join-Path $ImportDirectory "point_values.csv"
$TickSizesFile = Join-Path $ImportDirectory "tick_sizes.csv"
$MarginsFile = Join-Path $ImportDirectory "margins.csv"
$CompletionManifest = Join-Path $ImportDirectory "export_complete.json"
$DailyFormat = Join-Path $PSScriptRoot "daily.format"
$IntradayFormat = Join-Path $PSScriptRoot "intraday.format"
$InstrumentDetailsFormat = Join-Path $PSScriptRoot "instrument_details.format"
$PointValuesFormat = Join-Path $PSScriptRoot "point_values.format"
$TickSizesFormat = Join-Path $PSScriptRoot "tick_sizes.format"
$MarginsFormat = Join-Path $PSScriptRoot "margins.format"
$LogDirectory = Join-Path $ImportDirectory "logs"
$LogFile = Join-Path $LogDirectory (
    "amibroker_import_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss")
)

function Write-ImportLog {
    param([string]$Message)

    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line
}

function Assert-PathExists {
    param(
        [string]$Path,
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Description was not found: $Path"
    }
}

function Import-InstrumentProperties {
    param([object]$Manifest)

    $properties = $Manifest.instrument_properties
    $imports = @(
        @("instrument details", $InstrumentDetailsFile, $InstrumentDetailsFormat, [int]$properties.details_rows),
        @("point values", $PointValuesFile, $PointValuesFormat, [int]$properties.point_values_rows),
        @("tick sizes", $TickSizesFile, $TickSizesFormat, [int]$properties.tick_sizes_rows),
        @("margin deposits", $MarginsFile, $MarginsFormat, [int]$properties.margins_rows)
    )

    foreach ($item in $imports) {
        $description = $item[0]
        $dataFile = $item[1]
        $formatFile = $item[2]
        $rowCount = $item[3]

        if ($rowCount -eq 0) {
            Write-ImportLog "Skipping $description; the export contains no rows."
            continue
        }

        $result = $ab.Import(0, $dataFile, $formatFile)
        Write-ImportLog (
            "Imported {0} rows of {1} with AmiBroker result {2}." -f
            $rowCount,
            $description,
            $result
        )
    }
}

New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null

try {
    Assert-PathExists $AmiBrokerExecutable "AmiBroker executable"
    Assert-PathExists $DailyDatabase "Daily AmiBroker database"
    Assert-PathExists $IntradayDatabase "Intraday AmiBroker database"
    Assert-PathExists $DailyFile "Prepared daily import file"
    Assert-PathExists $IntradayFile "Prepared 5-minute import file"
    Assert-PathExists $InstrumentDetailsFile "Prepared instrument details file"
    Assert-PathExists $PointValuesFile "Prepared point values file"
    Assert-PathExists $TickSizesFile "Prepared tick sizes file"
    Assert-PathExists $MarginsFile "Prepared margins file"
    Assert-PathExists $CompletionManifest "Completed-export manifest"
    Assert-PathExists $DailyFormat "Daily AmiBroker format definition"
    Assert-PathExists $IntradayFormat "Intraday AmiBroker format definition"
    Assert-PathExists $InstrumentDetailsFormat "Instrument details format definition"
    Assert-PathExists $PointValuesFormat "Point values format definition"
    Assert-PathExists $TickSizesFormat "Tick sizes format definition"
    Assert-PathExists $MarginsFormat "Margins format definition"

    $manifest = Get-Content -LiteralPath $CompletionManifest -Raw | ConvertFrom-Json
    Write-ImportLog (
        "Using export generated {0}; timezone {1}; daily rows {2}; 5-minute rows {3}." -f
        $manifest.generated_at,
        $manifest.timezone,
        $manifest.daily.exported_rows,
        $manifest.intraday.exported_rows
    )
    Write-ImportLog (
        "Instrument properties: details {0}; point values {1}; tick sizes {2}; margins {3}; margin as-of {4}." -f
        $manifest.instrument_properties.details_rows,
        $manifest.instrument_properties.point_values_rows,
        $manifest.instrument_properties.tick_sizes_rows,
        $manifest.instrument_properties.margins_rows,
        $manifest.instrument_properties.margin_as_of
    )

    $ab = New-Object -ComObject "Broker.Application"
    $ab.Visible = 1

    Write-ImportLog "Loading daily database: $DailyDatabase"
    if (-not $ab.LoadDatabase($DailyDatabase)) {
        throw "AmiBroker could not load the daily database: $DailyDatabase"
    }

    $dailyResult = $ab.Import(0, $DailyFile, $DailyFormat)
    Import-InstrumentProperties $manifest
    $ab.SaveDatabase()
    Write-ImportLog "Daily import completed with AmiBroker result $dailyResult."

    Write-ImportLog "Loading intraday database: $IntradayDatabase"
    if (-not $ab.LoadDatabase($IntradayDatabase)) {
        throw "AmiBroker could not load the intraday database: $IntradayDatabase"
    }

    $intradayResult = $ab.Import(0, $IntradayFile, $IntradayFormat)
    Import-InstrumentProperties $manifest
    $ab.SaveDatabase()
    Write-ImportLog (
        "5-minute import completed with AmiBroker result $intradayResult."
    )
    Write-ImportLog "Import completed. AmiBroker remains open on the intraday database."
}
catch {
    Write-ImportLog "IMPORT FAILED: $($_.Exception.Message)"
    Write-Error $_
    exit 1
}
finally {
    if ($null -ne $ab) {
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($ab)
    }
}
