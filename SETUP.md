# SETUP — wiring a deployed s3-blob-store into daily use

Deploying (see `SKILL.md`) leaves you with exactly two things:

- a **FunctionUrl**, e.g.
  `https://<random>.lambda-url.<region>.on.aws/` — note the
  trailing slash; blobs live at `<FunctionUrl>blobs/<label>`
- a **bearer token**, 64 hex characters, which should now be in
  a password manager or your assistant platform's secure
  credential store — and nowhere else.

This file covers registering those two with your assistant,
the exact API contract, and day-to-day operation.

## Register the store with your assistant

The assistant needs the URL and the token, and it must hold
the token the way it holds any credential: in its secure
store, swapped into requests at send time, never in chat,
notes, memory, or plaintext config.

- **Muse:** register a custom connection for the store —
  provider name of your choosing (e.g. `s3-blob-store`), API
  host = the FunctionUrl host, authentication = bearer token
  in the `Authorization` header. **You** enter the token on
  the secure entry page, pasted from your password manager; it
  is stored as a credential the platform swaps in at egress to
  that one host, so the token never sits in the assistant's
  context or working files. (If the assistant deployed the
  store and downloaded the token file itself, see `SKILL.md`
  Path B step 4: it hands you the token, it doesn't read it.) The first real call (the
  health check below) is what verifies the registration.
- **Other assistants:** the equivalent — a secret-store entry
  or environment-scoped credential bound to that host, used
  as `Authorization: Bearer <token>`. If your assistant has
  no way to hold a token without putting it in plaintext
  config or chat, stop and use the store manually with curl
  (below) until it does. A credential store whose key is
  pasted around in plaintext has defeated its own purpose.

Keep the token in the password manager as the human-side
backup copy regardless of which assistant registration you
use. If you run more than one deployment (say a scratch stack
beside the real one), register them as separate connections
with distinct names — tokens are per-stack and not
interchangeable.

## Verify the wiring

Health check, with `URL` set to the FunctionUrl and `TOKEN` set
without typing it (see Manual use, below):

```
curl -s -H "Authorization: Bearer $TOKEN" "$URL"
```

Expected: `ok` (HTTP 200). Without the header, the same
request returns HTTP 401 `unauthorized` — check that too;
it proves the auth gate, not just the function, is alive.

## API contract

All routes are on the FunctionUrl, and every request needs
the bearer token: without it (or with a wrong one), any path
and any method gets 401 before anything else is checked.

| Route | Method | Success | Notes |
|---|---|---|---|
| `/` | GET | 200, body `ok` | health |
| `/blobs/<label>` | PUT | 200, body `stored` | request body is the blob (UTF-8 text, see Bodies); replaces any existing blob at the label |
| `/blobs/<label>` | GET | 200, the blob | 404 when the label has no blob |
| `/blobs/<label>` | DELETE | 200, body `deleted` | answers `deleted` whether or not the label existed, so it proves nothing about what was there; GET after delete returns 404 |

Errors: 401 `unauthorized` (missing or wrong token), 404
`not found` (no blob at the label, or no such route), 405
`method not allowed`, 429 (from Lambda, not the handler: the
store's concurrency cap, when one is set, is full; nothing was
changed, retry later; see Capping concurrent calls), 400 `invalid label` (label fails
validation), 400 `body must be UTF-8 text` (a body the Function
URL hands over base64-encoded that isn't valid base64 or UTF-8,
or a text body that can't be encoded as UTF-8), 400
`body must be UTF-8 text (U+FFFD not allowed)` (a body containing
U+FFFD, the replacement character). The U+FFFD rule is there
because a non-UTF-8 body sent as text (for example
`text/plain`) reaches the handler with each invalid byte already
replaced by U+FFFD (STORE-TEST, 2026-11-21): refusing it keeps
mangled text from being stored. A blob can't contain U+FFFD for
the same reason; ASCII-armored ciphertext never does.

**Labels:** letters, digits, dot, dash, underscore, starting
with a letter or digit; at most 128 characters. Colons and slashes are not allowed — if your
naming scheme uses them (e.g. `agent:purpose:key`), map them
before storing (`.` for `:` is the convention the reference
deployment uses) and keep the mapping in one place.

**Bodies:** UTF-8 text without U+FFFD (see Errors above), stored
and returned as is. That suits
ASCII-armored ciphertext (for example `age -a` output); encode
binary as base64 before storing it. The store is designed for
credential-sized payloads;
round trips are test-verified to 64,000 characters (see
`SCORECARD.md`). It is not a file store — keep blobs small.

**Semantics worth knowing:**

- PUT replaces unconditionally; there is no versioning
  (the bucket has versioning off) and no history. If you
  need the old value, fetch it before you replace it.
- DELETE removes the blob from the API immediately. AWS-side
  internal retention after a delete is not observable from
  outside; do not treat delete as a forensic purge.
- There is deliberately **no list operation**. The front
  door cannot enumerate the bucket, so a caller must already
  know a label to read it. Keep your own index of labels
  somewhere else (the reference deployment keeps a registry
  file beside its tooling).

## Manual use (curl)

Set `URL` to the FunctionUrl and `TOKEN` without typing the
token on the command line, for example `read -rs TOKEN` and a
paste from your password manager (a typed token stays in your
shell history). `-f` makes curl fail, and write nothing, on a
401 or 404, so an error reply can't be mistaken for a blob.

```
# store (from a file, so the content never appears in shell history as an argument)
curl -sf -X PUT -H "Authorization: Bearer $TOKEN" --data-binary @blob.txt "$URL"blobs/<label>

# fetch (to a file; the same reasoning)
curl -sf -H "Authorization: Bearer $TOKEN" "$URL"blobs/<label> -o blob.txt

# delete
curl -sf -X DELETE -H "Authorization: Bearer $TOKEN" "$URL"blobs/<label>
```

## Rotation

In CloudShell, paste the deploy line from README step 3 again,
from the current release, ending in `bash deploy.sh --rotate`
instead of `bash deploy.sh` (add `--suffix <x>` for a suffixed
store; SKILL.md, Isolation by name). Without `--rotate` the
script stops rather than rotate. Download from the current release
each time: rotating with an older `template.yaml` would also roll the
stack back to it. It generates a fresh token and digest and updates
the stack in place; the FunctionUrl does not change. Then:

1. Put the new token in the password manager.
2. Update the assistant's registration with the new token
   (replace the stored credential).
3. Verify the health check with the new token, and confirm
   the old token now gets 401.
4. Delete the token file copy as usual.

Rotate immediately if a token may have leaked. A leaked
token gives full read/write/delete over that one bucket —
it reaches nothing else in the account — and rotation is
the complete remedy.

## Capping concurrent calls

The Function URL is public, and every call that reaches the
handler is billed, the ones it refuses with `401` too. A reserved
concurrency on the function caps how many copies of it run at
once, and so how fast anyone calling it can run up a bill (not
the total: a caller who keeps going keeps costing, at the capped
rate). Calls past the cap get HTTP `429` from Lambda, and the
store is unchanged. They aren't billed: AWS counts a throttled
request in neither `Invocations` nor `Errors`, and the
`Invocations` count is the number of requests billed. AWS also
throttles a Function URL at 10 requests per second for each unit
of reserved concurrency. An assistant's fetches are
occasional, so a small cap (5, say) doesn't get in their way
normally; but while someone floods the URL, the assistant's own
calls get `429` too, until the flood stops. The cap trades a
bigger bill for that outage. Reserving concurrency costs nothing
in itself.

It's off by default (`ReservedConcurrency` 0), and it needs a
release whose `template.yaml` has the `ReservedConcurrency`
parameter: any release after v0.1.0. With an older template the
AWS CLI drops the setting without an error, so the line below
checks for it first. To set the cap, or change it, in CloudShell,
without touching the token (for a suffixed store, set
`S=s3-blob-store-<x>`):

```
S=s3-blob-store
curl -fsSL -O https://github.com/cmeans/s3-blob-store/releases/download/<release>/template.yaml && if grep -q '^  ReservedConcurrency:' template.yaml; then aws cloudformation deploy --template-file template.yaml --stack-name "$S" --capabilities CAPABILITY_IAM --parameter-overrides ReservedConcurrency=5; else echo "Stopped: this release's template.yaml has no ReservedConcurrency parameter; use a later release. Nothing was changed."; fi
```

Use the current release, as for a rotation: an older
`template.yaml` would roll the stack back to it. Parameters the
command doesn't name, the token's digest and the suffix included,
keep their current values (the AWS CLI's `deploy` says so for an
update), so the token and the FunctionUrl don't change, and a
later rotation keeps the cap. `ReservedConcurrency=0` removes the
cap again. Here 0 means "no cap", not Lambda's own reserved
concurrency of 0, which stops every call. Whatever the command
prints, `No changes to deploy` included, confirm the result with
`get-function-concurrency` below. (Setting a cap and removing it
again are in SKILL.md's STORE-TEST, and haven't been seen live
yet: the run after #39 was refused, below. AWS doesn't document
what CloudFormation does when the property goes away; its
resource schema lets an update remove a reservation, so `0` is
expected to clear it.)

AWS keeps part of every account's concurrency unreserved, and
refuses a cap that would cut into it. Its documentation says 100
must stay unreserved, and it doesn't say what happens on an
account whose whole limit is below that. New accounts start with
a reduced concurrency quota, which AWS raises automatically with
use. The STORE-TEST after #39 ran on an account whose limit was
10, and there the floor was the whole limit, so no cap could be
set at all: "Specified ReservedConcurrentExecutions for function
decreases account's UnreservedConcurrentExecution below its
minimum value of [10]" (SCORECARD.md). AWS names the floor in
that error. The deploy failed, CloudFormation rolled the update
back, and the store kept working as before, with the same token. Leave
the cap at 0, or request a higher quota in Service Quotas (AWS
Lambda, "Concurrent executions", quota code `L-B99A9384`, per
region) and try again. To see your account's limit
(`ConcurrentExecutions`) and the cap (with `S` set as above):

```
aws lambda get-account-settings --query AccountLimit
F=$(aws cloudformation describe-stack-resource --stack-name "$S" --logical-resource-id BlobFunction --query StackResourceDetail.PhysicalResourceId --output text)
aws lambda get-function-concurrency --function-name "$F"
```

A cap shows as `"ReservedConcurrentExecutions": 5`; no cap, as a
reply without that field.

The Terraform route takes the same setting as its
`reserved_concurrency` variable (`alternatives/terraform/README.md`).

## Security summary

- The token's SHA-256 digest is the only form of it in AWS
  (a NoEcho stack parameter). AWS never holds the token.
- The Lambda's IAM role is the only identity the stack grants
  access to the bucket, scoped to that bucket. The account's
  administrators (and root) can still reach it through S3
  itself, so protect those logins too. The bucket blocks all
  public access and denies insecure transport.
- The Function URL itself is public (it has to be, for your
  assistant to reach it from anywhere) — the token check is
  the gate, which is why token handling in `SKILL.md` is
  strict: never echoed, never chatted, file-to-password-
  manager with the file deleted.
- Losing the token without a password-manager copy means
  rotating (above) — there is no recovery path, by design.
