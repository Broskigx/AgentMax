param(
    [Parameter(Mandatory = $false)]
    [string]$Path = ".\dist",

    [Parameter(Mandatory = $false)]
    [string]$OutFile = "release-manifest.sha256"
)

$ErrorActionPreference = "Stop"
$resolvedPath = Resolve-Path -LiteralPath $Path
$manifestPath = Join-Path -Path $resolvedPath -ChildPath $OutFile

$files = Get-ChildItem -LiteralPath $resolvedPath -Recurse -File |
    Where-Object {
        if ($_.Name -eq $OutFile) {
            return $false
        }

        $relativeParent = $_.DirectoryName
        if ($relativeParent.StartsWith($resolvedPath.Path)) {
            $relativeParent = $relativeParent.Substring($resolvedPath.Path.Length).TrimStart('\', '/')
        }
        $parts = $relativeParent -split '[\\/]'
        return -not ($parts -contains "deps" -or $parts -contains "incremental")
    } |
    Sort-Object FullName

$lines = foreach ($file in $files) {
    $hash = Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
    $relative = Resolve-Path -LiteralPath $file.FullName -Relative
    "$($hash.Hash.ToLowerInvariant())  $relative"
}

Set-Content -LiteralPath $manifestPath -Value $lines -Encoding ASCII
Write-Host "Wrote $manifestPath"
