# Check user folder breakdown
$user = "C:\Users\YzK12"
Write-Host "=== User folder breakdown ==="
Get-ChildItem $user -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    if ($sizeGB -gt 0.01) {
        Write-Host "$sizeGB GB - $($_.Name)"
    }
}

Write-Host ""
Write-Host "=== Temp folders ==="
$tempPaths = @(
    "C:\Windows\Temp",
    "$env:LOCALAPPDATA\Temp",
    "C:\Users\YzK12\AppData\Local\Temp"
)
foreach ($p in $tempPaths) {
    if (Test-Path $p) {
        $size = (Get-ChildItem $p -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
        $sizeGB = [math]::Round($size / 1GB, 2)
        Write-Host "$sizeGB GB - $p"
    }
}

Write-Host ""
Write-Host "=== Downloads ==="
$dl = "C:\Users\YzK12\Downloads"
if (Test-Path $dl) {
    $size = (Get-ChildItem $dl -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - Downloads"
}

Write-Host ""
Write-Host "=== Recycle Bin ==="
$rb = "C:\`$Recycle.Bin"
if (Test-Path $rb) {
    $size = (Get-ChildItem $rb -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - Recycle Bin"
}

Write-Host ""
Write-Host "=== Large files in C root ==="
Get-ChildItem "C:\" -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 100MB } | ForEach-Object {
    $sizeGB = [math]::Round($_.Length / 1GB, 2)
    Write-Host "$sizeGB GB - $($_.Name)"
}

Write-Host ""
Write-Host "=== Special system files ==="
foreach ($f in @("C:\pagefile.sys", "C:\hiberfil.sys", "C:\swapfile.sys")) {
    if (Test-Path $f) {
        $size = (Get-Item $f -ErrorAction SilentlyContinue).Length
        $sizeGB = [math]::Round($size / 1GB, 2)
        Write-Host "$sizeGB GB - $f"
    }
}

Write-Host ""
Write-Host "=== npm/pip cache ==="
$npm = "$env:APPDATA\npm-cache"
$pip = "$env:LOCALAPPDATA\pip\cache"
if (Test-Path $npm) {
    $size = (Get-ChildItem $npm -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - npm cache"
}
if (Test-Path $pip) {
    $size = (Get-ChildItem $pip -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $sizeGB = [math]::Round($size / 1GB, 2)
    Write-Host "$sizeGB GB - pip cache"
}
