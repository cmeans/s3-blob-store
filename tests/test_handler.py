"""Tests for lambda/index.py, the Function URL handler.

boto3 is replaced by an in-memory stub, so these run with the standard
library only and never reach AWS. Run with:

    python3 -m unittest discover -s tests -v
"""
import base64
import hashlib
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOKEN = "t" * 64


class _ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _ClientError("AccessDenied")
        return {"Body": _Body(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


def _load_handler(token_digest=None):
    """Load lambda/index.py with a stub boto3; token_digest overrides the configured digest."""
    s3 = _FakeS3()
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda name: s3
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = _ClientError
    botocore = types.ModuleType("botocore")
    botocore.exceptions = exceptions
    saved = {k: sys.modules.get(k) for k in ("boto3", "botocore", "botocore.exceptions")}
    sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.exceptions": exceptions})
    os.environ.update({
        "BLOB_BUCKET": "bucket",
        "BLOB_PREFIX": "blobs/",
        "BEARER_TOKEN_SHA256": token_digest or hashlib.sha256(TOKEN.encode()).hexdigest(),
    })
    try:
        spec = importlib.util.spec_from_file_location("blob_handler", REPO / "lambda" / "index.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return module, s3


def _event(method, path, auth=None, body=None, b64=False):
    headers = {} if auth is None else {"Authorization": auth}
    return {
        "headers": headers,
        "requestContext": {"http": {"method": method, "path": path}},
        "body": body,
        "isBase64Encoded": b64,
    }


class HandlerTest(unittest.TestCase):
    def setUp(self):
        self.mod, self.s3 = _load_handler()

    def call(self, *args, **kwargs):
        r = self.mod.handler(_event(*args, **kwargs), None)
        return r["statusCode"], r["body"]

    def test_scheme_is_case_insensitive(self):
        for scheme in ("Bearer", "bearer", "BEARER", "bEaReR"):
            with self.subTest(scheme=scheme):
                self.assertEqual(self.call("GET", "/", f"{scheme} {TOKEN}"), (200, "ok"))

    def test_bad_auth_is_refused(self):
        for auth in (None, "", "Bearer", "Bearer ", f"Basic {TOKEN}", f"Bearer {TOKEN}x", TOKEN):
            with self.subTest(auth=auth):
                self.assertEqual(self.call("GET", "/", auth), (401, "unauthorized"))

    def test_round_trip_and_delete(self):
        auth = f"Bearer {TOKEN}"
        self.assertEqual(self.call("GET", "/blobs/a", auth), (404, "not found"))
        self.assertEqual(self.call("PUT", "/blobs/a", auth, body="hello"), (200, "stored"))
        self.assertEqual(self.call("GET", "/blobs/a", auth), (200, "hello"))
        self.assertEqual(self.call("DELETE", "/blobs/a", auth), (200, "deleted"))
        self.assertEqual(self.call("GET", "/blobs/a", auth), (404, "not found"))

    def test_base64_utf8_body_is_stored(self):
        auth = f"Bearer {TOKEN}"
        text = "caf" + chr(0xE9)
        body = base64.b64encode(text.encode("utf-8")).decode()
        self.assertEqual(self.call("PUT", "/blobs/u", auth, body=body, b64=True), (200, "stored"))
        self.assertEqual(self.call("GET", "/blobs/u", auth), (200, text))

    def test_body_that_is_not_utf8_is_a_400(self):
        auth = f"Bearer {TOKEN}"
        body = base64.b64encode(bytes([0xFF, 0xFE, 0x80])).decode()
        self.assertEqual(self.call("PUT", "/blobs/b", auth, body=body, b64=True), (400, "body must be UTF-8 text"))
        self.assertNotIn("blobs/b", self.s3.objects)

    def test_body_that_is_not_base64_is_a_400(self):
        auth = f"Bearer {TOKEN}"
        # "aGk=!!!" and a body with a newline decode without validate=True,
        # so these pin the strict decode; the last is non-ASCII text.
        for body in ("not base64!", "aGk=!!!", "aGVs\nbG8=", "caf" + chr(0xE9)):
            with self.subTest(body=body):
                self.assertEqual(self.call("PUT", "/blobs/c", auth, body=body, b64=True), (400, "body must be UTF-8 text"))
                self.assertNotIn("blobs/c", self.s3.objects)

    def test_empty_token_is_refused_even_if_its_digest_is_configured(self):
        mod, _ = _load_handler(token_digest=hashlib.sha256(b"").hexdigest())
        for auth in ("Bearer ", "Bearer"):
            with self.subTest(auth=auth):
                r = mod.handler(_event("GET", "/", auth), None)
                self.assertEqual((r["statusCode"], r["body"]), (401, "unauthorized"))

    def test_odd_authorization_values_are_refused_not_crashed(self):
        for auth in (1, None, "Bearer " + chr(0xD800)):
            with self.subTest(auth=repr(auth)):
                event = _event("GET", "/", None)
                event["headers"] = {"Authorization": auth}
                r = self.mod.handler(event, None)
                self.assertEqual((r["statusCode"], r["body"]), (401, "unauthorized"))

    def test_label_and_route_rules(self):
        auth = f"Bearer {TOKEN}"
        self.assertEqual(self.call("GET", "/blobs/.hidden", auth), (400, "invalid label"))
        self.assertEqual(self.call("GET", "/nope", auth), (404, "not found"))
        self.assertEqual(self.call("POST", "/blobs/a", auth), (405, "method not allowed"))


if __name__ == "__main__":
    unittest.main()
