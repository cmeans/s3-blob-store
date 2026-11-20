# SKILL — deploying and operating s3-blob-store (for AI assistants)

This file is the playbook for an AI assistant deploying or
operating an s3-blob-store on behalf of its user. Everything in it
was exercised live on 2026-11-20 in both modes described below;
the evidence is in `SCORECARD.md`. The human-facing story is in
`README.md`; wiring a finished deployment into daily use is in
`SETUP.md`.

**How you probably got here:** your user pasted you the prompt
from the README's Deploy section, which points at this file at a
pinned release. Follow the path that matches their situation —
Path C if they have no AWS account, Path B if they have one and
want you to drive, Path A if they are running the paste
themselves and you are advising. If you can install this file as
a persistent skill on your platform, offer to: it turns
"deploy / rotate / check my blob store" into a standing
capability instead of a one-off.

The store in one paragraph: a private S3 bucket behind a Lambda
Function URL. The Lambda checks a bearer token (only its SHA-256
digest exists in AWS) and serves `GET /` (health) and
PUT/GET/DELETE `/blobs/<label>`. Nothing else can reach the
bucket. It is built to hold small encrypted blobs — credentials
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
  data, and cleanup is one stack deletion.
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
   password manager, then runs `rm /tmp/s3-blob-store-token.txt`.
5. Endpoint checks (user runs them, or the assistant does once
   it has the token via SETUP.md): expected, in order —
   `401`, `stored`, `dummy`, `deleted`, `404` for unauthenticated
   GET, PUT, GET, DELETE, GET on `blobs/test`.

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
3. **Run the same one-paste** as Path A step 3. Driving a
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
4. **Token handling.** After the deploy, Actions → Download
   file `/tmp/s3-blob-store-token.txt` to the assistant's side,
   register it through the secure flow in SETUP.md, then delete
   the CloudShell copy and any local plaintext copy once the
   secure registration is verified. The token is never shown in
   the terminal or the chat.
5. **Verify** with the endpoint sequence and STORE-TEST below,
   run by the assistant.
6. **Optional but recommended — IAM bootstrap.** If you signed
   in as root, offer to create a dedicated IAM user for future
   assistant work so root is not the standing login (commands
   in the appendix). Proven live 2026-11-20.
7. **Cleanup when testing:** delete the stack, then confirm the
   FunctionUrl answers with an AWS-level error (403), not the
   handler — that distinguishes "function gone" from "auth
   working".

## Path C — no AWS account yet (assisted signup)

The assistant drives the AWS signup; the user is present for
three scripted moments. Get the user's explicit go before
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
checks must pass.

| Check | Expected |
|---|---|
| `GET /` with token | 200, body `ok` |
| `GET /blobs/x` without token | 401 |
| `GET` missing label, with token | 404 |
| Round trip 400 bytes | byte-identical (hash match) |
| Round trip 2,700 bytes | byte-identical |
| Round trip 8,000 bytes | byte-identical |
| Round trip 64,000 bytes | byte-identical |
| Replace a label, then GET | new content served |
| DELETE, then GET | `deleted`, then 404 |

Delete the test labels afterwards so the bucket is left empty
of test data.

## Operations

- **Rotation:** re-run the deploy one-paste. It generates a
  fresh token and digest and updates the stack in place; the
  old token stops working when the update completes. Move the
  new token into the password manager / secure registration
  (SETUP.md) and delete the file copy as usual. Do this
  immediately if a token may have leaked — a leaked token is
  full read/write access to that one bucket, nothing else.
- **Health:** `GET /` with the token → 200 `ok`. Without the
  token → 401 (which also proves the auth gate, not just the
  function, is alive).
- **Teardown:** `aws cloudformation delete-stack --stack-name
  <name>`, wait for completion, verify the FunctionUrl returns
  an AWS-level error. The bucket must be empty or deletion of
  the bucket resource fails — empty it first and note that
  behaviour if it surprises you.

## Release checklist (maintainers)

- [ ] `python3 tools/build_template.py` regenerates
      `template.yaml` from the skeleton + `lambda/index.py`;
      it refuses to build past the 4,096-character inline-code
      limit. The generated template must also stay well under
      the 51,200-byte TemplateBody limit (it is ~11.6 KB).
- [ ] STORE-TEST passes against a scratch stack deployed from
      the exact generated template (fresh `NameSuffix`).
- [ ] The release **attaches `template.yaml` as a release
      asset** — the one-paste downloads from the asset URL,
      which does not serve files that merely exist in the repo.
- [ ] Layout: the Terraform source (`terraform/` and
      `bootstrap/`) lives under `alternates/terraform/` in the
      repo — kept, not tagged away or deleted — and is kept in
      sync with the handler if the handler changes.

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
echo. Scope the policy down from AdministratorAccess if the
assistant's work allows it; admin is what the deploy requires,
not necessarily what daily use requires.
