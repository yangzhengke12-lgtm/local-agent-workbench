Write-Host "=== Temp folder ==="
$temp = "C:\Users\YzK12\AppData\Local\Temp"
$size = (Get-ChildItem $temp -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
Write-Host "$([math]::Round($size/1GB, 2)) GB"

Write-Host "=== NVIDIA folder ==="
$nv = "C:\Users\YzK12\AppData\Local\NVIDIA"
if (Test-Path $nv) {
    $size = (Get-ChildItem $nv -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    Write-Host "$([math]::Round($size/1GB, 2)) GB"
} else { Write-Host "gone" }

Write-Host "=== C drive free ==="
$c = Get-PSDrive C
Write-Host "Free: $([math]::Round($c.Free/1GB, 2)) GB / $([math]::Round($c.Used/1GB, 2)) GB used"
