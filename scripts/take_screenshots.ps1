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
    "00-landing"                   = @("/", 1330)
    "01-beginner-xray"              = @("/?sample=beginner&tab=X-Ray", 4400)
    "02-beginner-fees"              = @("/?sample=beginner&tab=Fee+Clock", 1300)
    "03-beginner-crash"             = @("/?sample=beginner&tab=Crash+Replay", 1950)
    "04-beginner-plan"              = @("/?sample=beginner&tab=Retirement+Plan", 1950)
    "05-beginner-top3"              = @("/?sample=beginner&tab=Top+3+Things+Worth+Knowing", 1600)
    "06-midcareer-xray"             = @("/?sample=midcareer&tab=X-Ray", 4400)
    "07-midcareer-fees"             = @("/?sample=midcareer&tab=Fee+Clock", 1300)
    "08-midcareer-crash"            = @("/?sample=midcareer&tab=Crash+Replay", 1950)
    "09-midcareer-plan"             = @("/?sample=midcareer&tab=Retirement+Plan", 1950)
    "10-midcareer-top3"             = @("/?sample=midcareer&tab=Top+3+Things+Worth+Knowing", 1600)
    "11-retiree-xray"               = @("/?sample=retiree&tab=X-Ray", 4400)
    "12-retiree-fees"               = @("/?sample=retiree&tab=Fee+Clock", 1300)
    "13-retiree-crash"              = @("/?sample=retiree&tab=Crash+Replay", 1950)
    "14-retiree-plan"               = @("/?sample=retiree&tab=Retirement+Plan", 1950)
    "15-retiree-top3"               = @("/?sample=retiree&tab=Top+3+Things+Worth+Knowing", 1600)
    "16-midcareer-about-you"      = @("/?sample=midcareer&tab=Retirement+Plan&about=open", 1800)
    "17-beginner-plan-assumptions" = @("/?sample=beginner&tab=Retirement+Plan&assumptions=open", 2300)
    "18-midcareer-pause-sell-all" = @("/?sample=midcareer&tab=Fee+Clock&pause=sell+everything", 2600)
    "19-retiree-pause-move-half"  = @("/?sample=retiree&tab=Fee+Clock&pause=move+half+to+cash", 2600)
    "20-admin"                    = @("/admin", 1500)
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
