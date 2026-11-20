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

- `template.yaml`: the CloudFormation deployment (the bucket and its
  policy, the Lambda, its scoped role, and the Function URL),
  generated from `template.skeleton.yaml` and `lambda/index.py` by
  `tools/build_template.py`. Don't edit it directly.
- `lambda/index.py`: the front-door handler, the one copy both deploy
  routes use. Bearer check, then GET / PUT / DELETE of
  `blobs/<label>`, plus a `GET /` health route that answers 200 to
  authorized callers.
- `alternatives/terraform/`: the same resources deployed with
  Terraform, with its own README.
- `alternatives/terraform-state-bootstrap/`: a shared Terraform
  state bucket, one per account and region, for the Terraform route
  (and any other Terraform project) if you don't have one already.
- `SCORECARD.md`: the full STORE-TEST result from the first live
  deployment, including the caveats and the two AWS behaviors that
  bit us on the way.

## Deploy

The Terraform route's steps are in
[`alternatives/terraform/README.md`](alternatives/terraform/README.md).

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

## License

MIT. See `LICENSE`.
