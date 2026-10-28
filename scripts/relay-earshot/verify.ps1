param(
    [string]$ApiBase = $env:TP_RELAY_BASE_URL,
    [string]$AdminUrl = $env:EARSHOT_ADMIN_URL,
    [string]$SshTarget = $env:EARSHOT_SSH_TARGET,
    [string]$LocalEvidence = '',
    [string]$OfflistEvidence = '',
    [string]$Output = (Join-Path $PSScriptRoot 'verification.json'),
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$arguments = @((Join-Path $PSScriptRoot 'verify-relay.py'), '--output', $Output)
if($ApiBase){ $arguments += @('--api-base', $ApiBase) }
if($AdminUrl){ $arguments += @('--admin-url', $AdminUrl) }
if($SshTarget){ $arguments += @('--ssh-target', $SshTarget) }
if($LocalEvidence){ $arguments += @('--local-evidence', $LocalEvidence) }
if($OfflistEvidence){ $arguments += @('--offlist-evidence', $OfflistEvidence) }
& $Python @arguments
exit $LASTEXITCODE
