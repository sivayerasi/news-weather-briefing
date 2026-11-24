#!/usr/bin/env bash
# Shared setup for deploy.sh and destroy.sh. It is sourced by them, not run directly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STACK="news-weather-briefing"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export CDK_DEFAULT_REGION="$AWS_DEFAULT_REGION"
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1   # hides the harmless "untested Node version" box

ASSUME_YES=0
for arg in "$@"; do
  if [ "$arg" = "--yes" ]; then ASSUME_YES=1; fi
done

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing tool: $1. See README > Setup." >&2; exit 1; }
}
need aws
need cdk
need python3

# CDK cannot always read an SSO login, so hand it temporary credentials from the AWS CLI.
if [ -n "${AWS_PROFILE:-}" ]; then
  if creds="$(aws configure export-credentials --profile "$AWS_PROFILE" --format env 2>/dev/null)"; then
    eval "$creds"
    unset AWS_PROFILE
  fi
fi

if ! ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
  echo "Not logged in to AWS, or the session expired." >&2
  echo "Log in first (for example: aws sso login --profile <profile>), then run:" >&2
  echo "  AWS_PROFILE=<profile> $0" >&2
  exit 1
fi
IDENTITY="$(aws sts get-caller-identity --query Arn --output text)"

echo "AWS account : $ACCOUNT"
echo "Identity    : $IDENTITY"
echo "Region      : $AWS_DEFAULT_REGION"
echo "Stack       : $STACK"
echo
