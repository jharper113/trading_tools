# Windows PowerShell 5.1+. Builds one immutable AmiBroker experiment job.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Matrix,
    [Parameter(Mandatory=$true)][string]$JobId,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$ReportsRoot
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$temporary = "$Destination.tmp.$([Guid]::NewGuid().ToString('N'))"

function Read-Json([string]$Path) {
    Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}
function Save-Json($Value, [string]$Path) {
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding UTF8
}
function File-Hash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Resolve-InputPath([string]$Path, [string]$Base) {
    if ([IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $Base $Path
}
function Replace-Exactly([string]$Text, [string]$Old, [string]$New, [int]$ExpectedCount) {
    $count = ([regex]::Matches($Text, [regex]::Escape($Old))).Count
    if ($count -ne $ExpectedCount) { throw "Adapter anchor count $count; expected $ExpectedCount" }
    return $Text.Replace($Old, $New)
}
function Set-XmlValues($Document, [string[]]$Names, [string]$Value) {
    foreach ($name in $Names) {
        foreach ($node in @($Document.SelectNodes("//*[local-name()='$name']"))) {
            $node.InnerText = $Value
        }
    }
}
function Add-BatchStep($Document, $Root, [string]$Action, [string]$Param='') {
    $step = $Document.CreateElement('Step')
    $actionNode = $Document.CreateElement('Action')
    $actionNode.InnerText = $Action
    [void]$step.AppendChild($actionNode)
    $paramNode = $Document.CreateElement('Param')
    $paramNode.InnerText = $Param
    [void]$step.AppendChild($paramNode)
    [void]$Root.AppendChild($step)
}

try {
    $matrixPath = (Resolve-Path -LiteralPath $Matrix).Path
    $matrixObject = Read-Json $matrixPath
    if ($matrixObject.schema_version -ne 1) { throw 'Unsupported experiment matrix schema' }
    $jobProperty = $matrixObject.jobs.PSObject.Properties[$JobId]
    if ($null -eq $jobProperty) { throw "Unknown experiment job: $JobId" }
    $job = $jobProperty.Value
    if ($job.job_id -ne $JobId -or -not $job.enabled) { throw 'Invalid or disabled experiment job' }
    if ($matrixObject.timezone -ne 'America/Detroit') { throw 'Experiment timezone must be America/Detroit' }
    if ($matrixObject.research_window.start -ne '2009-01-01' -or $matrixObject.research_window.end -ne '2019-01-01') {
        throw 'Experiment research dates must be 2009-01-01 through 2019-01-01'
    }

    $sourcePath = Resolve-InputPath ([string]$job.source_afl) ([string]$matrixObject.strategy_root_windows)
    $projectTemplate = Resolve-InputPath ([string]$job.project_template) (Split-Path -Parent $matrixPath)
    $profilePath = Resolve-InputPath ([string]$job.analysis_profile) $PSScriptRoot
    foreach ($path in @($sourcePath, $projectTemplate, $profilePath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required input does not exist: $path" }
    }
    if ((File-Hash $sourcePath) -ne ([string]$job.source_sha256).ToLowerInvariant()) {
        throw 'Canonical AFL source hash mismatch'
    }

    $formula = [IO.File]::ReadAllText($sourcePath).Replace("`r`n", "`n").Replace("`r", "`n")
    $replacementCount = 0
    $adapter = [string]$job.adapter.name
    if ($adapter -eq 'entry_cutoff_110000') {
        $formula = Replace-Exactly $formula 'Buy = afterRange AND Cross(Close, orHigh);' 'Buy = afterRange AND tn <= 110000 AND Cross(Close, orHigh);' 1
        $formula = Replace-Exactly $formula 'Short = afterRange AND Cross(orLow, Close);' 'Short = afterRange AND tn <= 110000 AND Cross(orLow, Close);' 1
        $replacementCount = 2
    } elseif ($adapter -eq 'fixed_110000') {
        [void](Replace-Exactly $formula 'SignalBar = TN == 110000;' 'SignalBar = TN == 110000;' 1)
    } elseif ($adapter -ne 'native') {
        throw "Unsupported experiment adapter: $adapter"
    }
    $formula += @'


// BEGIN GENERATED EXPERIMENT ENTRY AUDIT
Filter = Buy OR Short;
AddColumn( DateTime(), "Audit DateTime", formatDateTime );
AddColumn( TimeNum(), "TimeNum", 1.0 );
AddColumn( Buy, "Buy", 1.0 );
AddColumn( Short, "Short", 1.0 );
// END GENERATED EXPERIMENT ENTRY AUDIT
'@

    if (Test-Path -LiteralPath $Destination) { throw "Destination already exists: $Destination" }
    New-Item -ItemType Directory -Path $temporary | Out-Null
    $formulaTemporary = Join-Path $temporary 'formula.afl'
    $formulaFinal = Join-Path $Destination 'formula.afl'
    [IO.File]::WriteAllText($formulaTemporary, $formula, (New-Object Text.UTF8Encoding($false)))

    $profile = Read-Json $profilePath
    if ([int]$profile.periodicity.seconds -ne [int]$job.interval_seconds) {
        throw 'Analysis profile interval does not match matrix job'
    }
    $project = New-Object System.Xml.XmlDocument
    $project.XmlResolver = $null
    $project.Load($projectTemplate)
    Set-XmlValues $project @('FormulaPath') $formulaFinal
    Set-XmlValues $project @('FormulaContent') $formula
    Set-XmlValues $project @('InitialEquity') ([string]$profile.initial_equity)
    Set-XmlValues $project @('CommissionMode') ([string]$profile.commission_mode)
    Set-XmlValues $project @('CommissionValue','CommissionAmount') ([string]$profile.commission_per_contract_side)
    Set-XmlValues $project @('PointsOnlyTest') $(if ($profile.futures_mode) { '1' } else { '0' })
    Set-XmlValues $project @('Periodicity') ([string]$profile.periodicity.apx_code)
    Set-XmlValues $project @('ChartInterval') ([string]$job.interval_seconds)
    Set-XmlValues $project @('MinShares') ([string]$profile.min_shares)
    Set-XmlValues $project @('AllowSameBarExit') $(if ($profile.allow_same_bar_exit) { '1' } else { '0' })
    Set-XmlValues $project @('ReverseSignalForcesExit') $(if ($profile.reverse_signal_forces_exit) { '1' } else { '0' })
    Set-XmlValues $project @('UsePrevBarEquity','UsePrevBarEquityForPosSizing') $(if ($profile.use_previous_bar_equity) { '1' } else { '0' })
    Set-XmlValues $project @('OptTarget') ([string]$profile.fitness)
    Set-XmlValues $project @('RangeType','BacktestRangeType') '3'
    Set-XmlValues $project @('FromDate','RangeFromDate','BacktestRangeFromDate') ([string]$matrixObject.research_window.start)
    Set-XmlValues $project @('ToDate','RangeToDate','BacktestRangeToDate') ([string]$matrixObject.research_window.end)
    $projectTemporary = Join-Path $temporary 'project.apx'
    $project.Save($projectTemporary)

    $staging = Join-Path (Join-Path (Join-Path $ReportsRoot '_ExperimentStaging') ([string]$matrixObject.matrix_id)) $JobId
    $auditStaging = Join-Path $staging 'entry_audit'
    $configFinal = Join-Path $Destination 'batch.archive.json'
    $batchFinal = Join-Path $Destination 'batch.abb'
    $projectFinal = Join-Path $Destination 'project.apx'
    $helper = Join-Path $PSScriptRoot 'Archive_AmiBroker_Run.ps1'
    $config = [ordered]@{
        schema_version = 2
        batch_path = $batchFinal
        project_path = $projectFinal
        reports_root = $ReportsRoot
        staging_dir = $staging
        kind = 'Optimization'
        symbols = @($job.symbols)
        audit_symbols = @($job.symbols)
        experiment = [ordered]@{
            matrix_id = [string]$matrixObject.matrix_id
            matrix_path = $matrixPath
            matrix_sha256 = File-Hash $matrixPath
            job_id = $JobId
            strategy_id = [string]$job.strategy_id
            periodicity = [string]$job.periodicity
            adapter = $adapter
            database = [string]$job.database
            source_afl = $sourcePath
            source_sha256 = [string]$job.source_sha256
            analysis_profile = $profilePath
            analysis_profile_sha256 = File-Hash $profilePath
            build_manifest = (Join-Path $Destination 'build_manifest.json')
        }
    }
    Save-Json $config (Join-Path $temporary 'batch.archive.json')

    $batch = New-Object System.Xml.XmlDocument
    $root = $batch.CreateElement('AmiBroker-Batch')
    $root.SetAttribute('CompactMode', '0')
    [void]$batch.AppendChild($root)
    function Add-ArchiveStep([string]$Stage, [string]$Symbol='') {
        $command = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$helper`" -Config `"$configFinal`" -Stage $Stage"
        if ($Symbol) { $command += " -Symbol `"$Symbol`"" }
        Add-BatchStep $batch $root 'ExecuteAndWait' $command
    }
    Add-ArchiveStep 'Begin'
    Add-BatchStep $batch $root 'LoadDatabase' ([string]$job.database)
    Add-BatchStep $batch $root 'LoadProject' $projectFinal
    foreach ($symbolValue in @($job.symbols)) {
        $symbol = [string]$symbolValue
        Add-BatchStep $batch $root 'SetCurrentSymbol' $symbol
        Add-BatchStep $batch $root 'Optimize' ''
        Add-BatchStep $batch $root 'Export' (Join-Path $staging "$symbol.csv")
        Add-ArchiveStep 'Publish' $symbol
        Add-BatchStep $batch $root 'Scan' ''
        Add-BatchStep $batch $root 'Export' (Join-Path $auditStaging "$symbol.csv")
        Add-ArchiveStep 'PublishAudit' $symbol
    }
    Add-ArchiveStep 'Complete'
    $batch.Save((Join-Path $temporary 'batch.abb'))

    $manifest = [ordered]@{
        schema_version = 1
        matrix_id = [string]$matrixObject.matrix_id
        job_id = $JobId
        strategy = [string]$job.strategy_id
        periodicity = [string]$job.periodicity
        interval_seconds = [int]$job.interval_seconds
        adapter = $adapter
        replacement_count = $replacementCount
        timezone = [string]$matrixObject.timezone
        research_window = $matrixObject.research_window
        symbols = @($job.symbols)
        inputs = [ordered]@{
            matrix = [ordered]@{ path = $matrixPath; sha256 = File-Hash $matrixPath }
            source_afl = [ordered]@{ path = $sourcePath; sha256 = File-Hash $sourcePath }
            project_template = [ordered]@{ path = $projectTemplate; sha256 = File-Hash $projectTemplate }
            analysis_profile = [ordered]@{ path = $profilePath; sha256 = File-Hash $profilePath }
        }
        outputs = [ordered]@{
            formula = [ordered]@{ path = $formulaFinal; sha256 = File-Hash $formulaTemporary }
            project = [ordered]@{ path = $projectFinal; sha256 = File-Hash $projectTemporary }
            batch = [ordered]@{ path = $batchFinal; sha256 = File-Hash (Join-Path $temporary 'batch.abb') }
            archive_config = [ordered]@{ path = $configFinal; sha256 = File-Hash (Join-Path $temporary 'batch.archive.json') }
        }
    }
    Save-Json $manifest (Join-Path $temporary 'build_manifest.json')
    Move-Item -LiteralPath $temporary -Destination $Destination
    Write-Output $Destination
} catch {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
    throw
}
