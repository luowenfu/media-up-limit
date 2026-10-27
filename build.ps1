#Requires -Version 5.1
<#
    版本与发布脚本。不在本机构建镜像。

    .\build.ps1                 测试版最后一段加 1，本地提交
    .\build.ps1 -Bump Release   正式版中间一段加 1，推送、打标签、创建 GitHub Release
#>
[CmdletBinding()]
param(
    [ValidateSet('Test', 'Release')]
    [string]$Bump = 'Test'
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location $Root

$VersionFile = Join-Path $Root 'VERSION'
$Changelog   = Join-Path $Root 'CHANGELOG.md'
$VersionJs   = Join-Path $Root 'app\web\static\js\version.js'
$IndexHtml   = Join-Path $Root 'app\web\templates\index.html'
$Utf8        = New-Object System.Text.UTF8Encoding $false

function Read-Text([string]$Path) {
    return [System.IO.File]::ReadAllText($Path)
}

function Write-Text([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $Utf8)
}

function Stop-Early([string]$Message) {
    Write-Host $Message -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $VersionFile)) { throw '找不到 VERSION' }
if (-not (Test-Path $Changelog)) { throw '找不到 CHANGELOG.md' }

$fields = @{}
foreach ($line in [System.IO.File]::ReadAllLines($VersionFile)) {
    $line = $line.Trim()
    if ($line -match '^(ChineseName|AppName|Version)=(.*)$') {
        $fields[$Matches[1]] = $Matches[2].Trim()
    }
}
if ($fields['Version'] -notmatch '^\d+\.\d+(\.\d+)?$') { throw 'VERSION 里的版本号不合法' }
if (-not $fields['ChineseName']) { throw 'VERSION 缺少 ChineseName' }
if (-not $fields['AppName']) { throw 'VERSION 缺少 AppName' }

$log = Read-Text $Changelog
$firstHeading = [regex]::Match($log, '(?m)^## .+$')
if (-not $firstHeading.Success -or $firstHeading.Value.Trim() -ne '## 待定') {
    Stop-Early 'CHANGELOG.md 最上面没有 ## 待定，已停止'
}
$draft = [regex]::Match($log, '(?ms)^## 待定[ \t]*\r?\n(.*?)(?=^## |\z)')
if ($draft.Groups[1].Value -notmatch '(?m)^- ') {
    Stop-Early '## 待定 下面没有条目，已停止'
}

$current = $fields['Version']
$parts = $current.Split('.')
$major = [int]$parts[0]
$minor = [int]$parts[1]
$patch = if ($parts.Count -ge 3) { [int]$parts[2] } else { 0 }
if ($Bump -eq 'Test') {
    $next = '{0}.{1}.{2}' -f $major, $minor, ($patch + 1)
} elseif ($minor -ge 99) {
    $next = '{0}.0' -f ($major + 1)
} else {
    $next = '{0}.{1}' -f $major, ($minor + 1)
}

$log = [regex]::Replace($log, '(?m)^## 待定[ \t]*\r?$', "## $next", 1)
Write-Text $Changelog $log

$versionText = "ChineseName=$($fields['ChineseName'])`nAppName=$($fields['AppName'])`nVersion=$next`n"
Write-Text $VersionFile $versionText

$js = Read-Text $VersionJs
$js = [regex]::Replace($js, "export const APP_VERSION = 'v[^']*'", "export const APP_VERSION = 'v$next'")
Write-Text $VersionJs $js

$html = Read-Text $IndexHtml
$replacement = '${1}v' + $next + '${2}'
$html = [regex]::Replace($html, '(<span class="ver" id="ver">)v[^<]*(</span>)', $replacement)
Write-Text $IndexHtml $html

git add -A
if ($LASTEXITCODE -ne 0) { throw 'git add 失败' }
$staged = @(git diff --cached --name-only)
if (-not $staged) { Stop-Early '没有需要提交的变更' }

git commit -m "v$next"
if ($LASTEXITCODE -ne 0) { throw 'git 提交失败' }
Write-Host "已提交 v$next" -ForegroundColor Green

if ($Bump -ne 'Release') {
    Write-Host '未推送' -ForegroundColor DarkGray
    exit 0
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw '未检测到 gh，正式版还没有推送'
}

git push origin HEAD
if ($LASTEXITCODE -ne 0) { throw '推送分支失败' }

$tag = "v$next"
git tag $tag
if ($LASTEXITCODE -ne 0) { throw "创建标签 $tag 失败" }
git push origin $tag
if ($LASTEXITCODE -ne 0) { throw "推送标签 $tag 失败" }

$pattern = '(?ms)^## ' + [regex]::Escape($next) + '\s*\r?\n(.*?)(?=^## |\z)'
$notes = [regex]::Match((Read-Text $Changelog), $pattern).Groups[1].Value.Trim()
$notesFile = [System.IO.Path]::GetTempFileName()
Write-Text $notesFile $notes
gh release create $tag --title $tag --notes-file $notesFile
if ($LASTEXITCODE -ne 0) { throw '标签已推送，但创建 GitHub Release 失败' }
Write-Host "已发布 $tag" -ForegroundColor Green
