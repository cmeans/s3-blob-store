# ALTERNATES — other ways to stand the same resources up

The supported deployment is the CloudShell one-paste in
`README.md`. It is the only path that needs nothing installed,
nothing hosted, and no account preparation beyond the AWS
account itself, and it is the path that gets tested on every
release (`SCORECARD.md`).

The alternatives below produce the **identical stack** — the
same 7 resources from the same `template.yaml` — and are kept
for people whose circumstances fit them better. They are
documented, not supported: if one of them and the one-paste
disagree, the one-paste is right.

Shared prerequisite for all of them: the token and its digest.
Generate both wherever you have Python 3:

```
python3 -c 'import secrets, hashlib; t = secrets.token_hex(32); print(t); print(hashlib.sha256(t.encode()).hexdigest())'
```

The first line is the token — into a password manager
immediately. The second is the SHA-256 digest, the only form
that ever goes into a stack parameter. Whichever path you
take, finish with the endpoint check and wiring in `README.md`
steps 5–6 / `SETUP.md`.

## 1. Launch Stack (hosted template link)

The classic AWS one-click: a link of the form
`https://console.aws.amazon.com/cloudformation/home#/stacks/quickcreate?templateURL=<hosted template.yaml>`
opens the console with the template loaded; you paste the
digest into the `BearerTokenSha256` parameter, tick the
acknowledgement that the stack creates an IAM role, and
create the stack.

Why it is not the main path:

- The template must be hosted in an S3 bucket to have a
  templateURL, and a hosted template's bucket must be in the
  **same region** as the stack — so a hosted deployment means
  maintaining a template bucket per region (or accepting one
  region), a long-lived public object on the maintainer's
  account, and a link table per region in the README. The
  one-paste downloads the template from the GitHub release
  instead and deploys from a local file, which has none of
  those constraints.
- The digest cannot travel in the link (NoEcho parameters are
  ignored in quick-create URLs), so the token step is manual
  either way — the one-click saves less than it appears to.

If you maintain a fork and want Launch Stack links, host the
generated `template.yaml` in a bucket per region you support
and build the links from the pattern above.

## 2. Console upload

For users who don't want a shell at all:

1. Download `template.yaml` from the release (the asset, or
   the file in the repo at the release tag).
2. CloudFormation console → *Create stack → With new
   resources* → *Upload a template file* → choose the file.
3. Stack name `s3-blob-store`; paste the digest into
   `BearerTokenSha256`; leave `NameSuffix` empty.
4. Acknowledge the IAM-role creation on the review page and
   create the stack. Outputs are the same as the one-paste's.

Identical result; more screens, and you still need the digest
from the generator above, which needs a shell or an equivalent
tool — which is why the one-paste, that does it inline, is
the main path.

## 3. Local AWS CLI

For users who already have the AWS CLI configured with
credentials on their own machine, the one-paste's deploy step
works unchanged outside CloudShell — it *is* a CLI command:

```
curl -fsSLO https://github.com/cmeans/s3-blob-store/releases/download/<release>/template.yaml
aws cloudformation deploy --template-file template.yaml --stack-name s3-blob-store --capabilities CAPABILITY_IAM --parameter-overrides BearerTokenSha256=<digest>
```

Generate the token and digest with the generator above and
handle the token exactly as in `README.md` step 4 (password
manager, no terminal echo beyond the one generation, which
you should clear from scrollback). This path's only downside
is the prerequisite the main path exists to avoid: a local
CLI install with working credentials.

## 4. Terraform (the earlier version)

The Terraform version lives in this repo at
`alternates/terraform/` (with its CloudFormation bootstrap for
the state bucket) — kept in the repo, not retired, and kept in
sync with the handler if the handler changes. Existing
Terraform deployments are unaffected and need no action —
the handler and the API are the same. New deployments should
use the template: the Terraform version requires a local
Terraform install, a bootstrap step, and edits to a backend
file, and it is no longer how releases are tested. Its
STORE-TEST scorecard is preserved in `SCORECARD.md`.

## 5. By hand (not recommended)

The template *is* the specification: a bucket (versioning
off, public access blocked, SSE-S3) and its policy, one IAM
role with a policy scoped to that bucket, one Lambda running
`lambda/index.py` with the bucket name and token digest as
environment, one Function URL, and two Lambda permissions
(see the README caveats for why two). Creating those by
clicking through consoles is possible and pointless — every
property is already encoded and tested in `template.yaml`.
Listed here only so nobody wonders whether a hand-built
variant is a supported configuration: it isn't.
