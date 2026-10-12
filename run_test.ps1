# Video -> animation sur le personnage, de bout en bout. La logique est dans mocap.py
# (partagee avec l'interface Mocap.bat) ; ce script ne fait que lui passer les options.
#
#   powershell -ExecutionPolicy Bypass -File run_test.ps1 -Video "C:\...\ma_video.mp4"
#   powershell -ExecutionPolicy Bypass -File run_test.ps1 -Video "C:\...\ma_video.mp4" -Start 2 -End 12
#   powershell -ExecutionPolicy Bypass -File run_test.ps1 -Name ma_video        # plate deja importee
#   powershell -ExecutionPolicy Bypass -File run_test.ps1 -Character ybot.fbx   # un autre personnage
#   ... -Review   ajoute les videos de controle 2x2 et les mesures de fidelite

param(
    [string]$Name = "tennis",
    [string]$Video,
    [double]$Start = -1,
    [double]$End = -1,
    [string]$Character = "Benjamin.glb",
    [string]$Blender,
    [switch]$SkipEstimate,
    [switch]$Rebuild,
    [switch]$Review
)

$a = @("$PSScriptRoot\mocap.py", "--character", $Character)
if ($Video) { $a += @("--video", $Video) }
if ($PSBoundParameters.ContainsKey("Name") -or -not $Video) { $a += @("--name", $Name) }
if ($Start -ge 0) { $a += @("--start", "$Start") }
if ($End -ge 0) { $a += @("--end", "$End") }
if ($Blender) { $a += @("--blender", $Blender) }
if ($SkipEstimate) { $a += "--skip-estimate" }
if ($Rebuild) { $a += "--rebuild" }
if ($Review) { $a += "--review" }

& "$PSScriptRoot\tools\venv\Scripts\python.exe" @a
exit $LASTEXITCODE
