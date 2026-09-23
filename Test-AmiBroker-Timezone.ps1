# Windows PowerShell 5.1+. Verifies that Harp_Intraday is already Eastern time.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Broker,
    [Parameter(Mandatory=$true)][string]$Database,
    [Parameter(Mandatory=$true)][string]$ProjectTemplate,
    [Parameter(Mandatory=$true)][string]$WorkDir,
    [string]$CsvFixture
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Save-Json($Value, [string]$Path) {
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Add-BatchStep($Document, $Root, [string]$Action, [string]$Param='') {
    $step = $Document.CreateElement('Step')
    foreach ($pair in @(@('Action', $Action), @('Param', $Param))) {
        $node = $Document.CreateElement($pair[0])
        if ($pair[0] -eq 'Param') {
            $node.InnerText = ([string]$pair[1]).Replace('\', '\\')
        } else {
            $node.InnerText = $pair[1]
        }
        [void]$step.AppendChild($node)
    }
    [void]$Root.AppendChild($step)
}
function Set-XmlValues($Document, [string[]]$Names, [string]$Value) {
    foreach ($name in $Names) {
        foreach ($node in @($Document.SelectNodes("//*[local-name()='$name']"))) { $node.InnerText = $Value }
    }
}
function Test-SessionPair($Rows) {
    $times = @($Rows | ForEach-Object { [int]$_.TimeNum })
    return ($times -contains 93000) -and ($times -contains 155500)
}
function Invoke-AmiBrokerBatch([string]$BrokerPath, [string]$BatchPath) {
    # Broker.exe is a Windows GUI process, so invoking it with '&' does not
    # reliably populate $LASTEXITCODE or wait for the batch to finish.
    $quotedBatchPath = '"' + $BatchPath + '"'
    $brokerProcess = Start-Process -FilePath $BrokerPath `
        -ArgumentList @('/runbatch', $quotedBatchPath, '/exit') `
        -Wait -PassThru
    if ($brokerProcess.ExitCode -ne 0) {
        throw "AmiBroker timezone preflight exited $($brokerProcess.ExitCode)"
    }
}

New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
$csvPath = Join-Path $WorkDir 'timezone_preflight.csv'
$reportPath = Join-Path $WorkDir 'timezone_preflight.json'
$baseReport = [ordered]@{
    schema_version = 1; status = 'FAIL'; timezone = 'America/Detroit'; database = $Database
    timeshift_seconds = $null; interval_seconds = $null; winter_sessions = @(); summer_sessions = @()
    csv_path = $csvPath; csv_sha256 = $null; rows = 0; reason = $null
}

try {
    if ($CsvFixture) {
        Copy-Item -LiteralPath $CsvFixture -Destination $csvPath -Force
    } else {
        if (Test-Path -LiteralPath $csvPath) { Remove-Item -LiteralPath $csvPath -Force }
        $workspacePath = Join-Path $Database 'broker.workspace'
        foreach ($required in @($Broker, $workspacePath, $ProjectTemplate, (Join-Path $PSScriptRoot 'AmiBroker_Timezone_Preflight.afl'))) {
            if (-not (Test-Path -LiteralPath $required)) { throw "Required preflight input does not exist: $required" }
        }
        $formulaPath = Join-Path $PSScriptRoot 'AmiBroker_Timezone_Preflight.afl'
        $formula = [IO.File]::ReadAllText($formulaPath).Replace("`r`n", "`n").Replace("`r", "`n")
        $project = New-Object System.Xml.XmlDocument
        $project.XmlResolver = $null
        $project.Load($ProjectTemplate)
        Set-XmlValues $project @('FormulaPath') $formulaPath
        Set-XmlValues $project @('FormulaContent') $formula
        Set-XmlValues $project @('Periodicity') '11'
        Set-XmlValues $project @('ChartInterval') '300'
        Set-XmlValues $project @('RangeType','BacktestRangeType') '3'
        Set-XmlValues $project @('FromDate','RangeFromDate','BacktestRangeFromDate') '2009-01-01'
        Set-XmlValues $project @('ToDate','RangeToDate','BacktestRangeToDate') '2019-01-01'
        $projectPath = Join-Path $WorkDir 'timezone_preflight.apx'
        $project.Save($projectPath)

        $batch = New-Object System.Xml.XmlDocument
        $root = $batch.CreateElement('AmiBroker-Batch'); $root.SetAttribute('CompactMode', '0'); [void]$batch.AppendChild($root)
        Add-BatchStep $batch $root 'LoadDatabase' $workspacePath
        Add-BatchStep $batch $root 'LoadProject' $projectPath
        Add-BatchStep $batch $root 'SetCurrentSymbol' 'ES'
        Add-BatchStep $batch $root 'Explore' ''
        Add-BatchStep $batch $root 'Export' $csvPath
        $batchPath = Join-Path $WorkDir 'timezone_preflight.abb'
        $batch.Save($batchPath)
        Invoke-AmiBrokerBatch $Broker $batchPath
    }

    if (-not (Test-Path -LiteralPath $csvPath -PathType Leaf)) {
        throw 'Timezone preflight did not create a CSV. Verify that Harp_Intraday contains ES data in the 2009-01-01 through 2019-01-01 research window.'
    }
    $rows = @(Import-Csv -LiteralPath $csvPath)
    if (-not $rows) {
        throw 'Timezone preflight CSV is empty. Harp_Intraday has no qualifying ES data in the 2009-01-01 through 2019-01-01 research window.'
    }
    $requiredColumns = @('DateTime','TimeNum','TimeShiftSeconds','IntervalSeconds')
    foreach ($column in $requiredColumns) {
        if ($rows[0].PSObject.Properties.Name -notcontains $column) { throw "Timezone CSV is missing $column" }
    }
    $seen = @{}
    foreach ($row in $rows) {
        $parsed = [datetime]::MinValue
        if (-not [datetime]::TryParse([string]$row.DateTime, [ref]$parsed)) { throw 'Timezone CSV contains an invalid date' }
        $timeValue = 0; $shiftValue = 0; $intervalValue = 0
        if (-not [int]::TryParse([string]$row.TimeNum, [ref]$timeValue) -or
            -not [int]::TryParse([string]$row.TimeShiftSeconds, [ref]$shiftValue) -or
            -not [int]::TryParse([string]$row.IntervalSeconds, [ref]$intervalValue)) {
            throw 'Timezone CSV contains a malformed numeric value'
        }
        $key = "$($parsed.ToString('o'))|$timeValue"
        if ($seen.ContainsKey($key)) { throw 'Timezone CSV contains duplicate rows' }
        $seen[$key] = $true
        if ($shiftValue -ne 0) { throw 'AmiBroker database time shift is not zero' }
        if ($intervalValue -ne 300) { throw "AmiBroker analysis interval is $intervalValue seconds; expected 300 (5-minute)" }
        $row | Add-Member -NotePropertyName ParsedDate -NotePropertyValue $parsed
    }
    $pairs = @($rows | Group-Object { $_.ParsedDate.ToString('yyyy-MM-dd') })
    $winter = @($pairs | Where-Object { ([datetime]$_.Name).Month -eq 1 -and (Test-SessionPair $_.Group) } | ForEach-Object Name | Sort-Object)
    $summer = @($pairs | Where-Object { ([datetime]$_.Name).Month -eq 7 -and (Test-SessionPair $_.Group) } | ForEach-Object Name | Sort-Object)
    if (-not $winter) { throw 'Timezone export has no complete winter 09:30/15:55 session' }
    if (-not $summer) { throw 'Timezone export has no complete summer 09:30/15:55 session' }
    $baseReport.status = 'PASS'; $baseReport.timeshift_seconds = 0; $baseReport.interval_seconds = 300
    $baseReport.winter_sessions = $winter; $baseReport.summer_sessions = $summer; $baseReport.rows = $rows.Count
    $baseReport.csv_sha256 = (Get-FileHash -LiteralPath $csvPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Save-Json $baseReport $reportPath
    exit 0
} catch {
    $baseReport.reason = $_.Exception.Message
    if (Test-Path -LiteralPath $csvPath -PathType Leaf) {
        $baseReport.csv_sha256 = (Get-FileHash -LiteralPath $csvPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    Save-Json $baseReport $reportPath
    Write-Error $_
    exit 1
}
