#!/usr/bin/env bash
set -euo pipefail
source "$HOME/Github/ai-skill-library/skills/bws/scripts/bws-env.sh"
python3 "$(dirname "$0")/deploy-cloudflare.py"
