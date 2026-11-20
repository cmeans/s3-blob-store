# s3-blob-store

A dedicated store for ciphertext blobs, built on Amazon S3 with a
Lambda Function URL as its only front door. It was built and tested
as a store for the `encrypted-credential-handoff` pattern in
[muse-field-kit](https://github.com/cmeans/muse-field-kit), where an
agent must fetch an encrypted blob unattended while holding no cloud
credential of its own. It works just as well for any opaque text an
agent needs to park by label and fetch later.

The store never sees plaintext. Callers store and fetch ciphertext;
decryption, if any, happens on the caller's side with keys the store
never touches.

## How it works

- One private S3 bucket holds the blobs under a `blobs/` prefix,
  addressed by exact label: `blobs/<label>`.
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

## Layout

- `bootstrap/bootstrap.yaml` — a small CloudFormation stack that
  creates only the Terraform state bucket. It has to exist before
  Terraform can run, so it is the one piece Terraform does not
  manage. Bucket names are derived from your account ID and region,
  so they do not collide with anyone else's.
- `terraform/` — everything else: the blob bucket, the Lambda, its
  scoped role, and the Function URL.
  - `backend.tf` — S3 backend with native state locking
    (Terraform 1.11 or later). State is never committed to git.
  - `main.tf` — the resources.
  - `lambda/index.py` — the front-door handler. Bearer check, then
    GET / PUT / DELETE of `blobs/<label>`, plus a `GET /` health
    route that answers 200 to authorized callers.
  - `variables.tf` — the token digest input (see below) and an
    optional bucket name suffix.
  - `outputs.tf` — bucket name, function URL, role ARN. None are
    secrets.
- `SCORECARD.md` — the full STORE-TEST result from the first live
  deployment, including the caveats and the two AWS behaviours that
  bit us on the way.

## Two buckets, opposite versioning

| Bucket | Versioning | Why |
| --- | --- | --- |
| Terraform state bucket (bootstrap) | **On** | Old state versions are the recovery path if a write goes bad. |
| Blob bucket (Terraform) | **Off** | A replaced or deleted blob must actually be gone through the API. |

State is not stored in the blob bucket, and not through the blob
store. That would be circular, and state needs locking and signed
AWS writes, which is a different job from storing ciphertext by
label.

## Deploy

You need an AWS account, the AWS CLI configured with your own
credentials, and Terraform 1.11 or later. Your credentials stay on
your machine; nothing in this repo needs them at runtime.

1. **Bootstrap the state bucket.** In the CloudFormation console,
   create a stack from `bootstrap/bootstrap.yaml`. No parameters are
   needed. Note the `StateBucketName` and `Region` outputs.
2. **Point Terraform at it.** Put those two values in
   `terraform/backend.tf` (`bucket` and `region`), then:

   ```
   terraform init
   ```

3. **Generate the bearer token.** Keep the plaintext somewhere safe
   (a password manager). Only its digest goes to Terraform:

   ```
   TOKEN=$(openssl rand -hex 32)
   export TF_VAR_bearer_token_sha256=$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)
   ```

4. **Apply.**

   ```
   terraform plan
   terraform apply
   ```

   The plan is 12 resources: the bucket and its configuration, the
   role and its policies, the Lambda, its Function URL, and the two
   Function URL permissions (see the caveats for why there are two).

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

6. **Give your agent the token** through whatever secure credential
   mechanism your agent platform provides, scoped to the function
   URL's host, as a bearer header. Never put the token in a file,
   a template, or a chat.

## Caveats, honestly

- **History is API-deep.** With versioning off, replaced and deleted
  blobs are gone through every API this store exposes. Whether AWS
  retains anything in internal backups is not observable from
  outside, so do not treat deletion here as proof of destruction.
- **The Lambda is publicly invocable.** AWS requires an
  unconditional `lambda:InvokeFunction` grant for a bearer-style
  Function URL (the scoped condition AWS's console uses cannot be
  expressed in Terraform). Authentication therefore lives in the
  handler, which is the design assumption throughout: worst case, an
  unauthenticated caller reaches a function that returns 401 and can
  only ever move ciphertext.
- **Token rotation is manual.** Generate a new token, apply with its
  new digest, update the credential wherever your agent platform
  holds it. There is no expiry or revocation list; for a
  single-operator store that trade-off is deliberate.
- **Cost** at handoff scale is effectively zero: a handful of small
  S3 objects and a rarely-invoked Lambda.

## Licence

MIT. See `LICENSE`.
