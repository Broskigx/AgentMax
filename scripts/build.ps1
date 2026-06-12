<# Build AgentMax Tauri release. #>

$ErrorActionPreference = "Stop"

Push-Location "ui"
npm run build
npm run tauri build
Pop-Location

Write-Host "Build complete. Check ui\src-tauri\target\release\" -ForegroundColor Green
