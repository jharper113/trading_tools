# Windows PowerShell 5.1+. Archives both databases before a Kibot rebuild.
[CmdletBinding()]
param(
    [string]$DatabaseRoot = "Z:\04_Code\Amibroker\Databases",
    [string]$ArchiveDate = "2026-09-22"
)

$ErrorActionPreference = "Stop"
$DailyDatabase = Join-Path $DatabaseRoot "Harp_Daily"
$IntradayDatabase = Join-Path $DatabaseRoot "Harp_Intraday"
$ArchiveRoot = Join-Path $DatabaseRoot "Archive"
$DailyArchive = Join-Path $ArchiveRoot ("Harp_Daily_before_kibot_merge_{0}" -f $ArchiveDate)
$IntradayArchive = Join-Path $ArchiveRoot ("Harp_Intraday_before_kibot_merge_{0}" -f $ArchiveDate)
$ManifestPath = Join-Path $ArchiveRoot ("archive_manifest_{0}.json" -f $ArchiveDate)

function Get-DirectoryEvidence([string]$Path) {
    $root = (Resolve-Path -LiteralPath $Path).Path
    return @(
        Get-ChildItem -LiteralPath $Path -File -Recurse | Sort-Object FullName | ForEach-Object {
            [ordered]@{
                path = $_.FullName.Substring($root.Length).TrimStart('\')
                size = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
    )
}

if (Get-Process -Name "Broker" -ErrorAction SilentlyContinue) {
    throw "AmiBroker is running. Close Broker.exe before archiving databases."
}
foreach ($source in @($DailyDatabase, $IntradayDatabase)) {
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Database was not found: $source"
    }
}
foreach ($destination in @($DailyArchive, $IntradayArchive, $ManifestPath)) {
    if (Test-Path -LiteralPath $destination) {
        throw "Archive destination already exists: $destination"
    }
}

New-Item -ItemType Directory -Path $ArchiveRoot -Force | Out-Null
$manifest = [ordered]@{
    status = "READY"
    created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    daily = [ordered]@{ source = $DailyDatabase; destination = $DailyArchive; files = @(Get-DirectoryEvidence $DailyDatabase) }
    intraday = [ordered]@{ source = $IntradayDatabase; destination = $IntradayArchive; files = @(Get-DirectoryEvidence $IntradayDatabase) }
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8

$dailyMoved = $false
$intradayMoved = $false
try {
    Move-Item -LiteralPath $DailyDatabase -Destination $DailyArchive
    $dailyMoved = $true
    Move-Item -LiteralPath $IntradayDatabase -Destination $IntradayArchive
    $intradayMoved = $true
    $manifest.status = "PASS"
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
}
catch {
    Write-Warning "ROLLBACK: restoring database directories after archive failure."
    if ($intradayMoved -and (Test-Path -LiteralPath $IntradayArchive)) {
        Move-Item -LiteralPath $IntradayArchive -Destination $IntradayDatabase
    }
    if ($dailyMoved -and (Test-Path -LiteralPath $DailyArchive)) {
        Move-Item -LiteralPath $DailyArchive -Destination $DailyDatabase
    }
    $manifest.status = "FAIL"
    $manifest.error = $_.Exception.Message
    $failedStamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssfffZ")
    $failedManifest = Join-Path $ArchiveRoot ("archive_manifest_{0}_failed_{1}.json" -f $ArchiveDate, $failedStamp)
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $failedManifest -Encoding UTF8
    if (Test-Path -LiteralPath $ManifestPath) {
        Remove-Item -LiteralPath $ManifestPath -Force
    }
    throw
}

Write-Host "Archive complete: $ManifestPath"
Write-Host "Create Harp_Daily as a local end-of-day database."
Write-Host "Create Harp_Intraday as a local database with a 5-minute base interval, zero time shift, all-session display, and at least 2,000,000 bars."
