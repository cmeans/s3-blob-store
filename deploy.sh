#!/usr/bin/env bash
# s3-blob-store: deploy the store, rotate its token, or check a token file,
# from template.yaml in the current directory. Written for AWS CloudShell
# (bash, the AWS CLI, Python 3), and published as a release asset beside
# template.yaml. See README.md, step 3.
#
#   bash deploy.sh                 first deploy of the default store
#   bash deploy.sh --rotate        new token for an existing store
#   bash deploy.sh --check         is the token in the token file live?
#   add --suffix <x>               for a second store, s3-blob-store-<x>
#
# The token is generated here, written to a mode-600 file in /tmp before
# the deploy, and never printed. Only its SHA-256 digest goes to AWS.
#
# Exit status of a deploy or rotation: 0 deployed, the token file holds the
# live token; 1 failed and nothing changed, the token file was removed; 2
# the outcome isn't known yet, the token file is kept (run --check later);
# 3 stopped before deploying, nothing changed (see the message).
# Exit status of --check: 0 live; 1 not live; 2 can't tell yet.

set -u
set -o pipefail

say() { printf '%s\n' "$*"; }

usage() {
  say "Usage: bash deploy.sh [--rotate | --check] [--suffix <x>]"
  exit "$1"
}

rotate=0
check=0
suffix=
suffix_given=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rotate) rotate=1 ;;
    --check) check=1 ;;
    --suffix)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        say "Stopped: --suffix needs a value."
        usage 3
      fi
      if [ $suffix_given = 1 ]; then
        say "Stopped: give --suffix once."
        usage 3
      fi
      suffix=$2
      suffix_given=1
      shift ;;
    -h|--help) usage 0 ;;
    *) say "Stopped: unknown option $1"; usage 3 ;;
  esac
  shift
done
if [ $rotate = 1 ] && [ $check = 1 ]; then
  say "Stopped: use --rotate or --check, not both."
  exit 3
fi

if [ -n "$suffix" ]; then
  if ! [[ "$suffix" =~ ^[a-z0-9]([a-z0-9-]{0,19}[a-z0-9])?$ ]]; then
    say "Stopped: the suffix must be 1-21 lowercase letters, digits and hyphens, starting and ending with a letter or digit."
    exit 3
  fi
  stack="s3-blob-store-$suffix"
else
  stack="s3-blob-store"
fi
token_file="/tmp/$stack-token.txt"
me="bash deploy.sh${suffix:+ --suffix $suffix}"
# How long to wait before trusting a "nothing changed" reading after a
# failed deploy (an update the CLI started can take a moment to show).
settle=${DEPLOY_SH_SETTLE_SECONDS:-20}

# Reads the stack's status into $status: a CloudFormation status, "none"
# (no such stack), or "unreadable" (with AWS's message in $status_err).
# stdout and stderr are kept apart, so a CLI warning can't pass for a
# status, and "does not exist" is only believed from the error itself.
read_status() {
  local errf out rc
  errf=$(mktemp) || { status=unreadable; status_err="couldn't create a temporary file"; return; }
  out=$(aws cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].StackStatus" --output text 2>"$errf")
  rc=$?
  status_err=$(cat "$errf")
  rm -f "$errf"
  if [ $rc -eq 0 ] && [[ "$out" =~ ^[A-Z_]+$ ]]; then
    status=$out
  elif [ $rc -ne 0 ] && [[ "$status_err" == *"does not exist"* ]]; then
    status=none
  else
    status=unreadable
    [ -n "$status_err" ] || status_err="unexpected reply: $out"
  fi
}

# The digest the store's function actually holds (the stack parameter is
# NoEcho, so the function's configuration is the only place to read it).
# Prints nothing unless it reads a well-formed digest.
live_digest() {
  local fn d
  fn=$(aws cloudformation describe-stack-resource --stack-name "$stack" \
    --logical-resource-id BlobFunction \
    --query "StackResourceDetail.PhysicalResourceId" --output text 2>/dev/null) || return 1
  d=$(aws lambda get-function-configuration --function-name "$fn" \
    --query "Environment.Variables.BEARER_TOKEN_SHA256" --output text 2>/dev/null) || return 1
  [[ "$d" =~ ^[0-9a-f]{64}$ ]] && printf '%s' "$d"
}

file_digest() {
  python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$token_file" 2>/dev/null
}

outputs() {
  aws cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Outputs" --output table
}

# States in which the stack has no working function, so no token is live.
is_gone() {
  case "$1" in none|REVIEW_IN_PROGRESS|ROLLBACK_COMPLETE|ROLLBACK_FAILED|DELETE_COMPLETE) return 0 ;; *) return 1 ;; esac
}

is_settled() {
  case "$1" in
    CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE|IMPORT_ROLLBACK_COMPLETE) return 0 ;;
    *) return 1 ;;
  esac
}

# --check: compare the token file with the live digest.
if [ $check = 1 ]; then
  if [ -L "$token_file" ] || { [ -e "$token_file" ] && [ ! -f "$token_file" ]; }; then
    say "Can't tell: $token_file isn't a regular file (a link or a directory), so it isn't a token file this script wrote. Look at it, then remove it."
    exit 2
  fi
  if [ ! -f "$token_file" ]; then
    say "No token file at $token_file: nothing to check."
    exit 1
  fi
  mine=$(file_digest)
  if ! [[ "$mine" =~ ^[0-9a-f]{64}$ ]]; then
    say "Can't tell: couldn't read $token_file."
    exit 2
  fi
  read_status
  if [ "$status" = unreadable ]; then
    say "Can't tell: couldn't read stack $stack. AWS said:"
    say "$status_err"
    exit 2
  fi
  if is_gone "$status"; then
    say "NOT LIVE: stack $stack has no working store (${status}). The token in $token_file is no use; delete the file."
    exit 1
  fi
  if [[ "$status" == *_IN_PROGRESS ]]; then
    say "Can't tell yet: stack $stack is $status. Run $me --check again once it finishes."
    exit 2
  fi
  live=$(live_digest)
  if [ -z "$live" ]; then
    say "Can't tell: couldn't read the store's token digest from its function."
    exit 2
  fi
  if ! is_settled "$status"; then
    say "Note: stack $stack is $status and needs repair in the CloudFormation console; the answer below is for its function as it is now."
  fi
  if [ "$mine" = "$live" ]; then
    outputs
    say "LIVE: the token in $token_file is the store's current token. Save it (README, step 4)."
    exit 0
  fi
  say "NOT LIVE: the store uses a different token. The token in $token_file is no use; delete the file."
  exit 1
fi

if [ ! -f template.yaml ]; then
  say "Stopped: no template.yaml in $(pwd). Download it from the release first (README, step 3)."
  exit 3
fi

# 1. What's there now. Anything other than "no stack" or a settled state
#    stops here: an error must never read as "no stack".
read_status
if [ "$status" = unreadable ]; then
  say "Stopped: couldn't read the state of stack $stack, so nothing was changed. AWS said:"
  say "$status_err"
  exit 3
elif [ "$status" = none ] || [ "$status" = REVIEW_IN_PROGRESS ]; then
  # REVIEW_IN_PROGRESS: a change set that never ran (a failed or
  # interrupted first deploy); the CLI deploys over it as if there were
  # no stack.
  if [ $rotate = 1 ]; then
    say "Stopped: there's no store to rotate (stack $stack: ${status}). To deploy one, run $me without --rotate."
    exit 3
  fi
  mode=create
elif is_settled "$status"; then
  if [ $rotate = 1 ]; then
    mode=rotate
  else
    say "Stopped: stack $stack already exists ($status). Deploying again would rotate its token, and everything using the current token would start getting 401."
    say "To rotate on purpose: $me --rotate"
    exit 3
  fi
else
  case "$status" in
    ROLLBACK_COMPLETE)
      say "Stopped: stack $stack is a failed first deploy (ROLLBACK_COMPLETE). Delete it, then run $me again:"
      say "  aws cloudformation delete-stack --stack-name $stack && aws cloudformation wait stack-delete-complete --stack-name $stack"
      exit 3 ;;
    DELETE_FAILED)
      say "Stopped: stack $stack is DELETE_FAILED, usually a deleted store whose bucket still held blobs. See README, Caveats, on deleting the stack."
      exit 3 ;;
    *_IN_PROGRESS)
      say "Stopped: stack $stack is busy ($status). Wait for it to finish, then run $me again."
      exit 3 ;;
    *)
      say "Stopped: stack $stack is in state $status, which this script doesn't handle. Nothing was changed."
      exit 3 ;;
  esac
fi

# 2. Never overwrite a token file: it may hold a token not yet saved.
if [ -L "$token_file" ] || { [ -e "$token_file" ] && [ ! -f "$token_file" ]; }; then
  say "Stopped: $token_file isn't a regular file (a link or a directory), so it isn't a token file this script wrote. Look at it, remove it, then run this again."
  exit 3
fi
if [ -e "$token_file" ]; then
  say "Stopped: $token_file already exists. It may hold a token you haven't saved yet."
  say "To find out whether it's live: $me --check. Save it if it is; then delete the file and run this again."
  exit 3
fi

# 3. The token and its digest, generated here; the token goes to the file
#    before the deploy starts, so it's in the file however the deploy ends.
read -r token digest < <(python3 -c 'import secrets,hashlib;t=secrets.token_hex(32);print(t,hashlib.sha256(t.encode()).hexdigest())')
if ! [[ "${token:-}" =~ ^[0-9a-f]{64}$ && "${digest:-}" =~ ^[0-9a-f]{64}$ ]]; then
  say "Stopped: couldn't generate a token (is python3 available?). Nothing was changed."
  exit 3
fi
(set -C && umask 077 && printf '%s' "$token" > "$token_file") || {
  say "Stopped: couldn't write $token_file. Nothing was changed."
  exit 3
}
unset token

# 4. Deploy. Ctrl-C stops the CLI's wait, not the stack update, so it's
#    handled like any other failure: by asking the store what happened.
trap 'say ""; say "Interrupted: checking the stack before deciding about the token file."' INT
params=("BearerTokenSha256=$digest")
[ -n "$suffix" ] && params+=("NameSuffix=$suffix")
aws cloudformation deploy --template-file template.yaml --stack-name "$stack" \
  --capabilities CAPABILITY_IAM --parameter-overrides "${params[@]}"
deploy_rc=$?

deployed() {
  outputs
  if [ "$mode" = rotate ]; then
    say "Rotated. The new token is in $token_file: take it out with Actions > Download file (README, step 4). The previous token no longer works: update everything that uses it (SETUP.md, Rotation)."
  else
    say "Deployed. The token is in $token_file: take it out with Actions > Download file (README, step 4)."
  fi
  exit 0
}

if [ $deploy_rc -eq 0 ]; then
  trap - INT
  deployed
fi

# 5. The deploy failed or was interrupted. Keep or remove the token file
#    only on what the store says, never on the exit code alone.
unconfirmed() {
  say "The deploy's outcome isn't confirmed yet (${1}). The token file $token_file is KEPT: it may be the live token."
  say "Once the stack finishes changing, run: $me --check"
  say "It says whether the token in the file is live (save it) or not (delete the file; any previous token still works)."
  exit 2
}

# One reading, into $result: "gone", "new" (the store runs the new token),
# "old" (it doesn't), or "unknown" (with the reason in $why).
reading() {
  local live
  read_status
  why="stack status: $status"
  if [ "$status" = unreadable ]; then result=unknown; return; fi
  if is_gone "$status"; then result=gone; return; fi
  if [[ "$status" == *_IN_PROGRESS ]]; then result=unknown; return; fi
  live=$(live_digest)
  if [ -z "$live" ]; then why="$why, and the store's token digest couldn't be read"; result=unknown; return; fi
  if [ "$live" = "$digest" ]; then result=new; else result=old; fi
}

reading
first=$result
if [ "$first" = new ]; then
  trap - INT
  say "The deploy command reported an error, but the store now runs the new token."
  deployed
fi
[ "$first" = unknown ] && unconfirmed "$why"

# "Nothing changed" is about to delete the file: read again after a pause,
# in case an update the CLI started hasn't shown yet.
say "Checking again in ${settle}s before removing the token file..."
sleep "$settle"
trap - INT
reading
second=$result
if [ "$second" = new ]; then
  say "The deploy command reported an error, but the store now runs the new token."
  deployed
fi
[ "$second" = "$first" ] || unconfirmed "$why (it changed while the script waited)"

rm -f "$token_file"
if [ "$mode" = rotate ] && [ "$second" = old ]; then
  say "Rotation failed (see above): the store still runs the previous token, which still works. The new token file was removed."
elif [ "$mode" = rotate ]; then
  say "Rotation failed (see above), and stack $stack now has no working store. The new token file was removed."
elif [ "$second" = gone ]; then
  say "Deploy failed (see above): no store was created, and the token file was removed."
else
  say "Deploy failed (see above): the store doesn't run the new token, and the token file was removed."
fi
exit 1
