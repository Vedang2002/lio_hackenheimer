param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$taskOrigin = 'https://start-hack-portal-cxh3crbdaef2f8gt.westeurope-01.azurewebsites.net'
$taskRawRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'customer-data\soony-data'))
$taskPrivateRoot = Join-Path $PSScriptRoot 'launchguard\customer-data'
New-Item -ItemType Directory -Path $taskRawRoot,$taskPrivateRoot -Force | Out-Null
$taskHeaders = @{}
$taskSecure = $null
try {
    if ($env:SOONY_API_TOKEN) {
        $taskHeaders.Authorization = 'Bearer ' + $env:SOONY_API_TOKEN
    } else {
        $taskSecure = Read-Host 'Soony API token (hidden; not saved)' -AsSecureString
        $taskHeaders.Authorization = 'Bearer ' + [Net.NetworkCredential]::new('', $taskSecure).Password
    }
    $taskSpec = Invoke-RestMethod -Uri ($taskOrigin + '/api/v1/openapi.json') -Headers $taskHeaders -TimeoutSec 30 -MaximumRedirection 0
    $taskIndex = Invoke-RestMethod -Uri ($taskOrigin + '/api/v1/files') -Headers $taskHeaders -TimeoutSec 30 -MaximumRedirection 0
    if (-not $taskIndex.files -or $taskIndex.count -ne @($taskIndex.files).Count) { throw 'Unexpected portal file-list schema.' }
    $taskSeen = @{}
    $taskChanged = @()
    $taskNew = @()
    $taskRemoved = @()
    $taskDownloads = @()
    $taskAnalysisChanges = @()
    foreach ($taskFile in $taskIndex.files) {
        if ([string]::IsNullOrWhiteSpace($taskFile.path) -or $taskFile.path -match '(^[/\\]|(^|[/\\])\.\.([/\\]|$)|:)' -or $taskFile.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or $taskSeen.ContainsKey($taskFile.path)) { throw 'Invalid or duplicate portal file metadata.' }
        $taskSeen[$taskFile.path] = $true
        $taskLocal = [IO.Path]::GetFullPath((Join-Path $taskRawRoot $taskFile.path))
        if (-not $taskLocal.StartsWith($taskRawRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe portal path.' }
        $taskCurrent = if (Test-Path -LiteralPath $taskLocal) { (Get-FileHash -LiteralPath $taskLocal -Algorithm SHA256).Hash } else { $null }
        if ($taskCurrent -eq $taskFile.sha256) { continue }
        if ($null -eq $taskCurrent) { $taskNew += $taskFile.path } else { $taskChanged += $taskFile.path }
        if ($CheckOnly) { continue }
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($taskLocal)) -Force | Out-Null
        $taskEncodedPath = (($taskFile.path -split '/') | ForEach-Object { [Uri]::EscapeDataString($_) }) -join '/'
        $taskPending = $taskLocal + '.download'
        Invoke-WebRequest -UseBasicParsing -Uri ($taskOrigin + '/api/v1/files/' + $taskEncodedPath) -Headers $taskHeaders -TimeoutSec 90 -MaximumRedirection 0 -OutFile $taskPending
        if ((Get-FileHash -LiteralPath $taskPending -Algorithm SHA256).Hash -ne $taskFile.sha256 -or (Get-Item -LiteralPath $taskPending).Length -ne $taskFile.bytes) { throw ('Checksum or size mismatch for ' + $taskFile.path + '; original file retained.') }
        Move-Item -LiteralPath $taskPending -Destination $taskLocal -Force
        $taskDownloads += $taskFile.path
        Write-Output ('Downloaded and verified: ' + $taskFile.path)
    }
    $taskPreviousPath = Join-Path $taskPrivateRoot 'portal-file-index.json'
    if (Test-Path -LiteralPath $taskPreviousPath) {
        $taskPrevious = Get-Content -LiteralPath $taskPreviousPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $taskRemoved = @($taskPrevious.files | Where-Object { -not $taskSeen.ContainsKey($_.path) } | ForEach-Object { $_.path })
    }
    $taskBaselinePath = Join-Path $taskPrivateRoot 'snapshot.json'
    if (Test-Path -LiteralPath $taskBaselinePath) {
        $taskBaseline = Get-Content -LiteralPath $taskBaselinePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($taskBaseline.sourceChecksums) {
            foreach ($taskFile in $taskIndex.files) {
                $taskKnown = $taskBaseline.sourceChecksums.PSObject.Properties[$taskFile.path]
                if ($null -eq $taskKnown -or $taskKnown.Value -ne $taskFile.sha256) { $taskAnalysisChanges += $taskFile.path }
            }
            foreach ($taskKnown in $taskBaseline.sourceChecksums.PSObject.Properties) { if (-not $taskSeen.ContainsKey($taskKnown.Name)) { $taskAnalysisChanges += $taskKnown.Name } }
        } else { $taskAnalysisChanges += 'Analysis baseline has no source checksums; regenerate normalization.' }
    } else { $taskAnalysisChanges += 'No normalized customer analysis exists.' }
    $taskStatus = [ordered]@{ checkedAt = [DateTimeOffset]::UtcNow.ToString('o'); count = $taskIndex.count; checkOnly = [bool]$CheckOnly; new = @($taskNew); changed = @($taskChanged); removed = @($taskRemoved); downloaded = @($taskDownloads); analysisChanges = @($taskAnalysisChanges); requiresReview = ($taskNew.Count + $taskChanged.Count + $taskRemoved.Count + $taskAnalysisChanges.Count -gt 0); verifiedUnchanged = $taskIndex.count - $taskNew.Count - $taskChanged.Count }
    $taskStatus | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $taskPrivateRoot 'portal-sync-status.json') -Encoding UTF8
    if (-not $CheckOnly) {
        $taskIndex | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $taskPreviousPath -Encoding UTF8
        $taskSpec | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath (Join-Path $taskPrivateRoot 'portal-openapi.json') -Encoding UTF8
    }
    Write-Output ('Portal files: ' + $taskIndex.count + '; unchanged: ' + $taskStatus.verifiedUnchanged + '; new: ' + $taskNew.Count + '; changed: ' + $taskChanged.Count + '; removed: ' + $taskRemoved.Count)
    if ($taskStatus.requiresReview) { Write-Output 'Review required. The existing analysis has not been refreshed. Review changed evidence, regenerate extraction and normalization, then reload the snapshot.' } else { Write-Output 'All local file checksums match the current portal index. No customer files downloaded.' }
} catch {
    [ordered]@{ checkedAt=[DateTimeOffset]::UtcNow.ToString('o'); failed=$true; requiresReview=$true; message='Portal sync failed; do not treat previous hash verification as current.' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskPrivateRoot 'portal-sync-status.json') -Encoding UTF8
    throw
} finally {
    $taskHeaders.Clear()
    if ($null -ne $taskSecure) { $taskSecure.Dispose() }
}
