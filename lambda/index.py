"""Front door for the S3 handoff blob store (test).

Stores and returns ciphertext blobs by label. This function never
decrypts anything: it moves opaque text between the caller and one
S3 bucket, behind a bearer-token check.

Routes (all require "Authorization: Bearer <token>"):
  GET    /blobs/<label>  -> 200 blob text, or 404
  PUT    /blobs/<label>  -> store/replace blob from request body
  DELETE /blobs/<label>  -> delete blob (200 even if already gone)

Only the SHA-256 digest of the token is configured here; each
presented token is hashed and compared in constant time.
"""

import base64
import hashlib
import hmac
import os
import re

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3")

BUCKET = os.environ["BLOB_BUCKET"]
PREFIX = os.environ.get("BLOB_PREFIX", "blobs/")
TOKEN_SHA256 = os.environ["BEARER_TOKEN_SHA256"]

LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ROUTE_RE = re.compile(r"^/blobs/([^/]+)$")


def _authorized(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    # The auth scheme is case-insensitive (RFC 7235): "bearer" works.
    auth = headers.get("authorization", "")
    if not isinstance(auth, str):
        return False
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    digest = hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()
    return hmac.compare_digest(digest, TOKEN_SHA256)


def _reply(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "text/plain"}, "body": body}


def handler(event, _context):
    if not _authorized(event):
        return _reply(401, "unauthorized")

    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "")

    # Health route: connection checkers and operators probe the
    # root. Authorized callers get a plain 200; the blob routes
    # below remain the only way to reach S3.
    if method == "GET" and http.get("path", "") in ("/", ""):
        return _reply(200, "ok")

    route = ROUTE_RE.match(http.get("path", ""))
    if not route:
        return _reply(404, "not found")

    label = route.group(1)
    if not LABEL_RE.match(label):
        return _reply(400, "invalid label")
    key = PREFIX + label

    if method == "GET":
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
        except ClientError as err:
            # No s3:ListBucket, so S3 says AccessDenied for a missing
            # key; to the caller both mean no such blob.
            if err.response["Error"]["Code"] in ("NoSuchKey", "404", "AccessDenied", "403"):
                return _reply(404, "not found")
            raise
        return _reply(200, obj["Body"].read().decode("utf-8"))

    if method == "PUT":
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            try:
                body = base64.b64decode(body, validate=True).decode("utf-8")
            except ValueError:  # bad base64 or non-UTF-8 bytes
                return _reply(400, "body must be UTF-8 text")
        # U+FFFD: a text body's non-UTF-8 bytes arrive replaced by it.
        if "�" in body:
            return _reply(400, "body must be UTF-8 text")
        try:
            data = body.encode("utf-8")
        except UnicodeEncodeError:  # e.g. a lone surrogate in a text body
            return _reply(400, "body must be UTF-8 text")
        s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=data,
            ContentType="text/plain",
        )
        return _reply(200, "stored")

    if method == "DELETE":
        s3.delete_object(Bucket=BUCKET, Key=key)
        return _reply(200, "deleted")

    return _reply(405, "method not allowed")
