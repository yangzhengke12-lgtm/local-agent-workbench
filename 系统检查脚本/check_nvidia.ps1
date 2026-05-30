# Check NVIDIA related directories
Write-Host "=== Program Files NVIDIA ==="
$nv1 = "C:\Program Files\NVIDIA Corporation"
if (Test-Path $nv1) {
    $size = (Get-ChildItem $nv1 -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    Write-Host "$([math]::Round($size/1GB, 2)) GB"
    Get-ChildItem $nv1 -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  -- $($_.Name)"
    }
}

Write-Host "=== ProgramData NVIDIA ==="
$nv2 = "C:\ProgramData\NVIDIA Corporation"
if (Test-Path $nv2) {
    $size = (Get-ChildItem $nv2 -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    Write-Host "$([math]::Round($size/1GB, 2)) GB"
}

Write-Host "=== Driver Store NVIDIA packages ==="
$driverStore = "C:\Windows\System32\DriverStore\FileRepository"
$nvidiaPackages = Get-ChildItem $driverStore -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*nvidia*" -or $_.Name -like "*nv_disp*" -or $_.Name -like "*nvdmi*" -or $_.Name -like "*nv_dispi*" }
$total = 0
foreach ($pkg in $nvidiaPackages) {
    $size = (Get-ChildItem $pkg.FullName -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer -eq $false } | Measure-Object -Property Length -Sum).Sum
    $total += $size
    Write-Host "$([math]::Round($size/1GB, 2)) GB - $($pkg.Name)"
}
Write-Host "Total driver store NVIDIA: $([math]::Round($total/1GB, 2)) GB"

Write-Host ""
Write-Host "=== Current NVIDIA driver ==="
$output = & nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>&1
Write-Host "Active driver: $output"
