#!/usr/bin/env python3
"""Enable required Google sign-in after creating the production web OAuth client."""

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "wrangler.jsonc"
EXPECTED_ORIGIN = "github-analytics.yuvalyeret.com"
EXPECTED_DATABASE = "github-flow-analytics-users"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True, help="Google OAuth Web client ID")
    args = parser.parse_args()

    if not args.client_id.endswith(".apps.googleusercontent.com"):
        parser.error("--client-id must be a Google OAuth Web client ID")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not any(item.get("database_name") == EXPECTED_DATABASE and item.get("database_id")
               for item in config.get("d1_databases", [])):
        raise SystemExit("The users D1 binding is missing or has no database ID.")
    if not any(item.get("pattern") == EXPECTED_ORIGIN and item.get("custom_domain") is True
               for item in config.get("routes", [])):
        raise SystemExit(f"The {EXPECTED_ORIGIN} custom domain route is missing.")

    config.setdefault("vars", {})["AUTH_REQUIRED"] = "true"
    config["vars"]["GOOGLE_OAUTH_CLIENT_ID"] = args.client_id
    CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print("Google sign-in is enabled in Wrangler configuration for the next deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
