# Windows PowerShell 5.1+. Runs AmiBroker experiment jobs sequentially and resumably.
[CmdletBinding()]
param(
    [ValidateSet('pilot','full')][string]$Mode = 'pilot',
    [string]$Matrix = (Join-Path $PSScriptRoot 'amibroker_experiments\strategy_test_matrix.json'),
    [Parameter(Mandatory=$true)][string]$Broker,
    [Parameter(Mandatory=$true)][string]$ReportsRoot,
    [string]$ExperimentId,
    [string]$Resume,
    [string]$Unlock,
    [switch]$ContinueDailyAfterIntradayPreflightFailure
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$AllowedStates = 'PENDING','RUNNING','COMPLETE','FAILED','INTERRUPTED'

function Read-Json([string]$Path) { Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
function Save-Json($Value, [string]$Path) {
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function File-Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Assert-SameMembers($Actual, $Expected, [string]$Label) {
    $difference = @(Compare-Object @($Actual | Sort-Object) @($Expected | Sort-Object))
    if ($difference.Count) { throw "$Label does not match the experiment matrix" }
}
function Assert-RunManifest([string]$Path, $ExpectedSymbols) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing run manifest: $Path" }
    $run = Read-Json $Path
    if ($run.status -ne 'COMPLETE') { throw "Run manifest is $($run.status), not COMPLETE" }
    Assert-SameMembers @($run.expected_symbols) @($ExpectedSymbols) 'Expected symbols'
    Assert-SameMembers @($run.exports | ForEach-Object symbol) @($ExpectedSymbols) 'Optimization exports'
    Assert-SameMembers @($run.audit_exports | ForEach-Object symbol) @($ExpectedSymbols) 'Entry audit exports'
    $runDir = Split-Path -Parent $Path
    foreach ($entry in @($run.exports) + @($run.audit_exports)) {
        $relative = ([string]$entry.file).Replace('/', [IO.Path]::DirectorySeparatorChar)
        $file = Join-Path $runDir $relative
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing archived export: $($entry.file)" }
        if ((File-Hash $file) -ne [string]$entry.sha256) { throw "Archived export hash mismatch: $($entry.file)" }
    }
    return $run
}
function Validate-FullUnlock([string]$UnlockPath, [string]$MatrixPath, [string]$MatrixHash) {
    if (-not $UnlockPath) { throw 'Full mode requires an audit unlock artifact' }
    $unlockValue = Read-Json $UnlockPath
    if ($unlockValue.audit_status -ne 'PASS') { throw 'Full mode requires a passing pilot audit unlock' }
    if (-not [string]$unlockValue.pilot_experiment_id) { throw 'Unlock has no pilot experiment ID' }
    $auditPath = if ($null -ne $unlockValue.PSObject.Properties['audit_path_windows']) { [string]$unlockValue.audit_path_windows } else { [string]$unlockValue.audit_path }
    if (-not (Test-Path -LiteralPath $auditPath -PathType Leaf)) { throw 'Unlock audit file is missing' }
    if ((File-Hash $auditPath) -ne [string]$unlockValue.audit_sha256) { throw 'Pilot audit hash mismatch' }
    $audit = Read-Json $auditPath
    if ($audit.status -ne 'PASS' -or $audit.experiment_id -ne $unlockValue.pilot_experiment_id) { throw 'Unlock does not match the passing pilot audit' }
    if ($null -eq $unlockValue.PSObject.Properties['full_matrix_sha256'] -or [string]$unlockValue.full_matrix_sha256 -ne $MatrixHash) {
        throw 'Unlock does not authorize this expanded full matrix hash'
    }
    if ($null -eq $unlockValue.PSObject.Properties['authorization_hashes']) { throw 'Unlock has no authorization evidence hashes' }
    foreach ($property in @($unlockValue.authorization_hashes.PSObject.Properties)) {
        $entry = $property.Value
        $path = if ($null -ne $entry.PSObject.Properties['path_windows'] -and [string]$entry.path_windows) { [string]$entry.path_windows } else { [string]$entry.path }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Authorization evidence is missing: $($property.Name)" }
        if ((File-Hash $path) -ne [string]$entry.sha256) { throw "Authorization evidence changed: $($property.Name)" }
    }
    return $unlockValue
}

try {
    if ($Resume) {
        $manifestPath = (Resolve-Path -LiteralPath $Resume).Path
        $experiment = Read-Json $manifestPath
        if ($experiment.schema_version -ne 1) { throw 'Unsupported experiment manifest schema' }
        $Mode = [string]$experiment.mode
        $matrixPath = [string]$experiment.matrix_path
        if ((File-Hash $matrixPath) -ne [string]$experiment.matrix_sha256) { throw 'Experiment matrix hash changed since the run started' }
        if ($Mode -eq 'full') {
            if (-not $Unlock -and $null -ne $experiment.PSObject.Properties['authorization']) { $Unlock = [string]$experiment.authorization.unlock_path }
            if ($null -eq $experiment.PSObject.Properties['authorization'] -or (File-Hash $Unlock) -ne [string]$experiment.authorization.unlock_sha256) { throw 'Full-run authorization record is missing or changed' }
            [void](Validate-FullUnlock $Unlock $matrixPath (File-Hash $matrixPath))
        }
        foreach ($state in @($experiment.jobs)) {
            if ($state.status -eq 'RUNNING') {
                $state.status = 'INTERRUPTED'
                $state.error = 'Controller stopped before the attempt completed'
                foreach ($attempt in @($state.attempts)) {
                    if ($attempt.status -eq 'RUNNING') {
                        $attempt.status = 'INTERRUPTED'
                        $attempt.error = 'Controller stopped before the attempt completed'
                        $attempt.completed_utc = [DateTime]::UtcNow.ToString('o')
                    }
                }
            }
        }
        $experiment.status = 'RUNNING'
        $experiment.updated_utc = [DateTime]::UtcNow.ToString('o')
        Save-Json $experiment $manifestPath
    } else {
        $matrixSource = (Resolve-Path -LiteralPath $Matrix).Path
        $matrixValue = Read-Json $matrixSource
        if ($matrixValue.schema_version -ne 1) { throw 'Unsupported experiment matrix schema' }
        if ($matrixValue.timezone -ne 'America/Detroit') { throw 'Experiment matrix timezone must be America/Detroit' }
        $modeProperty = $matrixValue.modes.PSObject.Properties[$Mode]
        if ($null -eq $modeProperty) { throw "Experiment matrix has no mode: $Mode" }
        $selectedIds = @($modeProperty.Value)
        if (@($selectedIds | Group-Object | Where-Object Count -gt 1).Count) { throw "Matrix mode $Mode contains duplicate jobs" }
        $authorization = $null
        if ($Mode -eq 'full') {
            [void](Validate-FullUnlock $Unlock $matrixSource (File-Hash $matrixSource))
            $authorization = [ordered]@{unlock_path=(Resolve-Path -LiteralPath $Unlock).Path; unlock_sha256=File-Hash $Unlock}
            if ($selectedIds -contains '$catalog_supported') { throw 'Full mode requires the expanded matrix emitted by the passing pilot audit' }
        }
        if (-not $ExperimentId) {
            $ExperimentId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8)
        }
        $experimentDir = Join-Path (Join-Path $ReportsRoot 'Experiments') $ExperimentId
        if (Test-Path -LiteralPath $experimentDir) { throw "Experiment already exists: $experimentDir" }
        New-Item -ItemType Directory -Path $experimentDir | Out-Null
        $matrixPath = Join-Path $experimentDir 'strategy_test_matrix.json'
        Copy-Item -LiteralPath $matrixSource -Destination $matrixPath
        $jobs = @()
        foreach ($jobId in $selectedIds) {
            $jobProperty = $matrixValue.jobs.PSObject.Properties[[string]$jobId]
            if ($null -eq $jobProperty) { throw "Matrix mode references unknown job: $jobId" }
            $job = $jobProperty.Value
            $jobs += [ordered]@{
                job_id = [string]$jobId; strategy = [string]$job.strategy_id
                periodicity = [string]$job.periodicity; adapter = [string]$job.adapter.name
                status = 'PENDING'; error = $null; attempts = @(); run_manifest_path = $null
                run_manifest_sha256 = $null; build_manifest_path = $null; entry_audit_hashes = @()
            }
        }
        $manifestPath = Join-Path $experimentDir 'experiment_manifest.json'
        $experiment = [ordered]@{
            schema_version = 1; experiment_id = $ExperimentId; mode = $Mode; status = 'RUNNING'
            started_utc = [DateTime]::UtcNow.ToString('o'); updated_utc = [DateTime]::UtcNow.ToString('o')
            matrix_path = $matrixPath; matrix_sha256 = File-Hash $matrixPath
            timezone = 'America/Detroit'; research_window = $matrixValue.research_window
            preflight = $null; policy_hashes = [ordered]@{}; authorization=$authorization; jobs = $jobs
        }
        if ($null -ne $matrixValue.PSObject.Properties['shared_policy_windows']) {
            $policy = [string]$matrixValue.shared_policy_windows
            if (-not (Test-Path -LiteralPath $policy -PathType Leaf)) { throw "Shared policy is missing: $policy" }
            $experiment.policy_hashes['shared_policy'] = [ordered]@{path=$policy; sha256=File-Hash $policy}
        }
        Save-Json $experiment $manifestPath
    }

    $matrixValue = Read-Json $matrixPath
    $selectedJobs = @($experiment.jobs)
    $intraday = @($selectedJobs | Where-Object { $_.periodicity -ne 'Daily' })
    if ($intraday.Count -and ($null -eq $experiment.preflight -or $experiment.preflight.status -ne 'PASS')) {
        $firstJob = $matrixValue.jobs.PSObject.Properties[[string]$intraday[0].job_id].Value
        $preflightDir = Join-Path (Split-Path -Parent $manifestPath) 'Timezone_Preflight'
        & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Test-AmiBroker-Timezone.ps1') -Broker $Broker -Database ([string]$firstJob.database) -ProjectTemplate ([string]$firstJob.project_template) -WorkDir $preflightDir
        $preflightExit = $LASTEXITCODE
        $preflightReport = Join-Path $preflightDir 'timezone_preflight.json'
        if (Test-Path -LiteralPath $preflightReport) {
            $experiment.preflight = Read-Json $preflightReport
            $experiment.preflight | Add-Member -NotePropertyName report_path -NotePropertyValue $preflightReport -Force
            $experiment.preflight | Add-Member -NotePropertyName report_sha256 -NotePropertyValue (File-Hash $preflightReport) -Force
        } else {
            $experiment.preflight = [ordered]@{status='FAIL'; reason='Timezone preflight did not write its report'}
        }
        Save-Json $experiment $manifestPath
        if ($preflightExit -ne 0 -or $experiment.preflight.status -ne 'PASS') {
            if (-not $ContinueDailyAfterIntradayPreflightFailure) {
                $experiment.status = 'FAILED'; $experiment.updated_utc = [DateTime]::UtcNow.ToString('o'); Save-Json $experiment $manifestPath
                throw 'Intraday timezone preflight failed; no optimization jobs were started'
            }
        }
    }

    foreach ($state in @($experiment.jobs)) {
        if ($state.status -eq 'COMPLETE') {
            try {
                if ((File-Hash ([string]$state.run_manifest_path)) -ne [string]$state.run_manifest_sha256) { throw 'Run manifest hash mismatch' }
                [void](Assert-RunManifest ([string]$state.run_manifest_path) @($matrixValue.jobs.PSObject.Properties[[string]$state.job_id].Value.symbols))
                continue
            } catch {
                $state.status = 'FAILED'; $state.error = "Completed job validation failed: $($_.Exception.Message)"
            }
        }
        if ($state.periodicity -ne 'Daily' -and $experiment.preflight.status -ne 'PASS') {
            $state.status = 'FAILED'; $state.error = 'Intraday timezone preflight did not pass'; Save-Json $experiment $manifestPath; continue
        }
        $job = $matrixValue.jobs.PSObject.Properties[[string]$state.job_id].Value
        $attemptNumber = @($state.attempts).Count + 1
        $attemptId = "$($experiment.experiment_id)-$($state.job_id)-attempt-$attemptNumber"
        $attemptDir = Join-Path (Join-Path (Join-Path (Split-Path -Parent $manifestPath) 'jobs') ([string]$state.job_id)) "attempt-$attemptNumber"
        $attempt = [ordered]@{number=$attemptNumber; attempt_id=$attemptId; status='RUNNING'; started_utc=[DateTime]::UtcNow.ToString('o'); completed_utc=$null; build_dir=$attemptDir; error=$null}
        $state.attempts = @($state.attempts) + @($attempt)
        $state.status = 'RUNNING'; $state.error = $null; $experiment.updated_utc = [DateTime]::UtcNow.ToString('o'); Save-Json $experiment $manifestPath
        try {
            & (Join-Path $PSScriptRoot 'Build-AmiBroker-ExperimentJob.ps1') -Matrix $matrixPath -JobId ([string]$state.job_id) -Destination $attemptDir -ReportsRoot $ReportsRoot -AttemptId $attemptId
            $state.build_manifest_path = Join-Path $attemptDir 'build_manifest.json'
            & $Broker '/runbatch' (Join-Path $attemptDir 'batch.abb') '/exit'
            if ($LASTEXITCODE -ne 0) { throw "AmiBroker exited $LASTEXITCODE" }
            $archiveConfig = Read-Json (Join-Path $attemptDir 'batch.archive.json')
            $contextPath = Join-Path ([string]$archiveConfig.staging_dir) 'context.json'
            if (-not (Test-Path -LiteralPath $contextPath -PathType Leaf)) { throw 'AmiBroker did not publish archive context' }
            $context = Read-Json $contextPath
            $run = Assert-RunManifest ([string]$context.manifest_path) @($job.symbols)
            if ([string]$context.attempt_id -ne $attemptId -or [string]$context.job_id -ne [string]$state.job_id) { throw 'Archive context does not belong to this attempt' }
            if ([string]$run.experiment.attempt_id -ne $attemptId -or [string]$run.experiment.job_id -ne [string]$state.job_id) { throw 'Run manifest does not belong to this attempt' }
            if ([string]$run.experiment.build_manifest -ne [string]$state.build_manifest_path) { throw 'Run manifest build identity mismatch' }
            $state.run_manifest_path = [string]$context.manifest_path
            $state.run_manifest_sha256 = File-Hash ([string]$context.manifest_path)
            $state.entry_audit_hashes = @($run.audit_exports | ForEach-Object { [ordered]@{symbol=$_.symbol; sha256=$_.sha256} })
            $state.status = 'COMPLETE'; $attempt.status = 'COMPLETE'; $attempt.completed_utc = [DateTime]::UtcNow.ToString('o')
        } catch {
            $state.status = 'FAILED'; $state.error = $_.Exception.Message
            $attempt.status = 'FAILED'; $attempt.error = $_.Exception.Message; $attempt.completed_utc = [DateTime]::UtcNow.ToString('o')
        }
        $experiment.updated_utc = [DateTime]::UtcNow.ToString('o'); Save-Json $experiment $manifestPath
        if ($state.status -eq 'FAILED' -and $null -ne $job.PSObject.Properties['stop_on_failure'] -and $job.stop_on_failure) { break }
    }
    $failed = @($experiment.jobs | Where-Object { $_.status -ne 'COMPLETE' })
    $experiment.status = if ($failed.Count) { 'FAILED' } else { 'COMPLETE' }
    $experiment.updated_utc = [DateTime]::UtcNow.ToString('o')
    Save-Json $experiment $manifestPath
    Write-Output "Experiment manifest: $manifestPath"
    if ($failed.Count) { exit 1 }
    exit 0
} catch {
    [Console]::Error.WriteLine("Experiment failed: $($_.Exception.Message)")
    exit 1
}
