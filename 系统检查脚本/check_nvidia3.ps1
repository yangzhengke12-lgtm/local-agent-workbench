Write-Host "=== NVIDIA App breakdown ==="
$nvapp = "C:\ProgramData\NVIDIA Corporation\NVIDIA App"
Get-ChildItem $nvapp -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    if ($size -gt 10MB) {
        Write-Host "$([math]::Round($size/1GB, 2)) GB - $($_.Name)"
    }
}

Write-Host ""
Write-Host "=== Downloader breakdown ==="
$dler = "C:\ProgramData\NVIDIA Corporation\Downloader"
Get-ChildItem $dler -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    Write-Host "$([math]::Round($size/1GB, 2)) GB - $($_.Name)"
}
Get-ChildItem $dler -File -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "$([math]::Round($_.Length/1MB, 0)) MB - $($_.Name)"
}
