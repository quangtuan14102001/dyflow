# Chay tren Windows, PowerShell:
#   cd C:\Users\quang\dyflow
#   powershell -File .\scripts\collect_speedup_evidence.ps1
$ErrorActionPreference = "Continue"
Set-Location C:\Users\quang\dyflow
$py = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$out = Join-Path $env:LOCALAPPDATA "VideosCreator\output\_reports"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$report = Join-Path $out "collect.txt"
if (Test-Path $report) { Remove-Item $report }

function W([string]$s) {
  Add-Content -Path $report -Value $s
  Write-Host $s
}

W "==== ARCH ===="
systeminfo | Select-String -Pattern "System Type|Processor" | ForEach-Object { W $_.Line }
& $py -c "import platform,struct,sys; print('machine', platform.machine()); print('arch', platform.architecture()); print('ptr', struct.calcsize('P')*8); print(sys.version)" | ForEach-Object { W $_ }
W ("ffmpeg: " + (Get-Command ffmpeg -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source))
ffmpeg -version 2>&1 | Select-Object -First 2 | ForEach-Object { W $_ }

W ""
W "==== TIMING ===="
$latest = Get-ChildItem $out -Filter "*timing.txt" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) {
  W $latest.FullName
  Get-Content $latest.FullName | ForEach-Object { W $_ }
} else {
  W "Chua co timing.txt — chay 1 job roi chay lai script nay (ban patch moi giu file)."
}

W ""
W "==== QA ===="
$qa = Get-ChildItem $out -Filter "*segments_qa.json" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($qa) {
  W $qa.FullName
  $env:DYFLOW_QA_JSON = $qa.FullName
  & $py scripts\check_qa_order.py | ForEach-Object { W $_ }
} else {
  W "Chua co segments_qa.json trong _reports — dump tu DB:"
  & $py scripts\dump_qa_from_db.py | ForEach-Object { W $_ }
}

W ""
W "Gui file: $report"
