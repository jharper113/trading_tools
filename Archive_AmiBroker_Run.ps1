# Windows PowerShell 5.1+; called by native AmiBroker ExecuteAndWait steps.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][ValidateSet('Begin','Publish','PublishAudit','Complete')][string]$Stage,
    [string]$Symbol
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$lock = $null
$context = $null

function Save-Json($Value, [string]$Path) {
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Read-Json([string]$Path) {
    Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}
function Project-Value($Document, [string]$Name) {
    $node = $Document.SelectSingleNode("//*[local-name()='$Name']")
    if ($null -eq $node) { return '' }
    return $node.InnerText
}
function Safe-Name([string]$Name) {
    $safe = ($Name -replace '[<>:"/\\|?*\x00-\x1f]', '_').TrimEnd([char[]]' .')
    if (-not $safe) { $safe = 'Unnamed' }
    if ($safe -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') { $safe = "_$safe" }
    if ($safe.Length -gt 72) { $safe = $safe.Substring(0,72) }
    if ($safe -cne $Name) {
        $hash = [System.Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Name))).Replace('-','').Substring(0,8).ToLowerInvariant() }
        finally { $hash.Dispose() }
        $safe += "_$digest"
    }
    return $safe
}

try {
    $cfg = Read-Json $Config
    if ($cfg.schema_version -notin @(1,2) -or $cfg.kind -notin @('Optimization','WFA')) { throw 'Invalid archival configuration' }
    $staging = [string]$cfg.staging_dir
    $parent = Split-Path -Parent $staging
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    # Locks individual helper operations. Run batches serially in one AmiBroker instance.
    $lock = [IO.File]::Open("$staging.lock", [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $contextPath = Join-Path $staging 'context.json'
    if ($Stage -eq 'Begin') {
        if (Test-Path -LiteralPath $staging) {
            # Invalidate the old routing before any other fallible work.
            $previousStaging = "$staging.previous.$([Guid]::NewGuid().ToString('N'))"
            Move-Item -LiteralPath $staging -Destination $previousStaging
            $previousContext = Join-Path $previousStaging 'context.json'
            if (Test-Path -LiteralPath $previousContext) {
                $previous = Read-Json $previousContext
                $previousManifest = Read-Json $previous.manifest_path
                if ($previousManifest.status -eq 'RUNNING') {
                    $previousManifest.status = 'INTERRUPTED'
                    $previousManifest.updated_utc = [DateTime]::UtcNow.ToString('o')
                    Save-Json $previousManifest $previous.manifest_path
                }
            }
            # Previous staging also retains un-published interrupted/failed exports.
        }
        New-Item -ItemType Directory -Path $staging | Out-Null
        $auditSymbols = @()
        if ($null -ne $cfg.PSObject.Properties['audit_symbols']) {
            $auditSymbols = @($cfg.audit_symbols)
            New-Item -ItemType Directory -Path (Join-Path $staging 'entry_audit') | Out-Null
        }
        $snapshot = Join-Path $staging 'project.apx'
        Copy-Item -LiteralPath $cfg.project_path -Destination $snapshot
        $project = New-Object System.Xml.XmlDocument
        $project.XmlResolver = $null
        $project.Load($snapshot)
        $formulaPath = Project-Value $project 'FormulaPath'
        $strategy = [IO.Path]::GetFileNameWithoutExtension(($formulaPath -split '[\\/]')[-1])
        if (-not $strategy) { throw 'APX has no strategy FormulaPath' }
        $periodicity = Project-Value $project 'Periodicity'
        $interval = Project-Value $project 'ChartInterval'
        if ($periodicity -eq '0') { $timeframe = 'Daily' }
        elseif ($periodicity -eq '8' -and $interval -match '^\d+$' -and [int]$interval -gt 0) {
            if ([int]$interval % 60 -eq 0) { $timeframe = "Intraday_$([int]$interval / 60)m" }
            else { $timeframe = "Intraday_${interval}s" }
        }
        elseif ($periodicity -match '^\d+$') { $timeframe = "Periodicity_$periodicity" }
        else { throw 'Missing/invalid APX Periodicity' }
        $runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8)
        $runDir = Join-Path (Join-Path (Join-Path (Join-Path (Join-Path $cfg.reports_root 'Runs') (Safe-Name $strategy)) $timeframe) $cfg.kind) $runId
        New-Item -ItemType Directory -Path $runDir | Out-Null
        Copy-Item -LiteralPath $snapshot -Destination (Join-Path $runDir 'project.apx')
        Copy-Item -LiteralPath $cfg.batch_path -Destination (Join-Path $runDir 'batch.abb')
        Copy-Item -LiteralPath $Config -Destination (Join-Path $runDir 'batch.archive.json')
        Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $runDir 'Archive_AmiBroker_Run.ps1')
        $formula = Project-Value $project 'FormulaContent'
        if ($formula) { [IO.File]::WriteAllText((Join-Path $runDir 'formula.afl'), $formula, (New-Object Text.UTF8Encoding($false))) }
        $manifestPath = Join-Path $runDir 'run_manifest.json'
        $manifest = [ordered]@{
            schema_version = [int]$cfg.schema_version; run_id = $runId; status = 'RUNNING'; strategy = $strategy
            timeframe = $timeframe; kind = $cfg.kind; started_utc = [DateTime]::UtcNow.ToString('o')
            updated_utc = [DateTime]::UtcNow.ToString('o'); source_project = $cfg.project_path
            source_formula = $formulaPath; source_batch = $cfg.batch_path
            periodicity = $periodicity; interval_seconds = $interval
            range_type = (Project-Value $project 'RangeType')
            from_date = (Project-Value $project 'FromDate'); to_date = (Project-Value $project 'ToDate')
            project_sha256 = (Get-FileHash -LiteralPath $snapshot -Algorithm SHA256).Hash.ToLowerInvariant()
            expected_symbols = @($cfg.symbols); exports = @()
            expected_audit_symbols = $auditSymbols; audit_exports = @()
            experiment = $null; experiment_artifacts = @(); error = $null
            reproduction_note = 'APX and embedded AFL are saved. Market database, external AFL includes and plugins are not snapshotted.'
        }
        if ($null -ne $cfg.PSObject.Properties['experiment']) {
            $manifest.experiment = $cfg.experiment
            $artifactNames = @('matrix_path','source_afl','analysis_profile','build_manifest','shared_policy')
            foreach ($artifactName in $artifactNames) {
                $property = $cfg.experiment.PSObject.Properties[$artifactName]
                if ($null -eq $property -or -not [string]$property.Value) { continue }
                $artifactPath = [string]$property.Value
                if (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf)) { throw "Missing experiment artifact: $artifactPath" }
                $manifest.experiment_artifacts = @($manifest.experiment_artifacts) + @([ordered]@{
                    name = $artifactName; path = $artifactPath
                    sha256 = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
                })
            }
        }
        Save-Json $manifest $manifestPath
        Save-Json ([ordered]@{run_dir=$runDir; manifest_path=$manifestPath}) $contextPath
        Write-Output "Run archive: $runDir"
    }
    else {
        $context = Read-Json $contextPath
        $manifest = Read-Json $context.manifest_path
        if ($manifest.status -ne 'RUNNING') { throw "Run is $($manifest.status), not RUNNING" }
        if ($Stage -eq 'Publish') {
            if ($Symbol -notin $manifest.expected_symbols -or $Symbol -notmatch '^[A-Za-z0-9_-]+$') { throw "Unexpected symbol: $Symbol" }
            $source = Join-Path $staging "$Symbol.csv"
            $destination = Join-Path $context.run_dir "$Symbol.csv"
            if ((Test-Path -LiteralPath $destination) -or $Symbol -in @($manifest.exports | ForEach-Object { $_.symbol })) { throw "Refusing to overwrite export for $Symbol" }
            if (-not (Test-Path -LiteralPath $source) -or (Get-Item -LiteralPath $source).Length -eq 0) { throw "Missing/empty export: $source" }
            $entry = [ordered]@{symbol=$Symbol; file="$Symbol.csv"; bytes=(Get-Item -LiteralPath $source).Length; sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()}
            Move-Item -LiteralPath $source -Destination $destination
            $manifest.exports = @($manifest.exports) + @($entry)
        }
        elseif ($Stage -eq 'PublishAudit') {
            if ($Symbol -notin @($manifest.expected_audit_symbols) -or $Symbol -notmatch '^[A-Za-z0-9_-]+$') { throw "Unexpected audit symbol: $Symbol" }
            $source = Join-Path (Join-Path $staging 'entry_audit') "$Symbol.csv"
            $auditDir = Join-Path $context.run_dir 'entry_audit'
            New-Item -ItemType Directory -Path $auditDir -Force | Out-Null
            $destination = Join-Path $auditDir "$Symbol.csv"
            if ((Test-Path -LiteralPath $destination) -or $Symbol -in @($manifest.audit_exports | ForEach-Object { $_.symbol })) { throw "Refusing to overwrite audit export for $Symbol" }
            if (-not (Test-Path -LiteralPath $source) -or (Get-Item -LiteralPath $source).Length -eq 0) { throw "Missing/empty audit export: $source" }
            $entry = [ordered]@{symbol=$Symbol; file="entry_audit/$Symbol.csv"; bytes=(Get-Item -LiteralPath $source).Length; sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()}
            Move-Item -LiteralPath $source -Destination $destination
            $manifest.audit_exports = @($manifest.audit_exports) + @($entry)
        }
        else {
            if (@($manifest.exports).Count -ne @($manifest.expected_symbols).Count) { throw 'Incomplete run: not all expected exports were archived' }
            foreach ($entry in $manifest.exports) {
                $file = Join-Path $context.run_dir $entry.file
                if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw "Archived export changed: $file" }
            }
            if (@($manifest.audit_exports).Count -ne @($manifest.expected_audit_symbols).Count) { throw 'Incomplete run: not all expected entry audits were archived' }
            foreach ($entry in $manifest.audit_exports) {
                $file = Join-Path $context.run_dir ($entry.file -replace '/', [IO.Path]::DirectorySeparatorChar)
                if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw "Archived audit export changed: $file" }
            }
            $manifest.status = 'COMPLETE'
        }
        $manifest.updated_utc = [DateTime]::UtcNow.ToString('o')
        Save-Json $manifest $context.manifest_path
    }
}
catch {
    $message = $_.Exception.Message
    if ($null -ne $context) {
        try {
            $failed = Read-Json $context.manifest_path
            if ($failed.status -eq 'RUNNING') {
                $failed.status = 'FAILED'; $failed.error = $message
                $failed.updated_utc = [DateTime]::UtcNow.ToString('o')
                Save-Json $failed $context.manifest_path
            }
        } catch { }
    }
    [Console]::Error.WriteLine("Archive failed ($Stage): $message")
    exit 1
}
finally { if ($null -ne $lock) { $lock.Dispose() } }
