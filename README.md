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
- `deploy.sh` — the deploy script the one-paste runs (step 3),
  published as a release asset beside `template.yaml`.
- `tests/` — tests for the build tool and its guards, for the
  handler (against an in-memory stand-in for S3), and for
  `deploy.sh` and the one-paste line (against a fake AWS CLI).
  Run them with `python3 -m unittest discover -s tests`.
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
  what each trades off against the one-paste.
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
local CLI, a more hands-on Terraform route — are in
`ALTERNATIVES.md`. All but Terraform deploy this same template;
the one-paste below is the main path, and the one releases are
tested on.)

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

3. **Paste the one line**, with `<release>` replaced by the
   release you are installing (e.g. `v1.0.0`):

   ```
   curl -fsSL -O https://github.com/cmeans/s3-blob-store/releases/download/<release>/template.yaml -O https://github.com/cmeans/s3-blob-store/releases/download/<release>/deploy.sh && bash deploy.sh
   ```

   It downloads two files from that release, `template.yaml` and
   `deploy.sh`, and runs the script. (To read the script first,
   paste the line without its last `&& bash deploy.sh`, read
   `deploy.sh`, then run `bash deploy.sh`.) The script:

   - checks the stack's state first, and **stops** unless it can
     confirm there is no store yet. An existing store means
     deploying would rotate its token, and everything using the
     old one would start getting 401; to do that on purpose, see
     SETUP.md, Rotation. For a failed first deploy, a busy
     stack, or a state it can't read, it says what to do instead.
     Every stop changes nothing;
   - stops if a token file from an earlier run is still in
     `/tmp`, so a token you haven't saved yet is never overwritten
     (`bash deploy.sh --check` says whether that file's token is
     live);
   - generates the bearer token and writes it to a new private
     file in `/tmp` (mode 600, created only if nothing is at that
     path yet) **before** deploying, so it is in the file however
     the deploy ends. The token
     is never displayed: CloudShell replays terminal output on
     session restore, so it never goes to the screen;
   - deploys the stack with only the digest as a parameter, and
     prints the stack's outputs. The deploy takes about 100
     seconds of "Waiting for stack create/update to complete";
     that is normal. Note the `FunctionUrl` output;
   - if the deploy fails or you press Ctrl-C, checks the store
     before touching the token file. If the store didn't take the
     new token (the same answer twice, 20 seconds apart), it
     removes the file. If it did, that's a deploy. If it can't tell
     yet (an interrupted deploy, for example, keeps going on AWS's
     side), it keeps the file and tells you to run `bash deploy.sh
     --check` once the stack has finished changing, in the same
     CloudShell session: the file is in `/tmp`, which is cleared
     when the session ends. (If it's lost that way, `--check` can't
     tell whether its token went live; it says how to find out
     with the previous token, and when to rotate.) `--check` also
     prints the stack's outputs when the token is live, so you
     still get the `FunctionUrl`.

   The token never exists in your own shell, or in the script's:
   one Python process makes it, writes the file and hands back only
   the digest. Run one deploy at a time per store: a second one
   in another tab can't tell the first one's token apart from a
   stale one until the first finishes. If you already run a store from the Terraform
   route in this account and region, use a suffixed stack
   (`SKILL.md`, Isolation by name): both routes derive the same
   bucket name.

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
   as it should. (A `403` with the body `{"Message":null}` on both
   lines comes from AWS, not the handler: the function's invoke
   permissions are missing or wrong.) Otherwise:

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
`SCORECARD.md`) with the one-paste line as it was then. Step 3's
`deploy.sh` and step 5's fresh-label check came after; the
assistant-driven path ran with `deploy.sh` on scratch stacks on
2026-11-21 (same file). The assisted account creation hasn't been
run yet.

## Caveats, honestly

- **History is API-deep.** With versioning off, replaced and deleted
  blobs are gone through every API this store exposes. Whether AWS
  retains anything in internal backups is not observable from
  outside, so do not treat deletion here as proof of destruction.
- **The Function URL is public.** A bearer-style Function URL
  needs a `lambda:InvokeFunction` grant alongside
  `lambda:InvokeFunctionUrl`. The template limits that grant to
  calls through the URL (`InvokedViaFunctionUrl`); the Terraform
  route can't yet (an item in
  [#27](https://github.com/cmeans/s3-blob-store/issues/27)), so there
  any AWS principal can also invoke the function directly. Either
  way, authentication lives in the handler, which is the design
  assumption throughout: worst case, an unauthenticated caller
  reaches a function that returns 401 and can only ever move
  ciphertext.
- **One token, no expiry.** There is no expiry or revocation list:
  the token works until you rotate it, so rotating is how you cut off
  a leaked one, short of deleting the store. With the template that's
  a re-run of the step 3 line with `--rotate` (SETUP.md, Rotation);
  the Terraform route has its own steps in its README. For a
  single-operator store that trade-off is deliberate.
- **Deleting the stack leaves your blobs.** S3 won't delete a
  non-empty bucket, so a stack delete with blobs still in it leaves
  the bucket and the blobs standing and the stack in
  `DELETE_FAILED`. A new default store can't be deployed until that
  stack is gone: either empty the bucket (which destroys every blob)
  and delete the stack again, or keep the blobs and deploy the new
  store with `--suffix <x>`. The details are in `SKILL.md`,
  Operations, Teardown (written for an assistant; the commands work
  just as well by hand).

## Other ways to deploy

The one-paste above is the main path, and the one releases are
tested on. If you'd rather work
differently, [`ALTERNATIVES.md`](ALTERNATIVES.md) covers the other
routes, including a more hands-on Terraform one, and what each
trades off.

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
