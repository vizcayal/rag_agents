#!/bin/bash
# deploy_backend.sh — Deploy the RAG Agent backend via agentcore CLI
# Usage: bash scripts/deploy_backend.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTCORE_DIR="$SCRIPT_DIR/../agentcore"
LOG_DIR="$AGENTCORE_DIR/.cli/logs/deploy"

echo "=== Phase 1: Backend Deployment ==="
echo "Working directory: $AGENTCORE_DIR"
echo ""

cd "$AGENTCORE_DIR"

echo "Running: agentcore deploy --target default"
echo "-------------------------------------------"

if agentcore deploy --target default; then
    echo ""
    echo "✅ agentcore deploy completed successfully."
else
    EXIT_CODE=$?
    echo ""
    echo "❌ agentcore deploy failed (exit code $EXIT_CODE)."
    echo ""
    echo "--- Latest deploy log ---"
    LATEST_LOG=$(ls -t "$LOG_DIR"/deploy-*.log 2>/dev/null | head -1)
    if [ -n "$LATEST_LOG" ]; then
        echo "Log file: $LATEST_LOG"
        tail -50 "$LATEST_LOG"
    else
        echo "(No deploy log found)"
    fi
    exit $EXIT_CODE
fi
