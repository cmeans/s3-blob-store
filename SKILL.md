# SKILL — deploying and operating s3-blob-store (for AI assistants)

This file is the playbook for an AI assistant deploying or
operating an s3-blob-store on behalf of its user. Paths A and B
and the STORE-TEST were exercised live on 2026-11-20 (evidence in
`SCORECARD.md`), with the endpoint checks as they stood then;
Path C (signup), rotation and teardown of a store holding blobs
have not been run end to end. The human-facing story is in
`README.md`; wiring a finished deployment into daily use is in
`SETUP.md`.

**How you probably got here:** your user pasted you the prompt
from the README's Deploy section, which points at this file at a
pinned release. Use that same release tag wherever this file says
`<release>` (the tag in the URL you loaded this file from), so
the template you deploy matches the playbook you follow. Follow the path that matches their situation —
Path C if they have no AWS account, Path B if they have one and
want you to drive, Path A if they are running the paste
themselves and you are advising. If you can install this file as
a persistent skill on your platform, offer to: it turns
"deploy / rotate / check my blob store" into a standing
capability instead of a one-off.

The store in one paragraph: a private S3 bucket behind a Lambda
Function URL. The Lambda checks a bearer token (only its SHA-256
digest exists in AWS) and serves `GET /` (health) and
PUT/GET/DELETE `/blobs/<label>`. Nothing else the stack creates
can reach the bucket (the account's administrators still can,
through S3 itself). It is built to hold small encrypted blobs — credentials
an agent must not keep in its own context or memory.

## Ground rules (all paths)

- **Never handle the token in the open.** The plaintext token is
  generated in the deploy session, written to a file, moved to a
  password manager (the user's, or the assistant's secure store),
  and deleted. It is never echoed to a terminal (CloudShell
  session restore replays terminal output), never pasted into
  chat, never written into a repo, a note, or a config file in
  plaintext. The stack only ever receives the token's SHA-256
  digest, as a NoEcho parameter.
- **Verify, don't declare.** A deployment is not done when the
  stack says CREATE_COMPLETE. It is done when the endpoint
  sequence and the STORE-TEST below pass. Report failures with
  the actual error text; do not improvise around an auth or
  permission failure.
- **Isolation by name.** Every non-production deploy uses a
  distinct stack name and `NameSuffix`, which makes its bucket
  distinct. A test can then never touch another deployment's
  data, and cleanup is one stack deletion. In the one-paste,
  change both `--stack-name s3-blob-store` occurrences to
  `s3-blob-store-<x>` and add `NameSuffix=<x>` after the digest
  in `--parameter-overrides`. Use the same stack name for that
  store's pre-check, rotation and teardown too: run against the
  default name, they'd miss it (and a "rotation" would create a
  second, default store). `<x>`: 1 to 21 lowercase letters,
  digits and hyphens, not starting or ending with a hyphen
  (longer suffixes can overrun S3's 63-character bucket-name
  limit in long region names, so the template refuses them).
- **Restarts and destructive steps belong to the user** unless
  they have explicitly delegated them. Deleting a stack deletes
  its bucket's contents' only access path; confirm first unless
  the stack is a designated scratch stack.

## Path A — the user deploys (manual one-paste)

Give the user these steps; they take about five minutes.

1. AWS account (see Path C if they have none).
2. Open the **standalone CloudShell console** in the region the
   store should live in — not the embedded toolbar panel (in
   Firefox the toolbar panel refuses file downloads, which step
   4 needs). Bash session (the default).
   Before the paste, check that no store is there already:

   ```
   aws cloudformation describe-stacks --stack-name s3-blob-store --query "Stacks[0].StackStatus" --output text
   ```

   An error saying the stack does not exist means go ahead. Any
   status means a store (or a failed attempt) is already there:
   **stop and ask the user.** Re-running the paste over an
   existing store rotates its token, and every consumer of the
   old token starts getting 401. Rotate only when the user asked
   for a rotation (Operations, below). A `ROLLBACK_COMPLETE`
   status is a failed first deploy: with the user's OK, delete it
   (`aws cloudformation delete-stack --stack-name s3-blob-store`),
   wait with `aws cloudformation wait stack-delete-complete
   --stack-name s3-blob-store`, and re-run. A status ending in
   `_IN_PROGRESS` means something is still running: wait for it,
   then check again. `DELETE_FAILED` is a deleted store whose
   bucket still held blobs (Operations, Teardown): its bucket name
   is taken, so a new default stack would fail too. Ask the user
   whether the blobs matter, then see Teardown for the way out. If the account already has
   a store from the Terraform route in this region, use a
   `NameSuffix` (see Isolation by name): both routes derive the
   same bucket name.
3. Paste the one-liner (single line; `<release>` is the release
   being installed):

   ```
   curl -fsSLO https://github.com/cmeans/s3-blob-store/releases/download/<release>/template.yaml && read T D < <(python3 -c 'import secrets,hashlib;t=secrets.token_hex(32);print(t,hashlib.sha256(t.encode()).hexdigest())') && aws cloudformation deploy --template-file template.yaml --stack-name s3-blob-store --capabilities CAPABILITY_IAM --parameter-overrides BearerTokenSha256=$D && (umask 077; printf %s "$T" > /tmp/s3-blob-store-token.txt) && unset T D && aws cloudformation describe-stacks --stack-name s3-blob-store --query "Stacks[0].Outputs" --output table
   ```

   The deploy takes ~100 seconds of "Waiting for stack
   create/update to complete" — that is normal, not a stall.
   The user notes the `FunctionUrl` output.
4. Actions → Download file, path `/tmp/s3-blob-store-token.txt`.
   The user saves the token (64 hex characters) to their
   password manager. **Don't delete the file yet.**
5. Endpoint checks, in CloudShell, exactly as in README step 5:
   the token comes from the file (`TOKEN=$(cat
   /tmp/s3-blob-store-token.txt)`), never typed or pasted, since
   a typed token lands on screen and in the persistent shell
   history. Afterwards `unset TOKEN` and
   `rm /tmp/s3-blob-store-token.txt`. The checks run
   on a fresh label `store-check-<UTC timestamp>`, never a fixed
   one, expected in order `401` (unauthenticated GET), `404`
   (authorized GET: **stop if it isn't 404**, the label is in
   use), then `stored`, `dummy`, `deleted`, `404` for PUT, GET,
   DELETE, GET. The same rule holds for every STORE-TEST round
   trip: a label nothing else uses, confirmed free by a 404
   first, so a check can never replace or delete a real blob.

## Path B — the assistant deploys (user supplies a login)

For users who want the assistant to do everything. Tested
end-to-end 2026-11-20.

1. **Login, flexibly.** Ask the user to save their AWS login
   through your platform's secure credential flow (never in
   chat). Accept whatever sufficiently-privileged login they
   have — the root login is the common case; an existing admin
   IAM user works identically. Adapt the sign-in: root is
   email + password + MFA; IAM is account ID + username +
   password, plus MFA if enrolled. The user supplies MFA codes
   at prompts; never guess one, and ask promptly — codes are
   short-lived.
2. **Sign in and open the standalone CloudShell console** in the
   target region (same toolbar-panel caveat as Path A).
3. **Run the same pre-check and one-paste** as Path A step 3,
   and stop on an existing stack the same way. Driving a
   CloudShell terminal has its own rules, all learned live:
   - Prefer single-line pastes. A multi-line paste triggers
     CloudShell's "Safe Paste" warning; if you must paste
     multiple lines, verify the dialog's text matches your
     intended block exactly before confirming.
   - Click on the terminal area before pasting; a paste sent to
     an unfocused terminal silently goes nowhere.
   - End every pasted block with a trailing newline. A final
     line without one sits unexecuted at the prompt, and the
     next paste appends to it — producing a combined no-op line
     that looks like success. Verify at a fresh prompt after
     any paste that mutates state (e.g. re-check a file you
     deleted is actually gone).
   - Long waits (~100 s for the deploy) are normal.
4. **Token handling.** First hand the token to the user without
   reading it yourself: tell them to use Actions → Download file
   `/tmp/s3-blob-store-token.txt` (or, if you drive the browser,
   download it and give the user the file), save it to their
   password manager, and enter it on your platform's secure
   registration page (SETUP.md). If your platform can only
   register the token by reading the file, say so to the user
   first: the token then passes through your working files, and
   must be deleted from them once the registration is verified.
   Once the token is registered, delete any downloaded copy, but
   keep the CloudShell file until step 5 is done. Either
   way, the token is never shown in the terminal or the chat.
5. **Verify, then clean up**, in the same CloudShell session:
   set `TOKEN=$(cat /tmp/s3-blob-store-token.txt)`, run the
   endpoint sequence (Path A step 5) and the STORE-TEST below
   with it. When both pass, or as soon as you stop on a failure,
   run `unset TOKEN && rm /tmp/s3-blob-store-token.txt`. If the
   file is already gone (the CloudShell session ended), don't
   regenerate it or ask for the token: run the STORE-TEST through
   the store's registered connection instead. (Path A step 5 ends with that
   cleanup; in Path B it moves to the end, because the STORE-TEST
   needs the token too.)
6. **Optional but recommended — IAM bootstrap.** If you signed
   in as root, offer to create a dedicated IAM user for future
   assistant work so root is not the standing login (commands
   in the appendix). Proven live 2026-11-20 as far as creating
   the user, attaching and detaching the policy, and deleting it;
   the console password and MFA steps have not been run.
7. **Cleanup when testing** (a scratch stack deployed under its
   own name, per Isolation by name; never the user's
   `s3-blob-store`): delete that stack, then confirm the
   FunctionUrl answers with an AWS-level error (403), not the
   handler. That distinguishes "function gone" from "auth
   working".

## Path C — no AWS account yet (assisted signup)

The assistant drives the AWS signup; the user is present for a
few short moments (the root password, the card, verification
codes, and any challenge). Not yet run end to end. Get the user's explicit go before
starting — signup is a contract and a payment liability in
their name.

1. Assistant fills the signup (email, account name, root
   password set by the user through the secure flow, contact
   details the user provides, **Paid plan** — the Free plan
   closes the account after 6 months or when credits run out,
   which for a credential store is a credential-loss event).
2. **User moment 1 — payment card.** Hand the browser to the
   user for the card step. The assistant never receives, sees,
   or stores card numbers.
3. **User moment 2 — verification codes.** The email code can
   be fetched from the user's connected mailbox with their
   permission. The phone identity code the user relays.
4. **User moment 3 — CAPTCHA / fraud challenge**, if one
   appears: hand the browser to the user to solve it.
5. Continue at Path B step 2. Recommend the IAM bootstrap
   (Path B step 6) so root sign-in is a one-time event.

## Verification — STORE-TEST

Run against any new deployment before calling it done. All
checks must pass. Use labels only this run uses (for example
`store-test-<UTC timestamp>-<n>`), and GET each one first: it
must be 404, or stop. A store already holding blobs must never
lose one to its own test. Every check but the unauthenticated
one needs the token: in CloudShell, read it from the token file
(`TOKEN=$(cat /tmp/s3-blob-store-token.txt)`) before that file is
deleted; elsewhere, through the store's registered connection.

| Check | Expected |
|---|---|
| `GET /` with token | 200, body `ok` |
| `GET /blobs/x` without token | 401 |
| `GET` missing label, with token | 404 |
| Round trip 400 characters of text | byte-identical (hash match) |
| Round trip 2,700 characters | byte-identical |
| Round trip 8,000 characters | byte-identical |
| Round trip 64,000 characters | byte-identical |
| Replace a label, then GET | new content served |
| DELETE, then GET | `deleted`, then 404 |

Payloads are UTF-8 text (ASCII is simplest), as the store
expects (SETUP.md, Bodies): random binary fails, and that would
be a test error, not a store fault. Delete the test labels
afterwards so the bucket is left empty of test data.

## Operations

- **Rotation** (only when the user asks for one, or a token may
  have leaked): re-run the deploy one-paste against the existing
  stack; this is the one time the pre-check finding a stack is
  expected. It generates a fresh token and digest and updates
  the stack in place; the old token stops working when the
  update completes. Not yet run end to end. Move the
  new token into the password manager / secure registration
  (SETUP.md) and delete the file copy as usual. Do this
  immediately if a token may have leaked — a leaked token is
  full read/write access to that one bucket, nothing else.
- **Health:** `GET /` with the token → 200 `ok`. Without the
  token → 401 (which also proves the auth gate, not just the
  function, is alive).
- **Teardown:** confirm with the user first; for a real store,
  every credential in it becomes unreachable through the API.
  `aws cloudformation delete-stack --stack-name <name>`, wait,
  then verify the FunctionUrl returns an AWS-level error. S3
  refuses to delete a non-empty bucket, so with blobs still in
  it the bucket (and the blobs) stay, and the stack ends in
  `DELETE_FAILED` (expected; not yet observed). Empty the
  bucket only for a scratch stack, and only with the user's go:
  `aws s3 rm s3://<BlobBucketName>/blobs/ --recursive`, then
  delete the stack again. To keep the blobs instead, delete the
  stack with `--retain-resources BlobBucket` (the bucket stays,
  outside any stack) and deploy any new store with a
  `NameSuffix`, since the old bucket keeps the default name.
  (Not yet run end to end.)

## Release checklist (maintainers)

- [ ] `python3 tools/build_template.py --check` passes (CI runs
      it): `template.yaml` matches a fresh build from the
      skeleton + `lambda/index.py`, within the 4,096-character
      inline-code budget and the 51,200-byte TemplateBody limit
      (it is about 13 KB).
- [ ] STORE-TEST passes against a scratch stack deployed from
      the exact generated template (fresh `NameSuffix`).
- [ ] The release **attaches `template.yaml` as a release
      asset** — the one-paste downloads from the asset URL,
      which does not serve files that merely exist in the repo.
- [ ] The Terraform route under `alternatives/terraform/` still
      zips `lambda/index.py` (CI checks this) and passes
      `terraform validate`.

## Friction table (observed live, 2026-11-20)

| Observation | Handling |
|---|---|
| Console Toolbar CloudShell panel (Firefox): downloads unsupported | Use the standalone CloudShell console for the token download |
| Multi-line paste triggers a Safe Paste warning | Prefer single lines; verify the dialog text if not |
| Pasted block's last line lacks a trailing newline | It stays unexecuted; the next paste appends to it. End blocks with a newline; verify at a fresh prompt |
| Handler replies (`stored` etc.) have no trailing newline | Prompts run into output in a terminal; cosmetic only |
| Deploy wait ~100 s | Normal; not a stall |
| CloudShell home persists, /tmp does not | The token file goes to /tmp on purpose: a forgotten delete still dies with the session |

## Appendix — IAM bootstrap commands

Create a dedicated user for the assistant (run as root or an
admin), attach admin policy, verify — then use that user, not
root, for later sign-ins:

```
aws iam create-user --user-name <assistant-user>
aws iam attach-user-policy --user-name <assistant-user> --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
aws iam list-attached-user-policies --user-name <assistant-user>
```

Console access for the new user, and its password, are set
through the console/IAM with the password going straight into
the secure credential flow — never through chat or a terminal
echo. **Enable MFA on the new user before using it** (a virtual
MFA device whose seed the user holds), or the standing login is
weaker than the root login with MFA it replaces; what the
change gains is root out of daily use. Scope the policy down from AdministratorAccess if the
assistant's work allows it; admin is what the deploy requires,
not necessarily what daily use requires.
