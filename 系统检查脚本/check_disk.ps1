$targets = @(
    "C:\Users",
    "C:\Windows",
    "C:\Program Files",
    "C:\Program Files (x86)",
    "C:\ProgramData"
)

foreach ($target in $targets) {
    if (Test-Path $target) {
        $size = (Get-ChildItem $target -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        $sizeGB = [math]::Round($size / 1GB, 2)
        Write-Host "$sizeGB GB - $target"
    }
}

Write-Host "--- Top-level folders ---"
Get-ChildItem "C:\" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    if ($size -gt 1GB) {
        $sizeGB = [math]::Round($size / 1GB, 2)
        Write-Host "$sizeGB GB - $($_.Name)"
    }
}
