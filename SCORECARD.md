# STORE-TEST scorecard

Two deploy routes, each STORE-TESTed against a live stack it created:
the CloudFormation template (the current route, both ways of running
the one-paste) and the earlier Terraform deployment. Every run used a
scratch stack and dummy data in the maintainer's own AWS account
(us-east-2), and was cleaned up afterwards. The newest run is first.

## CloudFormation template, pre-release, 2026-11-21

**Verdict: pass.** Two runs by an assistant (Muse) in the standalone
CloudShell console, each on its own scratch stack
(`bash deploy.sh --suffix sttest`), with dummy data only; the
production store was untouched and health-checked afterwards (200
`ok`). The first run found one defect, fixed in
[#28](https://github.com/cmeans/s3-blob-store/pull/28); the second,
at the fixed commit, passed every row of `SKILL.md`'s STORE-TEST
table but one it allows to be skipped.

### Run 1: commit 749758d

`template.yaml` 12,964 bytes, SHA-256
`1413c302a7a59ffb454b1b14b041fa493f1abe5ad51604cd8ba2f3c1d58e5198`;
`deploy.sh` SHA-256
`9ea79f240d02d15ccdd7c1f5d30e9b839531115487770d480cd2256b713e1fc9`.
Both fetched from the repository at that commit and hash-checked.

Every endpoint and `deploy.sh` check passed, except one: a `PUT` of
the bytes `ff fe 00 61 62 63` with `Content-Type: text/plain`
returned 200 `stored`, and a GET returned
`ef bf bd ef bf bd 00 61 62 63`. The Function URL decodes a text
body before the handler sees it and replaces each invalid byte with
U+FFFD, so the handler's UTF-8 check never fired and the mangled
text was stored. #28 makes the handler refuse any body containing
U+FFFD. Not covered by this run: the lowercase `bearer` scheme, the
resource-policy condition and a direct invoke, which #28 then added
to the table.

### Run 2: commit 17251e9

`template.yaml` 12,857 bytes, SHA-256
`7cf5a3a1d75a8a78e81142ce777da44fdffb4e50345be0b8060bca0c843985f6`;
`deploy.sh` unchanged (SHA-256 as above). Both fetched at that commit
and hash-checked.

| Check | Result |
|---|---|
| Health, `Bearer` token | Pass: 200, body `ok` |
| Health, lowercase `bearer` | Pass: 200, body `ok` |
| Unauthenticated `GET` of a blob | Pass: 401 |
| Missing label | Pass: 404 |
| Round trips 400 / 2,700 / 8,000 / 64,000 characters | Pass: byte-identical, SHA-256 match |
| Replace | Pass: new content served |
| Delete, then `GET` | Pass: `deleted`, then 404 |
| Non-UTF-8 bytes `ff fe 00 61 62 63` as `text/plain` | Pass: 400 `body must be UTF-8 text (U+FFFD not allowed)`, then 404 |
| The same bytes as `application/octet-stream` | Pass: 400 `body must be UTF-8 text` (the base64 path's strict decode), then 404 |
| `café` (`63 61 66 c3 a9`) as `application/octet-stream` | Pass: stored, GET returned the same 5 bytes |
| Resource policy (`aws lambda get-policy`) | Pass: the `lambda:InvokeFunction` statement for principal `*` carries `"Condition":{"Bool":{"lambda:InvokedViaFunctionUrl":"true"}}`; the `lambda:InvokeFunctionUrl` statement carries `lambda:FunctionUrlAuthType` = `NONE` |
| Direct invoke from an identity with no Lambda permissions | Skipped, as the row allows: the only identity at hand was the account admin, whose own IAM permissions would allow the call whatever the resource policy says. No IAM principals were created for the test |
| `deploy.sh --check` with the token file present | Pass: `LIVE`, exit 0 |
| The one-paste again, without `--rotate` | Pass: `already exists (CREATE_COMPLETE)`, exit 3, nothing changed |
| Rotation | Pass: `Rotated.`; old token 401, new token 200; new token file mode 600 |
| `--suffix` of 22 characters | Pass: refused before any AWS call, exit 3 |

The token was read from its mode-600 file into a shell variable and
never printed. The rotation row's "token file saved and deleted
first" matters: with the old file still in place, `--rotate` stops
with exit 3 (`already exists. It may hold a token you haven't saved
yet.`) rather than overwrite it.

### Cleanup, both runs

Each scratch stack was deleted and confirmed gone, and its Function
URL then answered with an AWS-level 403 (`{"Message":null}`), not the
handler. In run 2 the first `delete-stack` ended in `DELETE_FAILED`,
because test blobs were still in the bucket and S3 won't delete a
non-empty bucket (the behavior `README.md` and `SKILL.md`
describe); emptying the scratch bucket and deleting the stack again
succeeded. Delete the test labels before the stack, as the
STORE-TEST section says, to avoid it. Token files and the downloaded
scripts were removed from CloudShell.

## CloudFormation template, 2026-11-20

**Verdict: pass, both ways.** The template was deployed with the
CloudShell one-paste from a GitHub release asset, twice: once with a
human running the paste, and once with an assistant (Muse) doing the
whole deployment from a login the account owner supplied. Each stack
passed the same STORE-TEST the Terraform deployment passed.

**What was deployed:** `template.yaml`, 11,627 bytes, SHA-256
`9b7aa1a4a130d12dec62e6b45f51b98129b188300a538c1aef61c5f84fb9bcd8`:
the same file as in this repo. It was downloaded from a release
asset and hash-checked before the deploy. Each run used its own
stack, its own `NameSuffix` and a throwaway token, so neither touched
any other store.

### STORE-TEST (both runs)

| Check | Result |
|---|---|
| Health, authorized `GET /` | Pass: 200, body `ok` |
| Unauthenticated `GET` of a blob | Pass: 401 |
| Missing label | Pass: 404 |
| Round trip 400 B | Pass: byte-identical, SHA-256 match |
| Round trip 2,700 B | Pass: byte-identical, SHA-256 match |
| Round trip 8,000 B | Pass: byte-identical, SHA-256 match |
| Round trip 64,000 B | Pass: byte-identical, SHA-256 match |
| Replace | Pass: new content served |
| Delete, then `GET` | Pass: `deleted`, then 404 |

The README's endpoint sequence, as it stood then, also passed in
CloudShell each time: 401, `stored`, `dummy`, `deleted`, 404. (It
has since gained a 404 check on a fresh label before anything is
written, so a check can't overwrite a real blob.) Test labels were
deleted afterwards. Each run deployed under its own stack name and
`NameSuffix` (`SKILL.md`'s "Isolation by name" variant), not the
default `s3-blob-store` stack.

### Human-run paste

- **Token download from `/tmp`:** works in the standalone CloudShell
  console (Actions, then *Download file*). The CloudShell panel
  embedded in the console toolbar refused downloads in Firefox ("The
  download option is not supported in the Console Toolbar in this web
  browser"), so the README sends people to the standalone console for
  this step.
- **Not tested:** clearing the token from scrollback
  (`clear && tmux clear-history`) as a fallback to the download. The
  docs don't rely on it.

### Assistant-driven deployment

- **Sign-in:** the account owner saved the account's login through
  the assistant's secure credential flow and supplied one MFA code at
  the prompt. Those were the only human steps.
- **Deploy:** the assistant opened the standalone CloudShell and ran
  the same one-paste. The deploy took about 100 seconds.
- **Token:** downloaded through *Download file*, never displayed, used
  for STORE-TEST, then deleted on both sides.
- **STORE-TEST:** 9 of 9. The first attempt hit one transient network
  read timeout; the retry passed, and it wasn't a stack fault.
- **IAM:** in the same session the assistant created an IAM user,
  attached and detached a policy, and deleted the user. So an
  assistant-driven deployment can create a dedicated non-root user as
  its first step after a root sign-in.

Friction, kept for the assistant playbook (`SKILL.md`):

- CloudShell shows a "Safe Paste for multiline text" warning on a
  multi-line paste. Single-line pastes don't trigger it, which is one
  reason the deploy is one line.
- The terminal area has to be clicked before a paste lands.
- A pasted block whose last line has no trailing newline leaves that
  line unexecuted at the prompt, and the next paste appends to it.
  End pasted blocks with a newline and check for a fresh prompt.
- The handler's replies (`stored`, `dummy`, `deleted`) have no
  trailing newline, so the shell prompt runs into them. The README's
  checks add one with `-w "\n"`.

### Cleanup, both runs

Each stack was deleted afterwards. Its Function URL then answered with
an AWS-level 403 (`{"Message":null}`), not the handler, so the
function was gone.

## Terraform deployment, 2026-11-20

The first live deployment, made with what is now the Terraform route
in `alternatives/terraform/`, following the STORE-TEST protocol from
the `encrypted-credential-handoff` tool in muse-field-kit: dummy
age-encrypted blobs only, scratch label `store-test`, every blob
verified after fetch with the handoff check tool, all test material
deleted afterwards.

| Check | Score | Evidence in one line |
| --- | --- | --- |
| Private | Pass | Bucket blocks all public access with ACLs disabled; unauthenticated requests get 401 before S3; only the scoped Lambda role can read objects |
| Unattended fetch | Pass | Fetched in a fresh agent session straight to a file and checked there; blob never passed through the agent's context; one approval prompt on the very first call, none after |
| Write, replace, delete | Pass | All three done through the agent's connection; replace confirmed by SHA-256 change |
| Round trip (largest passing size: 64,000) | Pass | 400 / 2,700 / 8,000 / 64,000 characters all byte-identical and check-verified; a missing label returns a distinct 404 |
| History after delete | Partial | Versioning off and post-delete fetch is 404 with nothing recoverable through the connection, but AWS-internal backup retention is unobservable from outside |
| Store credential scope | Pass | Bearer token reaches only this one Lambda host; no token or key file sits on the agent's machine; the Lambda role is limited to Get/Put/Delete on one bucket prefix |
| Limits and cost | Pass | Single blob bounded by the 6 MB Lambda payload limit; S3 + Lambda at this scale costs effectively nothing per month |

Cleaned up: yes

Verdict: **fits with caveats:** AWS-internal backup retention is
unobservable, and the Lambda is publicly invocable with
authentication enforced in the handler (the design assumption,
verified live).

### Findings worth knowing before you adopt

- A NONE-auth Lambda Function URL needs **two** resource-policy
  permissions, `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`,
  or AWS returns 403 for every request. Terraform's
  `aws_lambda_permission` cannot attach the `FunctionUrlAuthType`
  condition to the second one, so that grant is unconditional at the
  AWS layer and the handler's token check is the real gate. (The
  CloudFormation template now scopes it with `InvokedViaFunctionUrl`,
  [#11](https://github.com/cmeans/s3-blob-store/issues/11); the
  AWS provider version the Terraform route pins can't.)
- S3 answers a `GetObject` for a missing key with **AccessDenied**,
  not `NoSuchKey`, when the caller lacks `s3:ListBucket`. The handler
  maps that to 404 rather than granting list access the store does
  not need.
- Connection checkers probe the root path, so the handler answers
  `GET /` with 200 for authorized callers.
