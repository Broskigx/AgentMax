<#
.SYNOPSIS
    Quick connectivity test for LM Studio local server.
    Run BEFORE starting AgentMax to verify LM Studio is configured correctly.
#>

$Host_  = "127.0.0.1"
$Port   = 1234
$Base   = "http://${Host_}:${Port}/v1"

Write-Host "`n  Testing LM Studio connection..." -ForegroundColor Cyan

try {
    $resp = Invoke-RestMethod "$Base/models" -TimeoutSec 5 -ErrorAction Stop
    $models = $resp.data

    Write-Host "`n  ✓ LM Studio server reachable at $Base" -ForegroundColor Green

    if ($models.Count -eq 0) {
        Write-Host "  ✗ No models loaded." -ForegroundColor Yellow
        Write-Host "    → Open LM Studio, load a model, enable Local Server." -ForegroundColor Yellow
    } else {
        Write-Host "  ✓ Loaded models ($($models.Count)):" -ForegroundColor Green
        $models | ForEach-Object { Write-Host "      • $($_.id)" -ForegroundColor White }
        Write-Host "`n  Vision models (multimodal) that work well:" -ForegroundColor Cyan
        Write-Host "    • LLaVA-1.5, LLaVA-1.6 (any variant)"
        Write-Host "    • Qwen2-VL (7B or 72B)"
        Write-Host "    • Llama-3.2-Vision (11B or 90B)"
        Write-Host "    • MiniCPM-V 2.6"
        Write-Host "`n  Set in .env:" -ForegroundColor Cyan
        $firstModel = $models[0].id
        Write-Host "    AGENTMAX_BACKEND=lmstudio" -ForegroundColor White
        Write-Host "    AGENTMAX_LMS_MODEL=$firstModel" -ForegroundColor White
        Write-Host "    AGENTMAX_LMS_VISION_MODEL=$firstModel" -ForegroundColor White
    }
} catch {
    Write-Host "`n  ✗ Cannot connect to LM Studio at $Base" -ForegroundColor Red
    Write-Host ""
    Write-Host "  To fix:" -ForegroundColor Yellow
    Write-Host "    1. Open LM Studio"
    Write-Host "    2. Go to  Local Server  tab (left sidebar)"
    Write-Host "    3. Click  Start Server  (port 1234)"
    Write-Host "    4. Load any model"
    Write-Host "    5. Run this script again"
}

Write-Host ""
