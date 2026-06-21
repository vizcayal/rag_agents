"""
Pre-deployment verification script for the RAG Agent.

Checks:
  1. AWS credentials are active and point to account 911268715109 in us-east-1
  2. agentcore CLI is installed
  3. agentcore.json target config is correct (account + region)
  4. CDK node_modules are installed
  5. KNOWLEDGE_BASE_ID is correctly set in agentcore.json

Usage:
  python scripts/verify_prereqs.py

Returns exit code 0 if all checks pass, non-zero otherwise.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — all derived from the project config
# ---------------------------------------------------------------------------

EXPECTED_ACCOUNT   = "911268715109"
EXPECTED_REGION    = "us-east-1"
EXPECTED_KB_ID     = "UGVUHIVZKU"

# Paths are relative to the ragAgent/ directory
SCRIPT_DIR     = Path(__file__).resolve().parent
RAGAGENT_DIR   = SCRIPT_DIR.parent
AGENTCORE_DIR  = RAGAGENT_DIR / "agentcore"
AGENTCORE_JSON = AGENTCORE_DIR / "agentcore.json"
CDK_NODE_MOD   = AGENTCORE_DIR / "cdk" / "node_modules"


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def check_aws_identity() -> tuple[bool, str]:
    """Verify AWS credentials resolve to the expected account and region."""
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return False, f"aws sts failed: {result.stderr.strip()}"

        account = result.stdout.strip()
        if account != EXPECTED_ACCOUNT:
            return False, f"Account mismatch: got {account!r}, expected {EXPECTED_ACCOUNT!r}"

        # Check region via environment or AWS config
        region = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or _get_aws_config_region()
        )
        if region and region != EXPECTED_REGION:
            return False, (
                f"Region mismatch: got {region!r}, expected {EXPECTED_REGION!r}. "
                f"Set AWS_REGION=us-east-1 or update your AWS profile."
            )

        return True, f"Account {account} / region {region or '(not explicitly set — defaulting to us-east-1)'}"

    except FileNotFoundError:
        return False, "AWS CLI not found. Install it from https://aws.amazon.com/cli/"
    except subprocess.TimeoutExpired:
        return False, "aws sts timed out (check network / VPN)"


def _get_aws_config_region() -> str:
    """Read the region from the AWS config file if available."""
    try:
        result = subprocess.run(
            ["aws", "configure", "get", "region"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def check_agentcore_cli() -> tuple[bool, str]:
    """Verify the agentcore CLI is installed."""
    path = shutil.which("agentcore")
    if path:
        # Try to get version for info
        try:
            result = subprocess.run(
                ["agentcore", "--version"], capture_output=True, text=True, timeout=10
            )
            version = result.stdout.strip() or result.stderr.strip()
            return True, f"Found at {path}" + (f" ({version})" if version else "")
        except Exception:
            return True, f"Found at {path}"
    return False, (
        "agentcore CLI not found. "
        "Install it with: pip install bedrock-agentcore-starter-toolkit"
    )


def check_agentcore_json() -> tuple[bool, str]:
    """Verify agentcore.json exists and targets the correct account/region."""
    if not AGENTCORE_JSON.exists():
        return False, f"agentcore.json not found at {AGENTCORE_JSON}"

    try:
        cfg = json.loads(AGENTCORE_JSON.read_text())
    except json.JSONDecodeError as e:
        return False, f"agentcore.json is invalid JSON: {e}"

    # Check aws-targets.json for the default target
    targets_file = AGENTCORE_DIR / "aws-targets.json"
    if targets_file.exists():
        try:
            targets = json.loads(targets_file.read_text())
            default = next((t for t in targets if t.get("name") == "default"), None)
            if default:
                acct = default.get("account", "")
                rgn  = default.get("region", "")
                if acct != EXPECTED_ACCOUNT:
                    return False, f"aws-targets.json 'default' account is {acct!r}, expected {EXPECTED_ACCOUNT!r}"
                if rgn != EXPECTED_REGION:
                    return False, f"aws-targets.json 'default' region is {rgn!r}, expected {EXPECTED_REGION!r}"
        except Exception as e:
            return False, f"Could not parse aws-targets.json: {e}"

    return True, f"Config OK — name={cfg.get('name')!r}, target=default ({EXPECTED_ACCOUNT}/{EXPECTED_REGION})"


def check_cdk_node_modules() -> tuple[bool, str]:
    """Verify CDK node_modules are installed."""
    if CDK_NODE_MOD.exists() and any(CDK_NODE_MOD.iterdir()):
        return True, f"node_modules present at {CDK_NODE_MOD}"
    return False, (
        f"CDK node_modules not found at {CDK_NODE_MOD}. "
        f"Run: cd {AGENTCORE_DIR / 'cdk'} && npm install"
    )


def check_knowledge_base_id() -> tuple[bool, str]:
    """Verify KNOWLEDGE_BASE_ID is set correctly in agentcore.json."""
    if not AGENTCORE_JSON.exists():
        return False, "agentcore.json not found — run check 3 first"

    try:
        cfg = json.loads(AGENTCORE_JSON.read_text())
    except json.JSONDecodeError:
        return False, "agentcore.json is invalid JSON"

    runtimes = cfg.get("runtimes", [])
    if not runtimes:
        return False, "No runtimes defined in agentcore.json"

    env_vars = runtimes[0].get("envVars", [])
    kb_entries = [e for e in env_vars if e.get("name") == "KNOWLEDGE_BASE_ID"]

    if not kb_entries:
        return False, (
            f"KNOWLEDGE_BASE_ID not found in runtimes[0].envVars. "
            f"Add: {{\"name\": \"KNOWLEDGE_BASE_ID\", \"value\": \"{EXPECTED_KB_ID}\"}}"
        )

    actual_value = kb_entries[0].get("value", "")
    if actual_value != EXPECTED_KB_ID:
        return False, (
            f"KNOWLEDGE_BASE_ID is {actual_value!r}, expected {EXPECTED_KB_ID!r}"
        )

    return True, f"KNOWLEDGE_BASE_ID = {actual_value!r}"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

CHECKS = [
    ("AWS credentials & identity",    check_aws_identity),
    ("agentcore CLI installed",        check_agentcore_cli),
    ("agentcore.json config",          check_agentcore_json),
    ("CDK node_modules installed",     check_cdk_node_modules),
    ("KNOWLEDGE_BASE_ID in config",    check_knowledge_base_id),
]

GREEN  = "\033[32m"
RED    = "\033[31m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def main() -> int:
    print(f"\n{BOLD}Pre-deployment verification — RAG Agent{RESET}")
    print("=" * 55)

    failures = 0
    for label, fn in CHECKS:
        passed, detail = fn()
        status  = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        print(f"  [{status}] {label}")
        print(f"         {detail}")
        if not passed:
            failures += 1

    print("=" * 55)
    if failures == 0:
        print(f"{GREEN}{BOLD}All checks passed. Ready to deploy.{RESET}\n")
        return 0
    else:
        print(f"{RED}{BOLD}{failures} check(s) failed. Fix the issues above before deploying.{RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
