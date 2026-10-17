# Screenshot every main view of the running app (streamlit on http://localhost:8501) into docs/screenshots/.
# Uses headless Microsoft Edge with a throwaway profile. Run from Windows PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\take_screenshots.ps1
#   add -Only 01-landing,16-admin to retake just some
param([string]$Base = "http://localhost:8501", [string[]]$Only = @())

$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$out = Join-Path (Split-Path $PSScriptRoot -Parent) "docs\screenshots"
New-Item -ItemType Directory -Force $out | Out-Null

$top3 = "tab=Top+3+Things+Worth+Knowing"
$shots = [ordered]@{
    "01-landing"                   = @("/", 1100)
    "02-beginner-new-top3"         = @("/?sample=beginner&fluency=New+to+investing&$top3", 1500)
    "03-beginner-comfortable-top3" = @("/?sample=beginner&fluency=Comfortable&$top3", 1500)
    "04-beginner-pro-top3"         = @("/?sample=beginner&fluency=Pro&$top3", 1500)
    "05-midcareer-new-top3"        = @("/?sample=midcareer&fluency=New+to+investing&$top3", 1500)
    "06-midcareer-comfortable-top3"= @("/?sample=midcareer&fluency=Comfortable&$top3", 1500)
    "07-midcareer-pro-top3"        = @("/?sample=midcareer&fluency=Pro&$top3", 1500)
    "08-retiree-new-top3"          = @("/?sample=retiree&fluency=New+to+investing&$top3", 1500)
    "09-retiree-comfortable-top3"  = @("/?sample=retiree&fluency=Comfortable&$top3", 1500)
    "10-retiree-pro-top3"          = @("/?sample=retiree&fluency=Pro&$top3", 1500)
    "11-xray-midcareer"            = @("/?sample=midcareer&tab=X-Ray", 1650)
    "12-fee-clock-retiree"         = @("/?sample=retiree&tab=Fee+Clock", 1250)
    "13-crash-replay-midcareer"    = @("/?sample=midcareer&tab=Crash+Replay", 1900)
    "14-pause-midcareer-sell-all"  = @("/?sample=midcareer&tab=Fee+Clock&pause=sell+everything", 2500)
    "15-pause-beginner-buy-qqq"    = @("/?sample=beginner&tab=Fee+Clock&pause=buy+%245%2C000+of+QQQ", 2500)
    "16-admin"                     = @("/?page=Admin", 1100)
    "17-plan-midcareer"            = @("/?sample=midcareer&tab=Retirement+Plan", 1900)
    "18-plan-retiree"              = @("/?sample=retiree&tab=Retirement+Plan", 1700)
    "19-plan-assumptions-beginner" = @("/?sample=beginner&tab=Retirement+Plan&assumptions=open", 2200)
    "20-about-you-midcareer"       = @("/?sample=midcareer&tab=Retirement+Plan&about=open", 1700)
    "21-xray-concentration-midcareer" = @("/?sample=midcareer&tab=X-Ray", 2500)
}

foreach ($name in $shots.Keys) {
    if ($Only.Count -and -not ($Only -contains $name)) { continue }
    $url, $height = $shots[$name]
    # A capture under 30 KB is a blank page that had not finished loading; try again (up to 4 times).
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        $edgeProfile = Join-Path $env:TEMP ("sl-edge-" + [guid]::NewGuid())
        $tmp = Join-Path $env:TEMP "$name.png"
        Start-Process -FilePath $edge -Wait -ArgumentList @(
            "--headless", "--disable-gpu", "--hide-scrollbars", "--user-data-dir=$edgeProfile",
            "--window-size=1400,$height", "--virtual-time-budget=$(20000 + 10000 * $attempt)", "--screenshot=$tmp", "$Base$url")
        Remove-Item -Recurse -Force $edgeProfile -ErrorAction SilentlyContinue
        if ((Test-Path $tmp) -and (Get-Item $tmp).Length -gt 30KB) { break }
    }
    Move-Item -Force $tmp (Join-Path $out "$name.png")
    Write-Output "$name.png  $([math]::Round((Get-Item (Join-Path $out "$name.png")).Length / 1KB)) KB  (attempts: $attempt)"
}
