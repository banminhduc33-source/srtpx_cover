# Cài đặt gần như tự động: GitHub repo + secret rclone + secret TikTok sessionid + thư mục Drive + mã Apps Script chép sẵn vào clipboard.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location (Split-Path $PSScriptRoot)

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
}
function Need($cmd, $wingetId) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "Đang cài $cmd ..." -ForegroundColor Cyan
    winget install -e --id $wingetId --accept-package-agreements --accept-source-agreements
    Refresh-Path
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
      throw "Đã cài $cmd nhưng cửa sổ này chưa nhận ra. Đóng lại rồi bấm đúp Cai_Dat_Mot_Phat.bat lần nữa."
    }
  }
}
function Fail($msg) { Write-Host "`nLỖI: $msg" -ForegroundColor Red; exit 1 }

Write-Host "== 1/6 Cài công cụ (git, GitHub CLI, rclone) ==" -ForegroundColor Green
Need git Git.Git; Need gh GitHub.cli; Need rclone Rclone.Rclone

Write-Host "`n== 2/6 Đăng nhập GitHub (trình duyệt sẽ mở, bấm cho phép) ==" -ForegroundColor Green
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { gh auth login -h github.com -p https -w -s repo,workflow }
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Chưa đăng nhập GitHub." }
$owner = (gh api user -q .login).Trim()
$name = Read-Host "Tên repo GitHub [srtpx_cover]"
if (-not $name) { $name = 'srtpx_cover' }
$repo = "$owner/$name"

$vis = Read-Host "Repo công khai (public) hay riêng tư (private)? [public/private, mặc định private]"
$visFlag = if ($vis -match '^(?i)pub') { '--public' } else { '--private' }

Write-Host "`n== 3/6 Tạo repo $repo ($($visFlag.TrimStart('-'))) và đẩy code lên ==" -ForegroundColor Green
if (-not (Test-Path .git)) { git init -b main | Out-Null }
if (-not (git config user.name)) { git config user.name $owner }
if (-not (git config user.email)) { git config user.email "$owner@users.noreply.github.com" }
git add -A
git commit -m "srtpx_cover" 2>$null | Out-Null
gh repo view $repo 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  gh repo create $repo $visFlag --source . --remote origin --push
} else {
  git remote get-url origin 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { git remote add origin "https://github.com/$repo.git" }
  git push -u origin main
}
if ($LASTEXITCODE -ne 0) { Fail "Không đẩy được code lên GitHub." }

Write-Host "`n== 4/6 Kết nối Google Drive (trình duyệt sẽ mở, đăng nhập đúng tài khoản Drive muốn dùng) ==" -ForegroundColor Green
$remotes = (rclone listremotes 2>$null) -join "`n"
if ($remotes -notmatch '(?m)^gd:') { rclone config create gd drive scope drive }
rclone mkdir gd:srtpx/in
if ($LASTEXITCODE -ne 0) { Fail "rclone chưa vào được Drive." }
foreach ($d in 'processing','out','done','failed') { rclone mkdir "gd:srtpx/$d" }
$conf = (rclone config file | Where-Object { $_ -match 'rclone\.conf' } | Select-Object -Last 1).Trim()
$txt = Get-Content -Raw -Encoding UTF8 $conf
$m = [regex]::Match($txt, '(?ms)^\[gd\].*?(?=^\[|\z)')
if (-not $m.Success) { Fail "Không thấy mục [gd] trong rclone.conf." }
$m.Value | gh secret set RCLONE_CONF --repo $repo
if ($LASTEXITCODE -ne 0) { Fail "Không lưu được secret RCLONE_CONF." }

Write-Host "`n== 5/6 Lồng tiếng TikTok: lưu sessionid vào GitHub Secrets (không nằm trong code) ==" -ForegroundColor Green
Write-Host "Lấy sessionid: đăng nhập tiktok.com > F12 > Application > Cookies > sessionid. Nên dùng tài khoản phụ." -ForegroundColor DarkGray
$sec = Read-Host "Dán sessionid (khi gõ sẽ không hiện chữ; Enter để bỏ qua, xuất không lồng tiếng)" -AsSecureString
$sid = [System.Net.NetworkCredential]::new('', $sec).Password.Trim()
$sid = $sid -replace '^(?i)sessionid\s*=\s*', ''
if ($sid) {
  $sid | gh secret set TIKTOK_SESSIONID --repo $repo
  if ($LASTEXITCODE -ne 0) { Fail "Không lưu được secret TIKTOK_SESSIONID." }
  Write-Host "Đã lưu secret TIKTOK_SESSIONID (giọng Việt, âm gốc 30%, giọng đọc 100%)." -ForegroundColor Green
} else {
  Write-Host "Bỏ qua. Muốn thêm sau: repo > Settings > Secrets and variables > Actions > New repository secret > TIKTOK_SESSIONID." -ForegroundColor Yellow
}
$sid = $null; $sec = $null

Write-Host "`n== 6/6 Chuẩn bị mã Apps Script ==" -ForegroundColor Green
$token = (gh auth token).Trim()
$gs = Get-Content -Raw -Encoding UTF8 (Join-Path $PWD 'ci\drive_watcher.gs')
$embed = "const EMBED = {GH_REPO: '$repo', GH_TOKEN: '$token'};`n"
Set-Clipboard -Value ($embed + $gs)
Start-Process 'https://script.google.com/create'

Write-Host @"

XONG PHẦN TỰ ĐỘNG. Mã Apps Script (đã kèm sẵn repo + token) đang nằm trong clipboard.
Còn 4 thao tác trong trang Apps Script vừa mở (đăng nhập CÙNG tài khoản Google với Drive):
  1. Bấm vào khung code, Ctrl+A (chọn hết) rồi Ctrl+V (dán), bấm biểu tượng lưu.
  2. Ở ô chọn hàm phía trên, chọn  setup
  3. Bấm Run (Chạy), rồi Review permissions > Advanced > Go to ... (unsafe) > Allow
  4. Xong. Thả video + .srtpx cùng tên vào Drive: srtpx/in

Lưu ý: mã dán vào Apps Script chứa token GitHub của bạn. Dự án đó chỉ mình bạn xem được, đừng chia sẻ nó.
"@ -ForegroundColor Yellow
