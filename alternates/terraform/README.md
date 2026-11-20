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

- `bootstrap/bootstrap.yaml`: a small CloudFormation stack that
  creates only the Terraform state bucket. It has to exist before
  Terraform can run, so it is the one piece Terraform doesn't manage.
  Its name is derived from your account ID and region, so it doesn't
  collide with anyone else's.
- The `.tf` files here, everything else: the blob bucket, the Lambda,
  its scoped role, and the Function URL.
  - `backend.tf`: S3 backend with native state locking (Terraform 1.11
    or later). The bucket and region come from the bootstrap stack at
    `init` time, so the file needs no edits. State is never committed.
  - `main.tf`: the resources. The Lambda's code is zipped from
    `../../lambda/index.py`.
  - `variables.tf`: the token digest, the region (default
    `us-east-2`), and an optional bucket name suffix.
  - `outputs.tf`: bucket name, function URL, role ARN. None are
    secrets.

### Two buckets, opposite versioning

| Bucket | Versioning | Why |
| --- | --- | --- |
| Terraform state bucket (bootstrap) | **On** | Old state versions are the recovery path if a write goes bad. |
| Blob bucket (Terraform) | **Off** | A replaced or deleted blob must actually be gone through the API. |

State isn't stored in the blob bucket or through the blob store. That
would be circular, and state needs locking and signed AWS writes,
which is a different job from storing ciphertext by label.

## Deploy

You need an AWS account, the AWS CLI configured with your own
credentials, Terraform 1.11 or later, and Python 3. Your credentials
stay on your machine; nothing in this repo needs them at runtime.
Run the commands from the repo root.

1. **Bootstrap the state bucket.** In the CloudFormation console,
   create a stack from `alternates/terraform/bootstrap/bootstrap.yaml`.
   No parameters are needed. Note the `StateBucketName` and `Region`
   outputs.
2. **Initialize Terraform** with those two values:

   ```
   terraform -chdir=alternates/terraform init \
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
   terraform -chdir=alternates/terraform plan
   terraform -chdir=alternates/terraform apply
   ```

   The plan is 12 resources: the bucket and its configuration, the
   role and its policies, the Lambda, its Function URL, and the two
   Function URL permissions (the main README's caveats say why there
   are two).

5. **Check the endpoint.** With `URL` set to the `function_url`
   output:

   ```
   curl -s -o /dev/null -w "%{http_code}\n" "$URL"blobs/test
   curl -s -X PUT -H "Authorization: Bearer $TOKEN" --data-binary "dummy" "$URL"blobs/test
   curl -s -H "Authorization: Bearer $TOKEN" "$URL"blobs/test
   curl -s -X DELETE -H "Authorization: Bearer $TOKEN" "$URL"blobs/test
   curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$URL"blobs/test
   ```

   Expected, in order: `401`, `stored`, `dummy`, `deleted`, `404`.
   Then `unset TOKEN DIGEST TF_VAR_bearer_token_sha256`.

6. **Give your agent the token** (from your password manager) through whatever secure credential
   mechanism your agent platform provides, scoped to the function
   URL's host, as a bearer header. Never put the token in a file, a
   template, or a chat.

**Token rotation:** generate a new token (step 3), apply with its
digest, then update the credential wherever your agent platform holds
it.

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

`plan` should show no changes. The zip is now built from the
single `lambda/index.py` file rather than the old `lambda/`
directory, but the archive is the same (one `index.py` entry, same
bytes and mode), so its hash, and the Lambda, are unchanged. The
bootstrap stack's `Project` tag and output descriptions also changed;
they apply only if you update that stack, and it's safe to leave it
as it is.
