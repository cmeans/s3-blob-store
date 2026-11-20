# ALTERNATIVES — other ways to stand the same resources up

The supported deployment is the CloudShell one-paste in
`README.md`. It is the only path that needs nothing installed,
nothing hosted, and no account preparation beyond the AWS
account itself, and it is the path releases are tested on
(`SCORECARD.md`).

Sections 1 to 3 below produce the **identical stack** — the
same 7 resources from the same `template.yaml`; section 4,
Terraform, deploys the same store with its own resources; and
all are kept for people whose circumstances fit them better. They are
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
steps 5–6 / `SETUP.md`. These paths create no token file, so in
step 5 set `TOKEN` without typing it (`read -rs TOKEN`, then a
paste from the password manager) instead of the `cat` line, and
leave out the final `rm`.

## 1. Launch Stack (hosted template link)

The classic AWS one-click: a link of the form
`https://console.aws.amazon.com/cloudformation/home#/stacks/quickcreate?templateURL=<hosted template.yaml>`
opens the console with the template loaded; you paste the
digest into the `BearerTokenSha256` parameter, tick the
acknowledgment that the stack creates an IAM role, and
create the stack.

Why it is not the main path:

- The template must be hosted in an S3 bucket to have a
  templateURL. AWS doesn't document a region rule for template
  URLs, but cross-region quick-create links are reported to
  fail, so a hosted deployment likely means a template bucket
  per region (not tested here), plus a long-lived public object
  on the maintainer's account and a link table per region in the
  README. The
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
credentials on their own machine (bash and Python 3 too), the
one-paste works unchanged outside CloudShell: `deploy.sh` only
calls the AWS CLI. Run the README's step 3 line in an empty
directory. It writes the token to `/tmp` on your machine, as in
CloudShell; move it to your password manager and delete the
file. This path's only downside
is the prerequisite the main path exists to avoid: a local
CLI install with working credentials.

## 4. Terraform

For people who'd rather keep the store in Terraform and run
every step themselves, with a plan to read before anything
changes. It lives in
[`alternatives/terraform/`](alternatives/terraform/README.md), with
its own README (the steps, and how an existing deployment from an
earlier version of this repo keeps its state). It needs a
Terraform state bucket: one you have, or the shared one
[`alternatives/terraform-state-bootstrap/`](alternatives/terraform-state-bootstrap/bootstrap.yaml)
creates. It zips the same
`lambda/index.py` the template inlines, so the API is the same.

It isn't the main path because it needs a local Terraform
install, AWS credentials on your machine and a state bucket,
and it isn't how releases are tested. It creates 12 Terraform
resources where the template creates 7 (Terraform configures the
bucket through separate resources), with the same behavior. Its
STORE-TEST scorecard, from the first live deployment, is in
`SCORECARD.md`.

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
