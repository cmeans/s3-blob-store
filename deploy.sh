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
# live token; 1 failed, the new token isn't live, its file was removed; 2
# the outcome isn't known yet, the token file is kept (run --check later);
# 3 stopped before deploying, nothing changed (see the message).
# Exit status of --check: 0 live; 1 not live (or no file and no store); 2
# can't tell (yet), including no token file while a store runs, since a lost
# file may have held the live token.

set -u
set -o pipefail

say() { printf '%s\n' "$*"; }

# The stderr file of the AWS call in progress, if any: removed however the
# script ends (a Ctrl-C mid-call included).
errf=
trap 'rm -f "${errf:-}"' EXIT

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
# How long to wait before trusting a "not live" answer after a
# failed deploy (an update the CLI started can take a moment to show).
# The variable exists for the tests; anything but whole seconds is ignored.
settle=${DEPLOY_SH_SETTLE_SECONDS:-20}
[[ "$settle" =~ ^[0-9]+$ ]] || settle=20

# Reads the stack's status into $status: a CloudFormation status, "none"
# (no such stack), or "unreadable" (with AWS's message in $status_err).
# stdout and stderr are kept apart, so a CLI warning can't pass for a
# status, and "does not exist" is only believed from a failed call.
read_status() {
  local out rc
  errf=$(mktemp) || { status=unreadable; status_err="couldn't create a temporary file"; return; }
  out=$(aws cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].StackStatus" --output text 2>"$errf")
  rc=$?
  status_err=$(cat "$errf")
  rm -f "$errf"; errf=
  if [ $rc -eq 0 ] && [[ "$out" =~ ^[A-Z_]+$ ]]; then
    status=$out
  elif [ $rc -ne 0 ] && [[ "$status_err" == *"does not exist"* ]]; then
    status=none
  else
    status=unreadable
    [ -n "$status_err" ] || status_err="unexpected reply: $out"
  fi
}

# Reads the digest the store's function holds (the stack parameter is
# NoEcho, so the function's configuration is the only place to read it)
# into $live, and how that went into $fn_state: "ok", "missing" (AWS says
# the function doesn't exist) or "error" (with AWS's message in $fn_err).
read_function() {
  local fn rc
  live=
  errf=$(mktemp) || { fn_state=error; fn_err="couldn't create a temporary file"; return; }
  fn=$(aws cloudformation describe-stack-resource --stack-name "$stack" \
    --logical-resource-id BlobFunction \
    --query "StackResourceDetail.PhysicalResourceId" --output text 2>"$errf")
  rc=$?
  if [ $rc -ne 0 ] || [ -z "$fn" ] || [ "$fn" = None ]; then
    fn_err=$(cat "$errf"); rm -f "$errf"; errf=
    if [[ "$fn_err" == *"does not exist"* ]]; then fn_state=missing; else fn_state=error; fi
    return
  fi
  live=$(aws lambda get-function-configuration --function-name "$fn" \
    --query "Environment.Variables.BEARER_TOKEN_SHA256" --output text 2>"$errf")
  rc=$?
  fn_err=$(cat "$errf"); rm -f "$errf"; errf=
  if [ $rc -ne 0 ]; then
    live=
    if [[ "$fn_err" == *ResourceNotFoundException* ]]; then fn_state=missing; else fn_state=error; fi
  elif [[ "$live" =~ ^[0-9a-f]{64}$ ]]; then
    fn_state=ok
  else
    fn_state=error
    fn_err="the function has no well-formed BEARER_TOKEN_SHA256 (got: ${live:-nothing})"
    live=
  fi
}

# The token file's digest; prints nothing unless the path is a regular file
# (opened without following a link, and without blocking on a FIFO).
file_digest() {
  python3 -c '
import hashlib, os, stat, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
if not stat.S_ISREG(os.fstat(fd).st_mode):
    sys.exit(1)
print(hashlib.sha256(os.read(fd, 4096)).hexdigest())
' "$token_file" 2>/dev/null
}

# Stops (exit $1, message starting "$2") if something other than this
# user's own regular file is at the token path: a link, a directory, a FIFO,
# or another user's file. $3 ends the advice ("then run this again").
foreign_token_path() {
  local owner
  if [ -L "$token_file" ] || { [ -e "$token_file" ] && [ ! -f "$token_file" ]; }; then
    say "$2: $token_file isn't a regular file (a link, a directory or a FIFO), so it isn't a token file this script wrote. Look at it and remove it$3."
    exit "$1"
  fi
  if [ -e "$token_file" ] && [ ! -O "$token_file" ]; then
    owner=$(python3 -c 'import os,pwd,sys;u=os.lstat(sys.argv[1]).st_uid
try: print(pwd.getpwuid(u).pw_name)
except KeyError: print("uid %d" % u)' "$token_file" 2>/dev/null)
    say "$2: $token_file belongs to ${owner:-another user}, not to you. If you ran this script as that user (with sudo, for example), run it the same way: it may hold a live token, so don't delete it until --check says it isn't live. Otherwise use --suffix <x> for a token file of its own."
    exit "$1"
  fi
}

# How old the token file is, in whole minutes (empty if it can't tell).
file_age_minutes() {
  python3 -c 'import os,sys,time;print(int((time.time()-os.lstat(sys.argv[1]).st_mtime)//60))' "$token_file" 2>/dev/null
}

outputs() {
  aws cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Outputs" --output table
}

is_settled() {
  case "$1" in
    CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE|IMPORT_ROLLBACK_COMPLETE) return 0 ;;
    *) return 1 ;;
  esac
}

# The one place that decides what the store's state means for a token
# whose digest is $1. Sets $result:
#   gone     no working function, so no token is live
#   match    the function holds this digest: the token is live
#   other    the function holds another digest
#   busy     the stack is changing: ask again when it's done
#   unknown  AWS couldn't be read (the reason is in $why)
# and $repair=1 when the stack is in a *_FAILED state that needs repair.
# Order matters: REVIEW_IN_PROGRESS is gone, not busy, so it's tested first.
assess() {
  read_status
  why="stack status: $status"
  repair=0
  case "$status" in
    unreadable) why="couldn't read stack $stack; AWS said: $status_err"; result=unknown; return ;;
    none|REVIEW_IN_PROGRESS|ROLLBACK_COMPLETE|DELETE_COMPLETE) result=gone; return ;;
    *_IN_PROGRESS) result=busy; return ;;
  esac
  is_settled "$status" || repair=1
  read_function
  case "$fn_state" in
    missing) result=gone ;;
    error) why="$why; couldn't read the store's token digest from its function; AWS said: ${fn_err:-nothing}"; result=unknown ;;
    ok) if [ "$live" = "$1" ]; then result=match; else result=other; fi ;;
  esac
}

repair_note() {
  [ "$repair" = 1 ] && say "Note: stack $stack is $status and needs repair in the CloudFormation console before it can be deployed or rotated again."
  return 0
}

# --check: compare the token file with the live digest.
if [ $check = 1 ]; then
  foreign_token_path 2 "Can't tell" ""
  if [ ! -f "$token_file" ]; then
    # No file to compare. The same classification as below; with no
    # digest of our own, a running store can only answer "other".
    assess ""
    case "$result" in
      gone)
        say "No token file at $token_file, and stack $stack has no working store (${status}): no token is live, so there's nothing to do. (Deployed under another name, or in another region? Run this with the same --suffix, in that region's CloudShell.)"
        repair_note
        exit 1 ;;
      busy)
        say "No token file at $token_file, and stack $stack is $status. Run $me --check again once it has finished changing."
        exit 2 ;;
      unknown)
        say "No token file at $token_file, and the store couldn't be read: $why"
        exit 2 ;;
    esac
    # A store runs some token. Whether anyone holds it can't be told from
    # here: only the token the clients use can say.
    outputs
    say "No token file at $token_file. If you saved its token and then deleted the file, there's nothing to do."
    say "If you don't know whether the store runs a token you hold (say a deploy or rotation ended unconfirmed and the file was lost since: in CloudShell, /tmp is cleared when the session ends), test the token your clients use, from your password manager or the registered connection, never from a file, with the FunctionUrl above (SETUP.md, Verify the wiring):"
    # shellcheck disable=SC2016  # printed for the user to run: $TOKEN and $URL stay literal
    say '  curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL"'
    if [ "$repair" = 1 ]; then
      say "200: nothing to do. Anything else: the stack needs repair first (see the note below); don't rotate until it's repaired."
      repair_note
    else
      say "200: nothing to do. 401: the store runs a token nobody holds; rotate with $me --rotate. Anything else, or no token to test with: stop and find out first; never rotate on a guess, since it ends the token your clients use."
    fi
    exit 2
  fi
  mine=$(file_digest)
  if ! [[ "$mine" =~ ^[0-9a-f]{64}$ ]]; then
    say "Can't tell: couldn't read $token_file."
    exit 2
  fi
  assess "$mine"
  # A deploy still running elsewhere (another tab) shows its token as not
  # live until it applies: a file this young gets "can't tell yet" instead
  # of "delete it". Ten minutes covers a deploy's ~100 s several times over.
  age=$(file_age_minutes)
  if { [ "$result" = gone ] || [ "$result" = other ]; } && [[ "$age" =~ ^[0-9]+$ ]] && [ "$age" -lt 10 ]; then
    say "Can't tell yet: the store doesn't run this token now (${status}), but $token_file is only ${age} minutes old. If a deploy or rotation is still running in another tab, let it finish; otherwise wait until the file is 10 minutes old, and run $me --check again."
    exit 2
  fi
  case "$result" in
    gone)
      say "NOT LIVE: stack $stack has no working store (${status}). The token in $token_file is no use; delete the file."
      repair_note
      exit 1 ;;
    match)
      outputs
      say "LIVE: the token in $token_file is the store's current token. Save it (README, step 4)."
      repair_note
      exit 0 ;;
    other)
      say "NOT LIVE: the store uses a different token. The token in $token_file is no use; delete the file."
      repair_note
      exit 1 ;;
    busy)
      say "Can't tell yet: stack $stack is $status. Run $me --check again once it has finished changing."
      exit 2 ;;
    *)
      say "Can't tell: $why"
      exit 2 ;;
  esac
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
foreign_token_path 3 "Stopped" ", then run this again"
if [ -e "$token_file" ]; then
  say "Stopped: $token_file already exists. It may hold a token you haven't saved yet."
  say "To find out whether it's live: $me --check. Save it if it is; then delete the file and run this again."
  exit 3
fi

# 3. The token, generated and written to the file by one Python process,
#    before the deploy starts, so it's in the file however the deploy ends.
#    The file is created exclusively (O_EXCL, no links followed, mode 600):
#    anything at the path by now, put there by anyone, makes it fail. Only
#    the digest comes back; the token never passes through this shell.
#    Every byte is written and synced, or the file is removed again: a
#    short write (a full disk, a file-size limit) never leaves a token that
#    differs from the digest that gets deployed.
errf=$(mktemp) || { say "Stopped: couldn't create a temporary file. Nothing was changed."; exit 3; }
digest=$(python3 -c '
import hashlib, os, secrets, sys
path = sys.argv[1]
t = secrets.token_hex(32).encode()
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    done = 0
    while done < len(t):
        done += os.write(fd, t[done:])
    os.fsync(fd)
except BaseException:
    os.close(fd)
    os.unlink(path)
    raise
os.close(fd)
print(hashlib.sha256(t).hexdigest())
' "$token_file" 2>"$errf")
why=$(tail -n 1 "$errf"); rm -f "$errf"; errf=
if ! [[ "$digest" =~ ^[0-9a-f]{64}$ ]]; then
  say "Stopped: couldn't create $token_file or generate a token (is python3 available?). Nothing was changed. Python said: ${why:-nothing}"
  exit 3
fi

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
  repair_note
  exit 0
}

if [ $deploy_rc -eq 0 ]; then
  trap - INT
  repair=0
  deployed
fi

# 5. The deploy failed or was interrupted. Keep or remove the token file
#    only on what the store says, never on the exit code alone.
unconfirmed() {
  say "The deploy's outcome isn't confirmed yet ($1). The token file $token_file is KEPT: it may be the live token."
  say "Once the stack has finished changing and AWS can be read, run $me --check in this same shell session (the file is in /tmp; in CloudShell, /tmp is cleared when the session ends)."
  say "It says whether the token in the file is live (save it) or not (delete the file; any previous token still works)."
  exit 2
}

assess "$digest"
first=$result
case "$first" in
  match) trap - INT; say "The deploy command reported an error, but the store now runs the new token."; deployed ;;
  busy|unknown) unconfirmed "$why" ;;
esac

# "Not live" is about to delete the file: read again after a pause, in case
# an update the CLI started hasn't shown yet. A Ctrl-C now keeps the file.
trap 'say ""; unconfirmed "interrupted before the second check finished"' INT
say "Checking again in ${settle}s before removing the token file..."
sleep "$settle"
assess "$digest"
second=$result
if [ "$second" = match ]; then
  trap - INT
  say "The deploy command reported an error, but the store now runs the new token."
  deployed
fi
[ "$second" = unknown ] && unconfirmed "$why"
[ "$second" = "$first" ] || unconfirmed "$why (it changed while the script waited)"

trap '' INT
rm -f "$token_file"
if [ "$mode" = rotate ] && [ "$second" = other ]; then
  say "Rotation failed (see above): the store still runs the previous token, which still works. The new token file was removed."
elif [ "$mode" = rotate ]; then
  say "Rotation failed (see above), and stack $stack now has no working store (${status}). The new token file was removed."
elif [ "$second" = gone ]; then
  say "Deploy failed (see above): no store was created (${status}), and the token file was removed."
else
  say "Deploy failed (see above): the store doesn't run the new token, and the token file was removed."
fi
repair_note
exit 1
