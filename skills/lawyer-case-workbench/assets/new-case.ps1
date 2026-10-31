<#
.SYNOPSIS
  Create a standard lawyer case workbench folder tree and seed it with the four templates.

.DESCRIPTION
  One case = one folder. This script builds the folders that the
  lawyer-case-workbench skill prescribes, copies the templates in, and fills
  in case id / case name / lawyer. Files are written as UTF-8 (with BOM) so
  Chinese stays readable in Windows PowerShell 5.1 as well as PowerShell 7.

.EXAMPLE
  .\new-case.ps1 -Root "D:\案件" -CaseId "2026-015" -Name "张三诉李四民间借贷" -Lawyer "唐律师"

.EXAMPLE
  .\new-case.ps1 -Root "D:\案件" -CaseId "2026-016" -Name "王某劳动合同纠纷" -Force
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$CaseId,
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$Lawyer = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$TemplateDir = $PSScriptRoot
$utf8Bom = New-Object System.Text.UTF8Encoding($true)

$caseDir = Join-Path $Root ("案件-{0}-{1}" -f $CaseId, $Name)
if ((Test-Path -LiteralPath $caseDir) -and (-not $Force)) {
    throw "Already exists: $caseDir  (use -Force to re-seed templates)"
}

$dirs = @(
    '00-案件档案',
    '01-材料收集\当事人提供',
    '01-材料收集\律师调取',
    '02-证据整理',
    '03-法律研究\类案判例',
    '04-合同审查',
    '05-诉讼策略',
    '06-文书产出',
    '07-程序记录',
    '08-结案归档'
)

foreach ($d in $dirs) {
    $p = Join-Path $caseDir $d
    if (-not (Test-Path -LiteralPath $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
    }
}

function Copy-Template {
    param([string]$TemplateName, [string]$TargetSubPath)
    $src = Join-Path $TemplateDir $TemplateName
    if (-not (Test-Path -LiteralPath $src)) {
        Write-Warning "Template missing: $src"
        return
    }
    $target = Join-Path $caseDir $TargetSubPath
    $existing = Test-Path -LiteralPath $target
    if ($existing -and (-not $Force)) {
        Write-Host ("skip (exists): {0}" -f $TargetSubPath)
        return
    }
    $text = Get-Content -LiteralPath $src -Raw -Encoding UTF8
    [System.IO.File]::WriteAllText($target, $text, $utf8Bom)
    Write-Host ("seeded: {0}" -f $TargetSubPath)
}

Copy-Template -TemplateName '模板-案件基本信息.md' -TargetSubPath '00-案件档案\案件基本信息.md'
Copy-Template -TemplateName '模板-案件生命周期Checklist.md' -TargetSubPath '00-案件档案\案件生命周期Checklist.md'
Copy-Template -TemplateName '模板-案件台账.md' -TargetSubPath '00-案件档案\案件台账.md'
Copy-Template -TemplateName '模板-律师交付包清单.md' -TargetSubPath '00-案件档案\律师交付包清单.md'
Copy-Template -TemplateName '模板-期限与时效台账.md' -TargetSubPath '00-案件档案\期限与时效台账.md'
Copy-Template -TemplateName '模板-利益冲突审查表.md' -TargetSubPath '00-案件档案\利益冲突审查表.md'
Copy-Template -TemplateName '模板-证据原件交接单.md' -TargetSubPath '02-证据整理\证据原件交接单.md'
Copy-Template -TemplateName '模板-材料收集清单.md' -TargetSubPath '01-材料收集\材料收集清单.md'
Copy-Template -TemplateName '模板-证据目录.md' -TargetSubPath '02-证据整理\证据目录.md'

# Fill in the known values on the fresh case-info sheet.
$infoPath = Join-Path $caseDir '00-案件档案\案件基本信息.md'
if (Test-Path -LiteralPath $infoPath) {
    $text = [System.IO.File]::ReadAllText($infoPath, [System.Text.Encoding]::UTF8)
    $text = $text.Replace('- 案件编号：2026-XXX', ("- 案件编号：{0}" -f $CaseId))
    $text = $text.Replace('- 案件名称：原告 诉 被告 案由', ("- 案件名称：{0}" -f $Name))
    if ($Lawyer) {
        $text = $text.Replace('- 承办律师：', ("- 承办律师：{0}" -f $Lawyer))
    }
    $text = $text -replace '(?m)^- 建档日期：\s*$', ("- 建档日期：{0}" -f (Get-Date -Format 'yyyy-MM-dd'))
    [System.IO.File]::WriteAllText($infoPath, $text, $utf8Bom)
}

# Fill in the ledger header rows (case id / name / lawyer / stage / change log).
$ledgerPath = Join-Path $caseDir '00-案件档案\案件台账.md'
if (Test-Path -LiteralPath $ledgerPath) {
    $text = [System.IO.File]::ReadAllText($ledgerPath, [System.Text.Encoding]::UTF8)
    $text = $text.Replace('| 案件编号 | 2026-XXX |', ("| 案件编号 | {0} |" -f $CaseId))
    $text = $text.Replace('| 案件名称 | 原告 诉 被告 案由 |', ("| 案件名称 | {0} |" -f $Name))
    if ($Lawyer) {
        $text = $text.Replace('| 承办律师 | |', ("| 承办律师 | {0} |" -f $Lawyer))
    }
    $text = $text.Replace('| 当前阶段 | 咨询中 / 已接案', '| 当前阶段 | 咨询中')
    $text = $text.Replace('| | | 建台账 | 建台 |', ("| $(Get-Date -Format 'yyyy-MM-dd') | {0} | 建台账（{1}） | 建台 |" -f $Lawyer, $CaseId))
    [System.IO.File]::WriteAllText($ledgerPath, $text, $utf8Bom)
}

# Seed the 07 timeline with an opening entry.
$logPath = Join-Path $caseDir '07-程序记录\案件时间线.md'
if ((-not (Test-Path -LiteralPath $logPath)) -or $Force) {
    $log = @"
# 案件时间线

> 只追加，不删行。每个动作写一行：日期 / 做了什么 / 谁做的 / 产出文件。

| 日期 | 动作 | 责任人 | 产出文件 |
|---|---|---|---|
| $(Get-Date -Format 'yyyy-MM-dd') | 建案件工作台（$CaseId） | $Lawyer | 00-案件档案\案件基本信息.md |
"@
    [System.IO.File]::WriteAllText($logPath, $log, $utf8Bom)
}

Write-Host ''
Write-Host ("Case workbench ready: {0}" -f $caseDir)
Get-ChildItem -LiteralPath $caseDir -Recurse -File | ForEach-Object {
    Write-Host ("  {0}" -f $_.FullName.Substring($caseDir.Length + 1))
}
Write-Host ''
Write-Host 'Next: fill 00-案件档案\案件基本信息.md, then start the stage skill'
Write-Host '  consultation -> lawyer-legal-research'
Write-Host '  evidence     -> lawyer-evidence-handling'
Write-Host '  contract     -> lawyer-contract-review'
Write-Host '  strategy     -> lawyer-litigation-analysis'
Write-Host '  pleadings    -> lawyer-doc-drafting'
