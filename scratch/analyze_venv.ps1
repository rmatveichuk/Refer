$path = ".venv\Lib\site-packages"
if (Test-Path $path) {
    Get-ChildItem -Path $path -Directory | ForEach-Object {
        $size = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB
        [PSCustomObject]@{
            Package = $_.Name
            'Size(MB)' = [Math]::Round($size, 2)
        }
    } | Sort-Object 'Size(MB)' -Descending | Select-Object -First 20 | Format-Table -AutoSize
} else {
    Write-Host "Path $path not found."
}
