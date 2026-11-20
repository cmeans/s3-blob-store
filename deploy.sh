#!/usr/bin/env bash
# s3-blob-store: deploy (or, with ROTATE=1, rotate the token of) the store
# from template.yaml in the current directory. Written for AWS CloudShell
# (bash, the AWS CLI, Python 3), and published as a release asset beside
# template.yaml. See README.md, step 3.
#
#   bash deploy.sh                      first deploy of the default store
#   ROTATE=1 bash deploy.sh             new token for an existing store
#   NAME_SUFFIX=<x> bash deploy.sh      a second store, s3-blob-store-<x>
#
# The token is generated here, written to a mode-600 file in /tmp before
# the deploy, and never printed. Only its SHA-256 digest goes to AWS.
#
# Exit status: 0 deployed; 1 the deploy failed and nothing changed; 2 the
# stack's state couldn't be confirmed, or the outcome of the deploy isn't
# known yet; 3 stopped before deploying (see the message).

set -u
set -o pipefail

say() { printf '%s\n' "$*"; }

suffix=${NAME_SUFFIX:-}
if [ -n "$suffix" ]; then
  if ! [[ "$suffix" =~ ^[a-z0-9]([a-z0-9-]{0,19}[a-z0-9])?$ ]]; then
    say "Stopped: NAME_SUFFIX must be 1-21 lowercase letters, digits and hyphens, starting and ending with a letter or digit."
    exit 3
  fi
  stack="s3-blob-store-$suffix"
else
  stack="s3-blob-store"
fi
token_file="/tmp/$stack-token.txt"
rotate=${ROTATE:-}

if [ ! -f template.yaml ]; then
  say "Stopped: no template.yaml in $(pwd). Download it from the release first (README, step 3)."
  exit 3
fi

stack_status() {
  aws cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].StackStatus" --output text 2>&1
}

# When the stack last changed, to tell "the deploy did nothing" from "the
# deploy changed something" after a failure.
stack_updated() {
  aws cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].LastUpdatedTime" --output text 2>/dev/null
}

# 1. What's there now. Anything other than "does not exist" or a settled
#    state stops here: an error must never read as "no stack".
status=$(stack_status)
rc=$?
if [ $rc -ne 0 ]; then
  case "$status" in
    *"does not exist"*) mode=create ;;
    *)
      say "Stopped: couldn't confirm the state of stack $stack. Nothing was changed. AWS said:"
      say "$status"
      exit 2 ;;
  esac
else
  case "$status" in
    CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE|IMPORT_ROLLBACK_COMPLETE)
      if [ "$rotate" = 1 ]; then
        mode=rotate
        before=$(stack_updated)
        before_status=$status
      else
        say "Stopped: stack $stack already exists ($status). Deploying again would rotate its token, and everything using the current token would start getting 401."
        say "To rotate on purpose: ROTATE=1 bash deploy.sh"
        exit 3
      fi ;;
    ROLLBACK_COMPLETE)
      say "Stopped: stack $stack is a failed first deploy (ROLLBACK_COMPLETE). Delete it, then run this again:"
      say "  aws cloudformation delete-stack --stack-name $stack && aws cloudformation wait stack-delete-complete --stack-name $stack"
      exit 3 ;;
    DELETE_FAILED)
      say "Stopped: stack $stack is DELETE_FAILED, usually a deleted store whose bucket still held blobs. See README, Caveats, on deleting the stack."
      exit 3 ;;
    *_IN_PROGRESS)
      say "Stopped: stack $stack is busy ($status). Wait for it to finish, then run this again."
      exit 3 ;;
    *)
      say "Stopped: stack $stack is in state $status, which this script doesn't handle. Nothing was changed."
      exit 3 ;;
  esac
fi

# 2. Never overwrite a token file: it may hold a token not yet saved.
if [ -e "$token_file" ]; then
  say "Stopped: $token_file already exists. It may hold a token you haven't saved yet: save it to your password manager, delete the file, then run this again."
  exit 3
fi

# 3. The token and its digest, generated here; the token goes to the file
#    before the deploy starts, so a session that drops mid-deploy keeps it.
read -r token digest < <(python3 -c 'import secrets,hashlib;t=secrets.token_hex(32);print(t,hashlib.sha256(t.encode()).hexdigest())')
if ! [[ "${token:-}" =~ ^[0-9a-f]{64}$ && "${digest:-}" =~ ^[0-9a-f]{64}$ ]]; then
  say "Stopped: couldn't generate a token (is python3 available?). Nothing was changed."
  exit 3
fi
(umask 077 && printf '%s' "$token" > "$token_file") || {
  say "Stopped: couldn't write $token_file. Nothing was changed."
  exit 3
}
unset token

# 4. Deploy. Ctrl-C stops the CLI's wait, not the stack update, so it's
#    handled like any other failure: by asking the stack what happened.
trap 'say ""; say "Interrupted: checking the stack before deciding about the token file."' INT
params=("BearerTokenSha256=$digest")
[ -n "$suffix" ] && params+=("NameSuffix=$suffix")
aws cloudformation deploy --template-file template.yaml --stack-name "$stack" \
  --capabilities CAPABILITY_IAM --parameter-overrides "${params[@]}"
deploy_rc=$?
trap - INT
unset digest

if [ $deploy_rc -eq 0 ]; then
  aws cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Outputs" --output table
  say "Deployed. The token is in $token_file: take it out with Actions > Download file (README, step 4)."
  exit 0
fi

# 5. The deploy failed or was interrupted. Keep or remove the token file
#    only on what the stack says, never on the exit code alone.
status=$(stack_status)
if [ "$mode" = rotate ] && [ "$status" = "$before_status" ] && [ -n "${before:-}" ] && [ "$(stack_updated)" = "$before" ]; then
  rm -f "$token_file"
  say "Rotation failed before changing the stack (see above): the current token still works, and the new token file was removed."
  exit 1
fi
case "$mode:$status" in
  create:*"does not exist"*|create:ROLLBACK_COMPLETE|create:ROLLBACK_FAILED|create:DELETE_COMPLETE)
    rm -f "$token_file"
    say "Deploy failed (see above): no store was created, and the token file was removed. Stack status: ${status}."
    exit 1 ;;
  rotate:UPDATE_ROLLBACK_COMPLETE)
    rm -f "$token_file"
    say "Rotation failed and was rolled back (see above): the current token still works, and the new token file was removed."
    exit 1 ;;
  *)
    say "The deploy's outcome isn't confirmed yet (stack status: ${status}). The token file $token_file is KEPT: it may be the live token."
    say "Watch the stack: aws cloudformation describe-stacks --stack-name $stack --query Stacks[0].StackStatus --output text"
    say "If it ends CREATE_COMPLETE or UPDATE_COMPLETE, the token in the file is live: save it. If it ends in a rollback, delete the file; any previous token still works."
    exit 2 ;;
esac
