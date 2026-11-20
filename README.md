# s3-blob-store

[![CI](https://github.com/cmeans/s3-blob-store/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/cmeans/s3-blob-store/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/cmeans/s3-blob-store)](LICENSE)

A dedicated store for ciphertext blobs, built on Amazon S3 with a
Lambda Function URL as its only front door. It was built and tested
as a store for the `encrypted-credential-handoff` pattern in
[muse-field-kit](https://github.com/cmeans/muse-field-kit), where an
agent must fetch an encrypted blob unattended while holding no cloud
credential of its own. It works just as well for any opaque text an
agent needs to park by label and fetch later.

With [Muse](https://ai.meta.com/muse/), Meta's personal AI agent, the
store is a custom connector: Muse calls the Function URL with a
bearer token its connector holds, and needs no AWS credentials. The
playbook in `SKILL.md` lets Muse, or another assistant that can
browse and hold credentials, deploy and verify the store for you.

The store never sees plaintext. Callers store and fetch ciphertext;
decryption, if any, happens on the caller's side with keys the store
never touches.

## How it works

- One private S3 bucket holds the blobs under a `blobs/` prefix,
  addressed by exact label: `blobs/<label>`. Versioning is off, so a
  replaced or deleted blob is actually gone through the API.
- A small Lambda function is the only reader and writer. Its IAM
  role can only Get, Put, and Delete objects under that one prefix.
  There are no IAM users, no access keys, and nothing long-lived to
  leak.
- The Lambda is reached through its Function URL. Every request
  must carry a bearer token. The Lambda stores only the token's
  SHA-256 digest and compares digests in constant time, so nothing
  in the deployed configuration can be replayed as a credential.
- The fetching agent holds no AWS credential. Its platform swaps the
  bearer token in at request time, to this one host only, so the
  token never sits in a file, an environment variable, or the
  agent's context. (Calling Lambda or S3 directly would require
  SigV4 signing with a real AWS secret key on the agent's machine.
  That is exactly what this design avoids.)
- Deployment is a single CloudFormation template. CloudFormation
  holds its own state, so there is no bootstrap stack, no state
  bucket, and nothing to install locally.

## Layout

- `template.yaml` — the whole deployment: the bucket and its
  policy, the Lambda, its scoped role, and the Function URL.
  Generated from the two sources below; do not edit it directly.
- `template.skeleton.yaml` — the template source, with a marker
  where the handler code goes.
- `lambda/index.py` — the front-door handler, the canonical copy.
  Bearer check, then GET / PUT / DELETE of `blobs/<label>`, plus a
  `GET /` health route that answers 200 to authorized callers.
- `tools/build_template.py` — regenerates `template.yaml` from the
  skeleton and the handler (`--check` verifies the committed copy),
  and refuses to build past the 4,096-character inline-code budget
  or CloudFormation's 51,200-byte template-body limit.
- `tests/test_build_template.py` — tests for the build tool and its
  guards (`python3 -m unittest discover -s tests`).
- `alternatives/terraform/` — the same resources deployed with
  Terraform, for people who'd rather run every step themselves; its
  own README has the steps.
- `alternatives/terraform-state-bootstrap/` — a shared Terraform
  state bucket, one per account and region, for the Terraform route
  (or any other Terraform project) if you don't have one already.
- `SCORECARD.md` — STORE-TEST results, including the caveats and
  the AWS behaviors that bit us on the way.
- `SKILL.md` — the playbook for an AI assistant: both deploy
  paths (you run the paste, or your assistant drives from a
  login you supply), assisted account creation, token
  discipline, verification, and the release checklist.
- `SETUP.md` — wiring a deployed store into daily use:
  registering the URL and token with your assistant, the API
  contract, label rules, and rotation.
- `ALTERNATIVES.md` — the other ways to stand the same resources
  up (Launch Stack, console upload, local CLI, Terraform), and
  why the one-paste is the supported path instead.
- `.github/workflows/` — CI (the template check, the tests,
  cfn-lint, ruff, and Terraform `fmt` and `validate`) and the PR
  label workflow.

## Deploy

**Fastest start — hand this to your AI assistant.** Any
assistant that can browse and hold credentials securely can run
the whole deployment for you, including walking an AWS signup
with you if you don't have an account. Paste it this prompt:

```
Deploy s3-blob-store for me, following the playbook at
https://raw.githubusercontent.com/cmeans/s3-blob-store/<release>/SKILL.md

- If I don't have an AWS account yet, start with Path C
  (assisted signup). Ask for my explicit go before starting
  signup, and hand the browser to me for the payment-card
  step — never ask me for card numbers in chat.
- Otherwise use Path B: I'll save my AWS login through your
  secure credential flow and give you MFA codes when you ask.
- Use the one-paste deploy, verify with the full STORE-TEST,
  and register the store as described in SETUP.md before you
  call it done.
```

Replace `<release>` with the release you want installed, so
the playbook your assistant follows is the one that matches
the template it deploys. Your part in that flow is a few short
moments: saving the login, an MFA or verification code when
asked, and, if you're creating the account, the card step and
any challenge AWS shows. Everything else the assistant does.

Prefer to run it yourself? The same deployment, by hand:

You need an AWS account. Nothing else: no AWS CLI, no Terraform,
nothing installed on your machine. The whole deployment is one
line, pasted into a browser shell. (Other ways to stand the same
resources up — a hosted Launch Stack link, a console upload, a
local CLI, the earlier Terraform version — are in
`ALTERNATIVES.md`. All but Terraform deploy this same template;
the one-paste below is the supported path.)

1. **Create the AWS account, on the Paid plan, with root MFA.**
   Start at https://aws.amazon.com/free/. Since July 2025 new
   accounts choose a Free plan or a Paid plan. The Free plan
   closes the account after 6 months or when its credits run
   out — for a store whose whole job is to still be there when
   an agent comes back for a credential, that is a
   credential-loss event, so choose Paid. Cost at this scale is
   effectively zero either way. No account yet and you'd like
   your assistant to walk the signup with you? It can — see
   `SKILL.md`, Path C.

2. **Open CloudShell.** In the AWS console, open the
   **standalone CloudShell console** in the region you want the
   store in — the store is created in whatever region the
   CloudShell session is in. Use a Bash session (the default).
   Use the standalone console rather than the small embedded
   toolbar panel: in some browsers the toolbar panel cannot
   download files, and step 4 needs a download.

3. **Check there's no store yet, then paste the one line.**
   First:

   ```
   aws cloudformation describe-stacks --stack-name s3-blob-store --query "Stacks[0].StackStatus" --output text
   ```

   It should fail with "Stack with id s3-blob-store does not
   exist". If it prints a status instead, a store (or a failed
   attempt) is already there: **stop.** Pasting the line below
   over an existing store rotates its token, and everything using
   the old one starts getting 401 (to rotate on purpose, see the
   caveats). `ROLLBACK_COMPLETE` is a failed first deploy: delete
   it with `aws cloudformation delete-stack --stack-name
   s3-blob-store`, then `aws cloudformation wait
   stack-delete-complete --stack-name s3-blob-store`, and start
   this step again. A status ending in `_IN_PROGRESS`: wait, then
   check again. `DELETE_FAILED`: see the caveats on deleting the
   stack. If
   you already run a store from the Terraform route in this
   account and region, see `SKILL.md`'s "Isolation by name" for
   a suffixed stack: both routes derive the same bucket name.

   Then paste the line, with `<release>` replaced by the release
   you are installing (e.g. `v1.0.0`):

   ```
   curl -fsSLO https://github.com/cmeans/s3-blob-store/releases/download/<release>/template.yaml && read T D < <(python3 -c 'import secrets,hashlib;t=secrets.token_hex(32);print(t,hashlib.sha256(t.encode()).hexdigest())') && aws cloudformation deploy --template-file template.yaml --stack-name s3-blob-store --capabilities CAPABILITY_IAM --parameter-overrides BearerTokenSha256=$D && (umask 077; printf %s "$T" > /tmp/s3-blob-store-token.txt) && unset T D && aws cloudformation describe-stacks --stack-name s3-blob-store --query "Stacks[0].Outputs" --output table
   ```

   What it does, in order: downloads the template from the
   release, generates the bearer token and its SHA-256 digest
   in memory, deploys the stack with only the digest as a
   parameter, writes the token to a private file in `/tmp`
   (it is never displayed — CloudShell replays terminal output
   on session restore, so the token never goes to the screen),
   and prints the stack's outputs. The deploy takes about
   100 seconds of "Waiting for stack create/update to
   complete"; that is normal. Note the `FunctionUrl` output.

   The stack creates 7 resources: the bucket and its policy,
   the role, the Lambda, its Function URL, and the two
   Function URL permissions (see the caveats for why there
   are two).

4. **Take the token out.** CloudShell's Actions menu →
   *Download file*, path `/tmp/s3-blob-store-token.txt`. Save
   the token (64 hex characters) in a password manager. Keep the
   file for step 5.

5. **Check the endpoint**, still in CloudShell. Set `URL` to the
   `FunctionUrl` output (it ends with `/`), and read the token
   from the file rather than typing it: a typed token shows on
   screen and stays in CloudShell's persistent shell history.
   The check uses a label of its own, so it can never overwrite
   a blob you already store:

   ```
   URL=<FunctionUrl output>
   TOKEN=$(cat /tmp/s3-blob-store-token.txt)
   LABEL="store-check-$(date -u +%Y%m%dT%H%M%SZ)"
   curl -s -o /dev/null -w "%{http_code}\n" "$URL"blobs/$LABEL
   curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL"blobs/$LABEL
   ```

   Expected: `401`, then `404`. **If the second is anything but
   `404`, stop:** the label is taken, or the store isn't answering
   as it should. Otherwise:

   ```
   curl -s -w "\n" -X PUT -H "Authorization: Bearer $TOKEN" --data-binary "dummy" "$URL"blobs/$LABEL
   curl -s -w "\n" -H "Authorization: Bearer $TOKEN" "$URL"blobs/$LABEL
   curl -s -w "\n" -X DELETE -H "Authorization: Bearer $TOKEN" "$URL"blobs/$LABEL
   curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL"blobs/$LABEL
   ```

   Expected, in order: `stored`, `dummy`, `deleted`, `404`.
   Then clear the token from the shell and delete the file:

   ```
   unset TOKEN && rm /tmp/s3-blob-store-token.txt
   ```

   (`/tmp` is recycled with the session, so even a forgotten
   delete dies with it, but delete it anyway.)

6. **Wire it up.** Give your agent the URL and token through
   whatever secure credential mechanism your agent platform
   provides, scoped to the function URL's host, as a bearer
   header — never in a file, a template, or a chat. The exact
   registration steps and the API contract are in `SETUP.md`.

**Rather not run commands at all?** Your assistant can do
steps 2–5 for you: you save your AWS login through its secure
credential flow (root or an admin user — either works) and
supply an MFA code when asked; it runs the same paste, takes
the token out the same way, and verifies the endpoint itself.
That path, and the assisted account creation, are written up
for assistants in `SKILL.md`. Both deploy paths were exercised
end to end before this README was published (results in
`SCORECARD.md`), with step 3's pre-check and step 5's
fresh-label check added since; the assisted account creation
hasn't been run yet.

## Caveats, honestly

- **History is API-deep.** With versioning off, replaced and deleted
  blobs are gone through every API this store exposes. Whether AWS
  retains anything in internal backups is not observable from
  outside, so do not treat deletion here as proof of destruction.
- **The Function URL is public.** A bearer-style Function URL
  needs a `lambda:InvokeFunction` grant alongside
  `lambda:InvokeFunctionUrl`. The template limits that grant to
  calls through the URL (`InvokedViaFunctionUrl`); the Terraform
  route can't yet, so there any AWS principal can also invoke the
  function directly. Either way, authentication lives in the
  handler, which is the design assumption throughout: worst case,
  an unauthenticated caller reaches a function that returns 401
  and can only ever move ciphertext.
- **Token rotation is a re-run.** Paste the deploy line again
  (step 3's check will find the stack; that's expected here): it
  generates a fresh token, updates the stack with its digest,
  and the old token stops working; then update the credential
  wherever your agent platform holds it (SETUP.md). There is no
  expiry or revocation list; for a single-operator store that
  trade-off is deliberate.
- **Deleting the stack** removes the function, role and URL. The
  bucket is only deleted once it is empty — S3 refuses to delete a
  non-empty bucket, so a stack delete with blobs still in it
  leaves the bucket (and your blobs) standing, by design, and the
  stack in `DELETE_FAILED`. That bucket keeps the default name, so
  a new default stack would fail on it. To start over, either
  empty it (destroying every blob) and delete the stack again, or
  delete the stack with `--retain-resources BlobBucket` and deploy
  the new store with a `NameSuffix` (`SKILL.md`, Isolation by
  name). Not yet run end to end.
- **Cost** at handoff scale is effectively zero: a handful of small
  S3 objects and a rarely-invoked Lambda.

## Earlier Terraform version

This repo previously deployed with Terraform (plus a CloudFormation
bootstrap for its state bucket). That route is still supported, in
[`alternatives/terraform/`](alternatives/terraform/README.md): it uses
the same handler file as the template, and its STORE-TEST scorecard
is preserved in `SCORECARD.md`. Existing Terraform deployments are
unaffected; that README says how to keep managing one from the new
location. New deployments should use the template above.

## Acknowledgments

Designed and maintained by a human.

Built with AI collaborators:

- **Muse**, Meta's personal AI agent, drafted the CloudFormation
  template and these docs, and ran the assistant-driven STORE-TEST
  end to end.
- **Claude** (Anthropic), as claude.ai, proposed and reviewed the
  CloudShell one-paste deploy; as Claude Code, it built the repo's
  CI and change workflow.

The design follows AWS's documentation for Lambda function URLs,
CloudFormation and CloudShell.

## License

MIT. See `LICENSE`.
