"""
smoke_test_backend.py — End-to-end smoke test for the AgentCore Runtime backend.

Sends a real query to the deployed runtime and validates the response.

Usage:
  python scripts/smoke_test_backend.py
  python scripts/smoke_test_backend.py --prompt "What is a high-risk AI system?"
  python scripts/smoke_test_backend.py --arn <runtime_arn>

Exits 0 on PASS, 1 on FAIL.
"""

import argparse
import json
import sys
import boto3
from botocore.exceptions import ClientError

DEFAULT_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:911268715109:"
    "runtime/ragAgent_MyAgent-3AkJyICSTJ"
)
DEFAULT_REGION = "us-east-1"
DEFAULT_PROMPT = "What is AI?"

GREEN = "\033[32m"
RED   = "\033[31m"
BOLD  = "\033[1m"
RESET = "\033[0m"


def parse_args():
    parser = argparse.ArgumentParser(description="Backend smoke test")
    parser.add_argument("--arn",    default=DEFAULT_ARN,    help="AgentCore Runtime ARN")
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Test prompt to send")
    return parser.parse_args()


def invoke_runtime(client, arn: str, prompt: str, session_id: str = "smoke-test-001-deploy-verification-run") -> dict:
    """Invoke the AgentCore Runtime and return the parsed JSON response."""
    payload = json.dumps({"prompt": prompt}).encode("utf-8")

    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=payload,
        contentType="application/json",
        runtimeSessionId=session_id,
    )

    # Read the EventStream
    raw = ""
    for event in response.get("response", []):
        if isinstance(event, bytes):
            raw += event.decode("utf-8")
        elif isinstance(event, str):
            raw += event
        elif isinstance(event, dict):
            if "chunk" in event:
                chunk_data = event["chunk"].get("bytes", b"")
                raw += chunk_data.decode("utf-8") if isinstance(chunk_data, bytes) else str(chunk_data)
            elif "internalServerException" in event:
                raise RuntimeError(f"Internal server error: {event['internalServerException']}")
            elif "badRequestException" in event:
                raise RuntimeError(f"Bad request: {event['badRequestException']}")

    if not raw:
        raise ValueError("Runtime returned an empty response body")

    return json.loads(raw)


def run_smoke_test(args) -> int:
    print(f"\n{BOLD}Backend Smoke Test — AgentCore Runtime{RESET}")
    print("=" * 55)
    print(f"  ARN    : {args.arn}")
    print(f"  Prompt : {args.prompt!r}")
    print("=" * 55)

    try:
        client = boto3.client("bedrock-agentcore", region_name=args.region)
    except Exception as e:
        print(f"{RED}FAIL{RESET} Could not create boto3 client: {e}")
        return 1

    try:
        print("  Invoking runtime...", end="", flush=True)
        result = invoke_runtime(client, args.arn, args.prompt)
        print(" done.\n")
    except ClientError as e:
        print(f"\n{RED}{BOLD}FAIL{RESET}")
        print(f"  AWS error: {e.response['Error']['Code']} — {e.response['Error']['Message']}")
        return 1
    except Exception as e:
        print(f"\n{RED}{BOLD}FAIL{RESET}")
        print(f"  Error: {e}")
        return 1

    # -----------------------------------------------------------------------
    # Assertions
    # -----------------------------------------------------------------------
    failures = []

    # 1. response field must be present and non-empty
    answer = result.get("response") or result.get("answer")
    if not answer or not isinstance(answer, str) or len(answer.strip()) == 0:
        failures.append(f"'response' field is missing or empty (got: {answer!r})")

    # 2. status must not be a hard failure
    status = result.get("status", "unknown")
    if status == "failed" and result.get("error"):
        failures.append(f"Pipeline failed with error: {result['error']}")

    # 3. confidence should be > 0 (soft check — warn but don't fail)
    confidence = result.get("confidence", 0.0)
    low_confidence = confidence == 0.0

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    if failures:
        print(f"{RED}{BOLD}FAIL{RESET}")
        for f in failures:
            print(f"  ✗ {f}")
        print(f"\n  Raw result: {json.dumps(result, indent=2)}")
        return 1

    print(f"{GREEN}{BOLD}PASS{RESET}")
    print(f"  Status     : {status}")
    print(f"  Confidence : {confidence:.2f}" + (" ⚠ (low)" if low_confidence else ""))
    print(f"  Cited      : {result.get('cited_chunks', result.get('cited_chunk_ids', []))}")
    print(f"\n  Response:")
    # Word-wrap the answer for readability
    words = answer.split()
    line, lines = [], []
    for w in words:
        line.append(w)
        if len(" ".join(line)) > 80:
            lines.append("    " + " ".join(line[:-1]))
            line = [w]
    if line:
        lines.append("    " + " ".join(line))
    print("\n".join(lines))

    if result.get("issues"):
        print(f"\n  ⚠ Validator issues: {result['issues']}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(run_smoke_test(parse_args()))
