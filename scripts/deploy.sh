#!/usr/bin/env bash
# Deploy the stack.
#   AWS_PROFILE=<profile> scripts/deploy.sh          asks for confirmation first
#   AWS_PROFILE=<profile> scripts/deploy.sh --yes    no questions
# Optional: SENDER_EMAIL=you@example.com sets the verified SES sender for this deploy
# (otherwise it is read from a local, git-ignored cdk.context.json).
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv and installing requirements ..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

echo "Running tests ..."
.venv/bin/python -m pytest -q
echo

SENDER_ARGS=()
if [ -n "${SENDER_EMAIL:-}" ]; then
  SENDER_ARGS=(-c "sender_email=$SENDER_EMAIL")
elif ! grep -qs '"sender_email"' cdk.context.json; then
  echo "Note: no sender email is set, so the daily email will be skipped."
  echo "      Set SENDER_EMAIL=you@example.com (a verified SES address) to enable it."
  echo
fi

if [ "$ASSUME_YES" != 1 ]; then
  read -r -p "Deploy to AWS account $ACCOUNT? [y/N] " reply
  if [ "$reply" != "y" ] && [ "$reply" != "Y" ]; then echo "Cancelled."; exit 1; fi
fi

cdk deploy --require-approval never ${SENDER_ARGS[@]+"${SENDER_ARGS[@]}"}
