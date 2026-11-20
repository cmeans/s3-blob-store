# SETUP — wiring a deployed s3-blob-store into daily use

Deploying (see `SKILL.md`) leaves you with exactly two things:

- a **FunctionUrl**, e.g.
  `https://<random>.lambda-url.<region>.on.aws/` — note the
  trailing slash; blobs live at `<FunctionUrl>blobs/<label>`
- a **bearer token**, 64 hex characters, which should now be in
  a password manager or your assistant platform's secure
  credential store — and nowhere else.

This file covers registering those two with your assistant,
the exact API contract, and day-to-day operation.

## Register the store with your assistant

The assistant needs the URL and the token, and it must hold
the token the way it holds any credential: in its secure
store, swapped into requests at send time, never in chat,
notes, memory, or plaintext config.

- **Muse:** register a custom connection for the store —
  provider name of your choosing (e.g. `s3-blob-store`), API
  host = the FunctionUrl host, authentication = bearer token
  in the `Authorization` header. **You** enter the token on
  the secure entry page, pasted from your password manager; it
  is stored as a credential the platform swaps in at egress to
  that one host, so the token never sits in the assistant's
  context or working files. (If the assistant deployed the
  store and downloaded the token file itself, see `SKILL.md`
  Path B step 4: it hands you the token, it doesn't read it.) The first real call (the
  health check below) is what verifies the registration.
- **Other assistants:** the equivalent — a secret-store entry
  or environment-scoped credential bound to that host, used
  as `Authorization: Bearer <token>`. If your assistant has
  no way to hold a token without putting it in plaintext
  config or chat, stop and use the store manually with curl
  (below) until it does. A credential store whose key is
  pasted around in plaintext has defeated its own purpose.

Keep the token in the password manager as the human-side
backup copy regardless of which assistant registration you
use. If you run more than one deployment (say a scratch stack
beside the real one), register them as separate connections
with distinct names — tokens are per-stack and not
interchangeable.

## Verify the wiring

Health check, with `URL` set to the FunctionUrl and `TOKEN` set
without typing it (see Manual use, below):

```
curl -s -H "Authorization: Bearer $TOKEN" "$URL"
```

Expected: `ok` (HTTP 200). Without the header, the same
request returns HTTP 401 `unauthorized` — check that too;
it proves the auth gate, not just the function, is alive.

## API contract

All routes are on the FunctionUrl, and every request needs
the bearer token: without it (or with a wrong one), any path
and any method gets 401 before anything else is checked.

| Route | Method | Success | Notes |
|---|---|---|---|
| `/` | GET | 200, body `ok` | health |
| `/blobs/<label>` | PUT | 200, body `stored` | request body is the blob (UTF-8 text, see Bodies); replaces any existing blob at the label |
| `/blobs/<label>` | GET | 200, the blob | 404 when the label has no blob |
| `/blobs/<label>` | DELETE | 200, body `deleted` | answers `deleted` whether or not the label existed, so it proves nothing about what was there; GET after delete returns 404 |

Errors: 401 `unauthorized` (missing or wrong token), 404
`not found` (no blob at the label, or no such route), 405
`method not allowed`, 400 (label fails validation). A body
that isn't UTF-8 currently fails with a server error rather
than a 400 ([#12](https://github.com/cmeans/s3-blob-store/issues/12)).

**Labels:** letters, digits, dot, dash, underscore, starting
with a letter or digit; at most 128 characters. Colons and slashes are not allowed — if your
naming scheme uses them (e.g. `agent:purpose:key`), map them
before storing (`.` for `:` is the convention the reference
deployment uses) and keep the mapping in one place.

**Bodies:** UTF-8 text, stored and returned as is. That suits
ASCII-armored ciphertext (for example `age -a` output); encode
binary as base64 before storing it. The store is designed for
credential-sized payloads;
round trips are test-verified to 64,000 characters (see
`SCORECARD.md`). It is not a file store — keep blobs small.

**Semantics worth knowing:**

- PUT replaces unconditionally; there is no versioning
  (the bucket has versioning off) and no history. If you
  need the old value, fetch it before you replace it.
- DELETE removes the blob from the API immediately. AWS-side
  internal retention after a delete is not observable from
  outside; do not treat delete as a forensic purge.
- There is deliberately **no list operation**. The front
  door cannot enumerate the bucket, so a caller must already
  know a label to read it. Keep your own index of labels
  somewhere else (the reference deployment keeps a registry
  file beside its tooling).

## Manual use (curl)

Set `URL` to the FunctionUrl and `TOKEN` without typing the
token on the command line, for example `read -rs TOKEN` and a
paste from your password manager (a typed token stays in your
shell history). `-f` makes curl fail, and write nothing, on a
401 or 404, so an error reply can't be mistaken for a blob.

```
# store (from a file, so the content never appears in shell history as an argument)
curl -sf -X PUT -H "Authorization: Bearer $TOKEN" --data-binary @blob.txt "$URL"blobs/<label>

# fetch (to a file; the same reasoning)
curl -sf -H "Authorization: Bearer $TOKEN" "$URL"blobs/<label> -o blob.txt

# delete
curl -sf -X DELETE -H "Authorization: Bearer $TOKEN" "$URL"blobs/<label>
```

## Rotation

In CloudShell, in a directory with the release's `template.yaml`
and `deploy.sh`, run `ROTATE=1 bash deploy.sh` (with
`NAME_SUFFIX=<x>` for a suffixed store; SKILL.md, Isolation by
name). Without `ROTATE=1` the script stops rather than rotate. It generates a fresh token and digest and updates
the stack in place; the FunctionUrl does not change. Then:

1. Put the new token in the password manager.
2. Update the assistant's registration with the new token
   (replace the stored credential).
3. Verify the health check with the new token, and confirm
   the old token now gets 401.
4. Delete the token file copy as usual.

Rotate immediately if a token may have leaked. A leaked
token gives full read/write/delete over that one bucket —
it reaches nothing else in the account — and rotation is
the complete remedy.

## Security summary

- The token's SHA-256 digest is the only form of it in AWS
  (a NoEcho stack parameter). AWS never holds the token.
- The Lambda's IAM role is the only identity the stack grants
  access to the bucket, scoped to that bucket. The account's
  administrators (and root) can still reach it through S3
  itself, so protect those logins too. The bucket blocks all
  public access and denies insecure transport.
- The Function URL itself is public (it has to be, for your
  assistant to reach it from anywhere) — the token check is
  the gate, which is why token handling in `SKILL.md` is
  strict: never echoed, never chatted, file-to-password-
  manager with the file deleted.
- Losing the token without a password-manager copy means
  rotating (above) — there is no recovery path, by design.
