"""Tests for deploy.sh and the one-paste line that runs it.

deploy.sh runs under bash against a fake `aws` (first on PATH; python3 is
the real one) that keeps a pretend stack's status and last-updated time in
files, so every state and failure path is exercised without AWS. TMPDIR-like
isolation: the script's token file lives in /tmp, so each test uses its own
NAME_SUFFIX, and removes its file afterwards. Run with:

    python3 -m unittest discover -s tests -v
"""
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FAKE_AWS = r"""#!/bin/bash
# A pretend CloudFormation. State: $FAKE_DIR/status ("none" = no stack) and
# $FAKE_DIR/updated. FAKE_DESCRIBE=error makes describe-stacks fail;
# FAKE_DEPLOY picks what deploy does.
printf '%s\n' "$*" >> "$FAKE_DIR/log"
status=$(cat "$FAKE_DIR/status")
case "$1 $2" in
  "cloudformation describe-stacks")
    if [ "${FAKE_DESCRIBE:-}" = error ]; then
      echo "An error occurred (Throttling) when calling the DescribeStacks operation: Rate exceeded" >&2; exit 254
    fi
    if [ "$status" = none ]; then
      echo "An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id x does not exist" >&2; exit 254
    fi
    case "$*" in
      *StackStatus*) echo "$status" ;;
      *LastUpdatedTime*) cat "$FAKE_DIR/updated" ;;
      *Outputs*) echo "OUTPUTS-TABLE" ;;
    esac
    exit 0 ;;
  "cloudformation deploy")
    case "${FAKE_DEPLOY:-ok}" in
      ok)
        [ "$status" = none ] && echo CREATE_COMPLETE > "$FAKE_DIR/status" || { echo UPDATE_COMPLETE > "$FAKE_DIR/status"; date +%s%N > "$FAKE_DIR/updated"; }
        exit 0 ;;
      fail-early) exit 255 ;;
      fail-create) echo ROLLBACK_COMPLETE > "$FAKE_DIR/status"; exit 255 ;;
      fail-update) echo UPDATE_ROLLBACK_COMPLETE > "$FAKE_DIR/status"; date +%s%N > "$FAKE_DIR/updated"; exit 255 ;;
      interrupted) echo UPDATE_IN_PROGRESS > "$FAKE_DIR/status"; date +%s%N > "$FAKE_DIR/updated"; exit 130 ;;
      applied-then-error) echo UPDATE_COMPLETE > "$FAKE_DIR/status"; date +%s%N > "$FAKE_DIR/updated"; exit 255 ;;
    esac ;;
esac
exit 0
"""


class DeployScriptTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="deploy-sh-"))
        self.addCleanup(shutil.rmtree, self.dir)
        (self.dir / "bin").mkdir()
        aws = self.dir / "bin" / "aws"
        aws.write_text(FAKE_AWS, encoding="utf-8")
        aws.chmod(0o755)
        (self.dir / "work").mkdir()
        (self.dir / "work" / "template.yaml").write_text("x\n", encoding="utf-8")
        shutil.copy(REPO / "deploy.sh", self.dir / "work" / "deploy.sh")
        self.suffix = "t" + uuid.uuid4().hex[:12]
        self.token_file = Path(f"/tmp/s3-blob-store-{self.suffix}-token.txt")
        self.addCleanup(lambda: self.token_file.unlink(missing_ok=True))
        self.set_stack("none", "None")

    def set_stack(self, status, updated="2026-11-20T00:00:00Z"):
        (self.dir / "status").write_text(status + "\n", encoding="utf-8")
        (self.dir / "updated").write_text(updated + "\n", encoding="utf-8")

    def run_script(self, **env):
        full = {
            "PATH": f"{self.dir / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.dir),
            "FAKE_DIR": str(self.dir),
            "NAME_SUFFIX": self.suffix,
        }
        full.update(env)
        r = subprocess.run(["bash", "deploy.sh"], cwd=self.dir / "work", env=full,
                           capture_output=True, text=True, check=False)
        log = (self.dir / "log").read_text(encoding="utf-8") if (self.dir / "log").exists() else ""
        return r, log

    def deploys(self, log):
        return [ln for ln in log.splitlines() if ln.startswith("cloudformation deploy")]

    def test_fresh_deploy(self):
        r, log = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OUTPUTS-TABLE", r.stdout)
        token = self.token_file.read_text(encoding="utf-8")
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertEqual(stat.S_IMODE(self.token_file.stat().st_mode), 0o600)
        (deploy,) = self.deploys(log)
        self.assertIn(f"BearerTokenSha256={hashlib.sha256(token.encode()).hexdigest()}", deploy)
        self.assertIn(f"NameSuffix={self.suffix}", deploy)
        self.assertIn(f"--stack-name s3-blob-store-{self.suffix}", deploy)
        self.assertNotIn(token, log + r.stdout + r.stderr)

    def test_describe_error_stops_without_deploying(self):
        self.set_stack("CREATE_COMPLETE")
        r, log = self.run_script(FAKE_DESCRIBE="error", ROTATE="1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("couldn't confirm the state", r.stdout)
        self.assertEqual(self.deploys(log), [])
        self.assertFalse(self.token_file.exists())

    def test_existing_stack_stops_without_rotate(self):
        for value in (None, "yes", "0", ""):
            with self.subTest(ROTATE=value):
                self.set_stack("UPDATE_COMPLETE")
                env = {} if value is None else {"ROTATE": value}
                r, log = self.run_script(**env)
                self.assertEqual(r.returncode, 3)
                self.assertIn("already exists", r.stdout)
                self.assertIn("401", r.stdout)
                self.assertEqual(self.deploys(log), [])
                self.assertFalse(self.token_file.exists())

    def test_rotate_deploys_over_an_existing_stack(self):
        self.set_stack("CREATE_COMPLETE")
        r, log = self.run_script(ROTATE="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.deploys(log)), 1)
        self.assertRegex(self.token_file.read_text(encoding="utf-8"), r"^[0-9a-f]{64}$")

    def test_unsettled_or_failed_states_stop(self):
        for status, text in (("ROLLBACK_COMPLETE", "failed first deploy"),
                             ("DELETE_FAILED", "DELETE_FAILED"),
                             ("UPDATE_IN_PROGRESS", "busy"),
                             ("UPDATE_ROLLBACK_FAILED", "doesn't handle")):
            with self.subTest(status=status):
                self.set_stack(status)
                r, log = self.run_script(ROTATE="1")
                self.assertEqual(r.returncode, 3)
                self.assertIn(text, r.stdout)
                self.assertEqual(self.deploys(log), [])

    def test_existing_token_file_is_never_overwritten(self):
        self.token_file.write_text("unsaved", encoding="utf-8")
        r, log = self.run_script()
        self.assertEqual(r.returncode, 3)
        self.assertIn("already exists", r.stdout)
        self.assertEqual(self.token_file.read_text(encoding="utf-8"), "unsaved")
        self.assertEqual(self.deploys(log), [])

    def test_failed_first_deploy_removes_the_token_file(self):
        r, _ = self.run_script(FAKE_DEPLOY="fail-create")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no store was created", r.stdout)
        self.assertFalse(self.token_file.exists())

    def test_rolled_back_rotation_removes_the_new_token_file(self):
        self.set_stack("UPDATE_COMPLETE")
        r, _ = self.run_script(ROTATE="1", FAKE_DEPLOY="fail-update")
        self.assertEqual(r.returncode, 1)
        self.assertIn("rolled back", r.stdout)
        self.assertFalse(self.token_file.exists())

    def test_rotation_that_failed_before_changing_anything(self):
        self.set_stack("UPDATE_COMPLETE")
        r, _ = self.run_script(ROTATE="1", FAKE_DEPLOY="fail-early")
        self.assertEqual(r.returncode, 1)
        self.assertIn("before changing the stack", r.stdout)
        self.assertFalse(self.token_file.exists())

    def test_interrupted_or_unconfirmed_deploy_keeps_the_token_file(self):
        for mode in ("interrupted", "applied-then-error"):
            with self.subTest(mode=mode):
                self.token_file.unlink(missing_ok=True)
                self.set_stack("UPDATE_COMPLETE")
                r, _ = self.run_script(ROTATE="1", FAKE_DEPLOY=mode)
                self.assertEqual(r.returncode, 2)
                self.assertIn("KEPT", r.stdout)
                self.assertRegex(self.token_file.read_text(encoding="utf-8"), r"^[0-9a-f]{64}$")

    def test_bad_suffix_and_missing_template_stop(self):
        r, log = self.run_script(NAME_SUFFIX="Bad_Suffix")
        self.assertEqual(r.returncode, 3)
        self.assertIn("NAME_SUFFIX must be", r.stdout)
        (self.dir / "work" / "template.yaml").unlink()
        r, log = self.run_script()
        self.assertEqual(r.returncode, 3)
        self.assertIn("no template.yaml", r.stdout)
        self.assertEqual(self.deploys(log), [])


class OnePasteLineTest(unittest.TestCase):
    PREFIX = "curl -fsSL -O https://github.com/cmeans/s3-blob-store/releases/download/<release>/template.yaml"

    def lines(self, doc):
        text = (REPO / doc).read_text(encoding="utf-8")
        return [ln for ln in text.splitlines() if ln.strip().startswith(self.PREFIX)]

    def test_readme_and_skill_show_the_same_line(self):
        readme, skill = self.lines("README.md"), self.lines("SKILL.md")
        self.assertEqual(len(readme), 1)
        self.assertEqual(len(skill), 1)
        self.assertEqual(readme[0].strip(), skill[0].strip())
        self.assertEqual(readme[0], readme[0].rstrip())

    def test_line_fetches_both_assets_from_the_same_release_and_runs_the_script(self):
        line = self.lines("README.md")[0].strip()
        self.assertIn(" -O https://github.com/cmeans/s3-blob-store/releases/download/<release>/deploy.sh", line)
        self.assertTrue(line.endswith("&& bash deploy.sh"))


if __name__ == "__main__":
    unittest.main()
