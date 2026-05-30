Write-Host "=== ProgramData NVIDIA breakdown ==="
$nvpd = "C:\ProgramData\NVIDIA Corporation"
Get-ChildItem $nvpd -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    if ($size -gt 10MB) {
        Write-Host "$([math]::Round($size/1GB, 2)) GB - $($_.Name)"
    }
}

Write-Host ""
Write-Host "=== Top-level files ==="
Get-ChildItem $nvpd -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 10MB } | ForEach-Object {
    Write-Host "$([math]::Round($_.Length/1GB, 2)) GB - $($_.Name)"
}

Write-Host ""
Write-Host "=== Installer2 contents ==="
$inst2 = "C:\Program Files\NVIDIA Corporation\Installer2"
if (Test-Path $inst2) {
    Get-ChildItem $inst2 -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
        Write-Host "$([math]::Round($size/1GB, 2)) GB - $($_.Name)"
    }
}
