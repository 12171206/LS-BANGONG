<#
.SYNOPSIS
  把 LS BANGONG 的 6 个律师办案技能安装到本机 DSH 技能目录。

.DESCRIPTION
  复制 skills\ 下每个技能目录到目标技能目录，并把技能文件里作者本机的绝对路径
  （C:\Users\<作者>\.dsh\skills）改写成你本机的实际路径。
  DSH 的技能目录是热加载的，安装后无需重启。

.PARAMETER SkillsDir
  目标技能目录。默认 %USERPROFILE%\.dsh\skills

.PARAMETER Force
  目标已存在时覆盖。

.EXAMPLE
  pwsh -File .\install.ps1

.EXAMPLE
  pwsh -File .\install.ps1 -SkillsDir "D:\dsh\skills" -Force
#>
[CmdletBinding()]
param(
    [string]$SkillsDir = (Join-Path $env:USERPROFILE '.dsh\skills'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$srcRoot = Join-Path $here 'skills'
$authorPath = '%USERPROFILE%\.dsh\skills'
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path -LiteralPath $srcRoot)) {
    throw "找不到 skills 目录：$srcRoot（请在仓库根目录运行本脚本）"
}

if (-not (Test-Path -LiteralPath $SkillsDir)) {
    Write-Host "目标目录不存在，创建：$SkillsDir"
    New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null
}

$SkillsDir = (Resolve-Path -LiteralPath $SkillsDir).Path
$skills = Get-ChildItem -LiteralPath $srcRoot -Directory
Write-Host ("发现 {0} 个技能，目标目录：{1}" -f $skills.Count, $SkillsDir)
Write-Host ''

$installed = 0
$skipped = 0
foreach ($skill in $skills) {
    $dest = Join-Path $SkillsDir $skill.Name
    if ((Test-Path -LiteralPath $dest) -and (-not $Force)) {
        Write-Host ("跳过（已存在，加 -Force 覆盖）：{0}" -f $skill.Name)
        $skipped++
        continue
    }
    if (Test-Path -LiteralPath $dest) {
        Remove-Item -LiteralPath $dest -Recurse -Force
    }
    Copy-Item -LiteralPath $skill.FullName -Destination $dest -Recurse -Force

    # 去掉可能带过来的缓存
    Get-ChildItem -LiteralPath $dest -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force

    # 改写作者本机路径 → 本机实际路径
    $files = Get-ChildItem -LiteralPath $dest -Recurse -File -Include *.md, *.ps1, *.py, *.json
    foreach ($f in $files) {
        $text = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
        if ($text.Contains($authorPath)) {
            $text = $text.Replace($authorPath, $SkillsDir)
            $isPs1 = $f.Extension -eq '.ps1'
            $enc = if ($isPs1) { $utf8Bom } else { $utf8NoBom }
            [System.IO.File]::WriteAllText($f.FullName, $text, $enc)
        }
    }

    Write-Host ("已安装：{0}" -f $skill.Name)
    $installed++
}

Write-Host ''
Write-Host ("完成：安装 {0} 个，跳过 {1} 个" -f $installed, $skipped)
Write-Host ''
Write-Host '下一步：'
Write-Host '  1) 打开 DeepSeek Harness，直接说："新案子进来，帮我建案件工作台"'
Write-Host ('  2) 建台命令：& "{0}\lawyer-case-workbench\assets\new-case.ps1" -Root "D:\案件" -CaseId "2026-001" -Name "张三诉李四民间借贷" -Lawyer "你的名字"' -f $SkillsDir)
Write-Host ('  3) 台账校验：python "{0}\lawyer-case-workbench\assets\check-ledger.py" "<案件目录>"' -f $SkillsDir)
Write-Host ''
Write-Host '注意：所有产出均为 AI 辅助草稿，须执业律师审核签字后才能对外使用。'
