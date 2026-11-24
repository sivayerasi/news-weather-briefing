#!/usr/bin/env bash
# Remove everything the stack created.
#   AWS_PROFILE=<profile> scripts/destroy.sh          you must type the stack name to confirm
#   AWS_PROFILE=<profile> scripts/destroy.sh --yes    no questions (use with care)
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

echo "This PERMANENTLY deletes the stack '$STACK' from account $ACCOUNT:"
echo "  - the Lambda, the API, the daily schedule and the IAM role"
echo "  - the S3 bucket and the web page in it"
echo "  - the DynamoDB table, including saved settings and the audit log"
echo

if [ "$ASSUME_YES" != 1 ]; then
  read -r -p "Type the stack name ($STACK) to confirm: " typed
  if [ "$typed" != "$STACK" ]; then echo "Cancelled. Nothing was deleted."; exit 1; fi
fi

cdk destroy "$STACK" --force
