$ErrorActionPreference = 'Stop'
. "$HOME\Github\ai-skill-library\skills\bws\scripts\bws-env.ps1"
python (Join-Path $PSScriptRoot 'deploy-cloudflare.py')
