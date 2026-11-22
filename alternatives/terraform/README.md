# s3-blob-store: the Terraform route

The same store as the repo's CloudFormation template, deployed with
Terraform from your own machine. Use it if you'd rather keep the store
in Terraform, run every step yourself, and have a plan to read before
anything changes. The CloudFormation template at the repo root
(`template.yaml`, see the [main README](../../README.md)) is the
default route; this one deploys the same resources and the same
handler, `lambda/index.py` at the repo root.

This route deploys **one store per AWS account**: its IAM role
(`handoff-blob-store-lambda`) has a fixed name, and IAM role names
are account-wide, across every region. (Its Lambda function,
`handoff-blob-store`, is fixed per region too.) `name_suffix` changes
only the bucket's name. For a second store in the same account, in
any region, deploy the CloudFormation template as a second stack; it
generates its role and function names, and needs its own `NameSuffix`
only in the same region as the first. Don't deploy both routes in the
same account and region without a suffix either: they derive the same
bucket name (`handoff-blobs-<account>-<region>`).

## What it creates

- The `.tf` files here: the blob bucket, the Lambda, its scoped
  role, and the Function URL.
  - `backend.tf`: S3 backend with native state locking (Terraform 1.11
    or later). The state bucket and its region are given at `init`
    time, so the file needs no edits. State is never committed.
  - `main.tf`: the resources. The Lambda's code is zipped from
    `../../lambda/index.py`.
  - `variables.tf`: the token digest, the region (default
    `us-east-2`), an optional bucket name suffix, and an optional
    cap on the function's concurrency (below).
  - `outputs.tf`: bucket name, function URL, role ARN. None are
    secrets.

It needs a Terraform state bucket, which it doesn't create (Terraform
can't create the bucket its own state lives in). That bucket isn't
part of this store: it's account- and region-level, and can hold the
state of any number of Terraform projects, each under its own key.
Use one you already have, or create one with the shared bootstrap
stack, [`../terraform-state-bootstrap/bootstrap.yaml`](../terraform-state-bootstrap/bootstrap.yaml):
a small CloudFormation stack that creates only that bucket and its
TLS-only policy. The bucket is named `tfstate-<account>-<region>`,
versioned, and kept if the stack is deleted.

### Two buckets, opposite versioning

| Bucket | Versioning | Why |
| --- | --- | --- |
| Terraform state bucket (shared) | **On** (the bootstrap stack's is; turn it on in your own) | Old state versions are the recovery path if a write goes bad. |
| Blob bucket (Terraform) | **Off** | A replaced or deleted blob must actually be gone through the API. |

State isn't stored in the blob bucket or through the blob store. That
would be circular, and state needs locking and signed AWS writes,
which is a different job from storing ciphertext by label.

## Deploy

You need an AWS account, the AWS CLI configured with your own
credentials, Terraform 1.11 or later, and Python 3. Your credentials
stay on your machine; nothing in this repo needs them at runtime.
Run the commands from the repo root.

1. **Have a state bucket.** If you already keep Terraform state in
   an S3 bucket in this account, use it (with versioning on), and
   skip to step 2 with its name and region. Otherwise, once per
   account and region, create a stack from
   `alternatives/terraform-state-bootstrap/bootstrap.yaml` in the
   CloudFormation console (no parameters needed), and note its
   `StateBucketName` and `Region` outputs. Keep that stack when you
   remove the store: other Terraform projects may share its bucket.
2. **Initialize Terraform** with that bucket and region:

   ```
   terraform -chdir=alternatives/terraform init \
     -backend-config="bucket=<StateBucketName>" \
     -backend-config="region=<Region>"
   ```

3. **Generate the bearer token.** Only its SHA-256 digest goes to
   Terraform:

   ```
   read TOKEN DIGEST < <(python3 -c 'import secrets,hashlib;t=secrets.token_hex(32);print(t,hashlib.sha256(t.encode()).hexdigest())')
   export TF_VAR_bearer_token_sha256=$DIGEST
   ```

   Save the token in a password manager now: `echo "$TOKEN"` shows
   it once, then `clear` the screen. The password manager is the only
   copy after step 5.

   To deploy somewhere other than `us-east-2`, also
   `export TF_VAR_region=<region>`.

4. **Plan and apply.**

   ```
   terraform -chdir=alternatives/terraform plan
   terraform -chdir=alternatives/terraform apply
   ```

   The plan is 12 resources: the bucket and its configuration, the
   role and its policies, the Lambda, its Function URL, and the two
   Function URL permissions (the main README's caveats say why there
   are two).

5. **Check the endpoint.** With `URL` set to the `function_url`
   output. The check uses a label of its own, so it can never
   overwrite a blob you already store:

   ```
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

   Expected, in order: `stored`, `dummy`, `deleted`, `404`. Then
   `unset TOKEN DIGEST TF_VAR_bearer_token_sha256`.

6. **Give your agent the token** (from your password manager) through whatever secure credential
   mechanism your agent platform provides, scoped to the function
   URL's host, as a bearer header. Never put the token in a file, a
   template, or a chat.

**Token rotation:** generate a new token (step 3), apply with its
digest, then update the credential wherever your agent platform holds
it.

**Capping concurrent calls:** `reserved_concurrency` (default 0, no
cap) sets a reserved concurrency on the function, which limits how
fast someone calling the public Function URL can run up the bill;
calls past it get HTTP `429`. A small value such as 5 is plenty for
an assistant's occasional fetches, though while someone floods the
URL the assistant's calls get `429` too (SETUP.md, Capping
concurrent calls). 0 here means "no cap" (it becomes the provider's
`-1`), never Lambda's own 0, which stops every call. Unlike the
template's parameter, Terraform keeps no previous value: give it on
every apply, a rotation's included, or the cap goes back to 0. A
`terraform.tfvars` file in this directory holding
`reserved_concurrency = 5` does that (`*.tfvars` is git-ignored; keep
the digest out of it). AWS refuses a cap that would cut into the
concurrency it keeps unreserved: 100 by its documentation. On a new
account whose limit was 10, it refused any cap at all. If the apply fails that way, leave it at 0, or
request a higher quota (Service Quotas, AWS Lambda, "Concurrent
executions", `L-B99A9384`); SETUP.md, Capping concurrent calls, has
the details. `aws lambda get-account-settings --query AccountLimit`
shows the account's limit, and `aws lambda get-function-concurrency
--function-name handoff-blob-store` the cap.

## If you deployed from an earlier version of this repo

Before this move, the Terraform files were at `terraform/` and
`bootstrap/`, `backend.tf` named the state bucket itself, and the
state key was `muse-field-kit/handoff-blob-store/terraform.tfstate`.
A running deployment is unaffected by the repo changing. To keep
managing it from this directory:

1. Note the `bucket` and `region` from your edited
   `terraform/backend.tf`. That edit makes `git pull` stop on a
   conflict (the file was moved and rewritten here). Keep your copy
   of the two values, take this version, and delete the old
   `terraform/` directory, including `terraform/.terraform/`.
2. Initialize against your existing state: pass your bucket and
   region as in step 2, plus
   `-backend-config="key=muse-field-kit/handoff-blob-store/terraform.tfstate"`.
   Set `TF_VAR_region` if you didn't deploy in `us-east-2`, and
   `TF_VAR_bearer_token_sha256` to your current token's digest.
3. Run `plan`. **If it shows anything to add or destroy, stop and
   don't apply:** the key, bucket or region doesn't match your
   state, and an apply would try to create a second store.

`plan` should show nothing to add or destroy. The zip is now built
from the single `lambda/index.py` file rather than the old
`lambda/` directory; the archive layout is the same (one
`index.py` entry, same mode). If `lambda/index.py` has changed
since your deployment (it has, for example, the handler fixes of
2026-11-20 and 2026-11-21), `plan` shows the Lambda function
updated in place, and only that. The
bootstrap template moved to
`alternatives/terraform-state-bootstrap/`, and its description,
`Project` tag (now removed) and output descriptions changed. Its
bucket is the same; your existing bootstrap stack needs no update,
and it's safe to leave it as it is.
