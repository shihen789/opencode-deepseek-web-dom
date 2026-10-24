$env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'
$env:ELECTRON_BUILDER_BINARIES_MIRROR = 'https://npmmirror.com/mirrors/electron-builder-binaries/'
Set-Location 'C:/Users/39841/Documents/Default Project/uuid_manager'

# 清理失败的缓存
Remove-Item -Recurse -Force 'C:/Users/39841/AppData/Local/electron-builder/Cache/winCodeSign' -ErrorAction SilentlyContinue

Write-Host 'Building with sign disabled...' -ForegroundColor Cyan
npx electron-builder --win portable --config.win.signAndEditExecutable=false
