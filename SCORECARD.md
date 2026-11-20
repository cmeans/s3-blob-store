# STORE-TEST scorecard — Amazon S3 + Lambda Function URL

Tested 2026-11-20 in the author's own AWS account (us-east-2),
following the STORE-TEST protocol from the `encrypted-credential-handoff`
tool in muse-field-kit: dummy age-encrypted blobs only, scratch label
`store-test`, every blob verified after fetch with the handoff check
tool, all test material deleted afterwards.

| Check | Score | Evidence in one line |
| --- | --- | --- |
| Private | Pass | Bucket blocks all public access with ACLs disabled; unauthenticated requests get 401 before S3; only the scoped Lambda role can read objects |
| Unattended fetch | Pass | Fetched in a fresh agent session straight to a file and checked there; blob never passed through the agent's context; one approval prompt on the very first call, none after |
| Write, replace, delete | Pass | All three done through the agent's connection; replace confirmed by SHA-256 change |
| Round trip (largest passing size: 64,000) | Pass | 400 / 2,700 / 8,000 / 64,000 characters all byte-identical and check-verified; a missing label returns a distinct 404 |
| History after delete | Partial | Versioning off and post-delete fetch is 404 with nothing recoverable through the connection, but AWS-internal backup retention is unobservable from outside |
| Store credential scope | Pass | Bearer token reaches only this one Lambda host; no token or key file sits on the agent's machine; the Lambda role is limited to Get/Put/Delete on one bucket prefix |
| Limits and cost | Pass | Single blob bounded by the 6 MB Lambda payload limit; S3 + Lambda at this scale costs effectively nothing per month |

Cleaned up: yes

Verdict: **fits with caveats:** AWS-internal backup retention is
unobservable, and the Lambda is publicly invocable with
authentication enforced in the handler (the design assumption,
verified live).

## Findings worth knowing before you adopt

- A NONE-auth Lambda Function URL needs **two** resource-policy
  permissions, `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`,
  or AWS returns 403 for every request. Terraform's
  `aws_lambda_permission` cannot attach the `FunctionUrlAuthType`
  condition to the second one, so that grant is unconditional at the
  AWS layer and the handler's token check is the real gate.
- S3 answers a `GetObject` for a missing key with **AccessDenied**,
  not `NoSuchKey`, when the caller lacks `s3:ListBucket`. The handler
  maps that to 404 rather than granting list access the store does
  not need.
- Connection checkers probe the root path, so the handler answers
  `GET /` with 200 for authorized callers.
