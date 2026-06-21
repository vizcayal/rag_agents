"""
poll_runtime_status.py — Poll the AgentCore Runtime until it reaches READY state.

Usage:
  python scripts/poll_runtime_status.py
  python scripts/poll_runtime_status.py --arn <runtime_arn>
  python scripts/poll_runtime_status.py --max-attempts 30 --interval 20

Exits 0 when READY, 1 on timeout or terminal failure state.
"""

import argparse
import sys
import time
import boto3
from botocore.exceptions import ClientError

DEFAULT_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:911268715109:"
    "runtime/ragAgent_MyAgent-320L5P7elr"
)
DEFAULT_REGION      = "us-east-1"
DEFAULT_INTERVAL    = 15   # seconds between polls
DEFAULT_MAX_ATTEMPTS = 20  # 20 × 15s = 5 minutes max wait

# States that mean we should stop waiting
READY_STATE     = "READY"
TERMINAL_FAILED = {"FAILED", "DELETE_FAILED", "DELETING", "DELETED"}


def parse_args():
    parser = argparse.ArgumentParser(description="Poll AgentCore Runtime readiness")
    parser.add_argument(
        "--arn", default=DEFAULT_ARN,
        help=f"AgentCore Runtime ARN (default: {DEFAULT_ARN})"
    )
    parser.add_argument(
        "--region", default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})"
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL,
        help=f"Seconds between polls (default: {DEFAULT_INTERVAL})"
    )
    parser.add_argument(
        "--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
        help=f"Maximum poll attempts (default: {DEFAULT_MAX_ATTEMPTS})"
    )
    return parser.parse_args()


def get_runtime_status(client, runtime_arn: str) -> str:
    """
    Retrieve the current status of an AgentCore Runtime.
    Returns the status string, or raises ClientError.
    """
    # Extract the runtime ID from the ARN (last segment after '/')
    runtime_id = runtime_arn.split("/")[-1]

    response = client.get_agent_runtime(agentRuntimeId=runtime_id)
    return response.get("status", "UNKNOWN")


def main() -> int:
    args = parse_args()

    print(f"\n=== Polling AgentCore Runtime Status ===")
    print(f"ARN    : {args.arn}")
    print(f"Region : {args.region}")
    print(f"Max wait: {args.max_attempts * args.interval}s "
          f"({args.max_attempts} attempts × {args.interval}s)\n")

    try:
        client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    except Exception as e:
        print(f"❌ Failed to create boto3 client: {e}")
        return 1

    for attempt in range(1, args.max_attempts + 1):
        try:
            status = get_runtime_status(client, args.arn)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg  = e.response["Error"]["Message"]
            print(f"  [{attempt:2d}/{args.max_attempts}] ClientError: {code} — {msg}")
            if code in ("ResourceNotFoundException",):
                print("❌ Runtime not found. Check the ARN.")
                return 1
        except Exception as e:
            print(f"  [{attempt:2d}/{args.max_attempts}] Error: {e}")
        else:
            print(f"  [{attempt:2d}/{args.max_attempts}] Status: {status}")

            if status == READY_STATE:
                print(f"\n✅ Runtime is READY.\n")
                return 0

            if status in TERMINAL_FAILED:
                print(f"\n❌ Runtime entered terminal state: {status}. "
                      f"Check the deploy logs.\n")
                return 1

        if attempt < args.max_attempts:
            time.sleep(args.interval)

    print(f"\n❌ Timed out after {args.max_attempts * args.interval}s. "
          f"Runtime did not reach READY state.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
