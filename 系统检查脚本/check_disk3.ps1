# Check more cache and hidden areas
Write-Host "=== Windows Update Cache ==="
$wu = "C:\Windows\SoftwareDistribution"
if (Test-Path $wu) {
    $size = (Get-ChildItem $wu -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - Windows Update Cache"
}

Write-Host ""
Write-Host "=== WinSxS ==="
$sxs = "C:\Windows\WinSxS"
if (Test-Path $sxs) {
    $size = (Get-ChildItem $sxs -File -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - WinSxS (component store)"
}

Write-Host ""
Write-Host "=== AppData Roaming ==="
$roaming = "C:\Users\YzK12\AppData\Roaming"
Get-ChildItem $roaming -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    if ($sizeGB -gt 0.5) {
        Write-Host "$sizeGB GB - Roaming\$($_.Name)"
    }
}

Write-Host ""
Write-Host "=== AppData Local (large) ==="
$local = "C:\Users\YzK12\AppData\Local"
Get-ChildItem $local -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    if ($sizeGB -gt 0.5) {
        Write-Host "$sizeGB GB - Local\$($_.Name)"
    }
}

Write-Host ""
Write-Host "=== Docker ==="
$docker = "C:\ProgramData\Docker"
if (Test-Path $docker) {
    $size = (Get-ChildItem $docker -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - Docker"
}

Write-Host ""
Write-Host "=== WSL ==="
$wsl = "$env:LOCALAPPDATA\Packages"
Get-ChildItem $wsl -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*Canonical*" -or $_.Name -like "*Ubuntu*" -or $_.Name -like "*Debian*" } | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - WSL: $($_.Name)"
}

Write-Host ""
Write-Host "=== NuGet Cache ==="
$nuget = "$env:USERPROFILE\.nuget"
if (Test-Path $nuget) {
    $size = (Get-ChildItem $nuget -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - NuGet cache"
}

Write-Host ""
Write-Host "=== Gradle Cache ==="
$gradle = "$env:USERPROFILE\.gradle"
if (Test-Path $gradle) {
    $size = (Get-ChildItem $gradle -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - Gradle cache"
}

Write-Host ""
Write-Host "=== pagefile/hiberfil (admin) ==="
cmd /c "dir C:\pagefile.sys /ah 2>nul & dir C:\hiberfil.sys /ah 2>nul & dir C:\swapfile.sys /ah 2>nul"
