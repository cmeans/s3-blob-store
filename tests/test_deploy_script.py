"""Tests for deploy.sh and the one-paste line that runs it.

deploy.sh runs under bash against a fake `aws` (first on PATH; python3 is
the real one). The fake keeps a pretend stack in files: its status and the
token digest its function holds. So every state and failure path runs
without AWS. The script writes its token file to /tmp; the tests run a copy
whose token path points into the test's own directory instead, so no test
(and no mutant of the script) can touch a real token file. Run with:

    python3 -m unittest discover -s tests -v
"""
import hashlib
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OLD = "0" * 64
TOKEN_LINE = 'token_file="/tmp/$stack-token.txt"'

FAKE_AWS = r"""#!/bin/bash
# A pretend CloudFormation and Lambda. State: $FAKE_DIR/status ("none" = no
# stack) and $FAKE_DIR/digest (the function's BEARER_TOKEN_SHA256).
# FAKE_DESCRIBE=error fails every describe-stacks; =after fails it once a
# deploy has run. FAKE_LAMBDA=error fails the function read. FAKE_WARN=1
# adds a warning on stderr to every call. FAKE_DEPLOY picks what deploy
# does. The function is found only by the template's own names.
printf '%s\n' "$*" >> "$FAKE_DIR/log"
[ -n "${FAKE_WARN:-}" ] && echo "WARNING: a newer CLI is available" >&2
status=$(cat "$FAKE_DIR/status")
new=$(printf '%s\n' "$@" | sed -n 's/^BearerTokenSha256=//p')
case "$1 $2" in
  "cloudformation describe-stacks")
    if [ "${FAKE_DESCRIBE:-}" = error ] || { [ "${FAKE_DESCRIBE:-}" = after ] && [ -e "$FAKE_DIR/deployed" ]; }; then
      echo "An error occurred (Throttling) when calling the DescribeStacks operation: Rate exceeded" >&2; exit 254
    fi
    [ "${FAKE_DESCRIBE:-}" = empty ] && exit 0
    [ "${FAKE_DESCRIBE:-}" = odd ] && { echo "Stack with id x does not exist" >&2; exit 0; }
    if [ "$status" = none ]; then
      echo "An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id x does not exist" >&2; exit 254
    fi
    case "$*" in
      *StackStatus*)
        echo "$status"
        # late-execute: the update shows only after the first read.
        if [ -e "$FAKE_DIR/late" ]; then rm "$FAKE_DIR/late"; echo UPDATE_IN_PROGRESS > "$FAKE_DIR/status"; fi ;;
      *Outputs*) echo "OUTPUTS-TABLE" ;;
    esac
    exit 0 ;;
  "cloudformation describe-stack-resource")
    [ "$status" = none ] && exit 254
    case "$*" in *"--logical-resource-id BlobFunction "*) ;; *) echo "no such resource" >&2; exit 254 ;; esac
    echo "fake-function"; exit 0 ;;
  "lambda get-function-configuration")
    [ "${FAKE_LAMBDA:-}" = error ] && { echo "AccessDenied" >&2; exit 254; }
    [ "${FAKE_LAMBDA:-}" = none ] && { echo None; exit 0; }
    if [ "$(cat "$FAKE_DIR/digest")" = missing ]; then
      echo "An error occurred (ResourceNotFoundException) when calling the GetFunctionConfiguration operation: Function not found" >&2; exit 254
    fi
    case "$*" in *"--function-name fake-function "*"Environment.Variables.BEARER_TOKEN_SHA256 "*) cat "$FAKE_DIR/digest" ;; *) echo None ;; esac
    exit 0 ;;
  "cloudformation deploy")
    touch "$FAKE_DIR/deployed"
    case "${FAKE_DEPLOY:-ok}" in
      ok)
        if [ "$status" = none ] || [ "$status" = REVIEW_IN_PROGRESS ]; then echo CREATE_COMPLETE; else echo UPDATE_COMPLETE; fi > "$FAKE_DIR/status"
        echo "$new" > "$FAKE_DIR/digest"; exit 0 ;;
      fail-early) [ -n "${FAKE_DESCRIBE_STATUS:-}" ] && echo "$FAKE_DESCRIBE_STATUS" > "$FAKE_DIR/status"; exit 255 ;;
      late-execute) touch "$FAKE_DIR/late"; exit 255 ;;
      changeset-failed) echo REVIEW_IN_PROGRESS > "$FAKE_DIR/status"; exit 255 ;;
      fail-create) echo "${FAKE_CREATE_END:-ROLLBACK_COMPLETE}" > "$FAKE_DIR/status"; echo missing > "$FAKE_DIR/digest"; exit 255 ;;
      # delayed: fails now; FAKE_DELAY seconds later the stack becomes
      # FAKE_LATER (and, with FAKE_LATER_NEW=1, holds the new digest).
      delayed)
        ( sleep "${FAKE_DELAY:-1}"; echo "${FAKE_LATER:-UPDATE_IN_PROGRESS}" > "$FAKE_DIR/status"
          [ -n "${FAKE_LATER_NEW:-}" ] && echo "$new" > "$FAKE_DIR/digest" ) >/dev/null 2>&1 &
        exit 255 ;;
      fail-update) echo UPDATE_ROLLBACK_COMPLETE > "$FAKE_DIR/status"; exit 255 ;;
      interrupted) echo UPDATE_IN_PROGRESS > "$FAKE_DIR/status"; exit 130 ;;
      applied-then-error) echo UPDATE_COMPLETE > "$FAKE_DIR/status"; echo "$new" > "$FAKE_DIR/digest"; exit 255 ;;
      hang) echo UPDATE_IN_PROGRESS > "$FAKE_DIR/status"; touch "$FAKE_DIR/waiting"; sleep 30; exit 0 ;;
    esac ;;
esac
exit 0
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="deploy-sh-"))
        self.addCleanup(shutil.rmtree, self.dir)
        (self.dir / "bin").mkdir()
        aws = self.dir / "bin" / "aws"
        aws.write_text(FAKE_AWS, encoding="utf-8")
        aws.chmod(0o755)
        self.work = self.dir / "work"
        self.work.mkdir()
        (self.work / "template.yaml").write_text("x\n", encoding="utf-8")
        script = (REPO / "deploy.sh").read_text(encoding="utf-8")
        self.assertEqual(script.count(TOKEN_LINE), 1, "deploy.sh's token path moved")
        self.tokens = self.dir / "tokens"
        self.tokens.mkdir()
        (self.work / "deploy.sh").write_text(
            script.replace(TOKEN_LINE, f'token_file="{self.tokens}/$stack-token.txt"'), encoding="utf-8")
        self.suffix = "t" + uuid.uuid4().hex[:12]
        self.token_file = self.tokens / f"s3-blob-store-{self.suffix}-token.txt"
        self.set_stack("none")

    def set_stack(self, status, digest=OLD):
        (self.dir / "status").write_text(status + "\n", encoding="utf-8")
        (self.dir / "digest").write_text(digest + "\n", encoding="utf-8")
        for f in ("log", "deployed"):
            (self.dir / f).unlink(missing_ok=True)

    def env(self, **extra):
        full = {"PATH": f"{self.dir / 'bin'}:{os.environ['PATH']}", "HOME": str(self.dir),
                "FAKE_DIR": str(self.dir), "DEPLOY_SH_SETTLE_SECONDS": "0"}
        full.update(extra)
        return full

    def run_script(self, *args, suffix=True, **env):
        argv = ["bash", "deploy.sh", *args] + (["--suffix", self.suffix] if suffix else [])
        r = subprocess.run(argv, cwd=self.work, env=self.env(**env),
                           capture_output=True, text=True, check=False, timeout=60)
        return r, self.log()

    def log(self):
        p = self.dir / "log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def deploys(self, log):
        return [ln for ln in log.splitlines() if ln.startswith("cloudformation deploy")]

    def token(self):
        return self.token_file.read_text(encoding="utf-8")

    def assert_token_file(self):
        self.assertRegex(self.token(), r"^[0-9a-f]{64}$")


class DeployTest(Base):
    def test_fresh_deploy(self):
        r, log = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OUTPUTS-TABLE", r.stdout)
        self.assertIn("Deployed.", r.stdout)
        token = self.token()
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertEqual(stat.S_IMODE(self.token_file.stat().st_mode), 0o600)
        (deploy,) = self.deploys(log)
        self.assertIn(f"BearerTokenSha256={hashlib.sha256(token.encode()).hexdigest()}", deploy)
        self.assertIn(f"NameSuffix={self.suffix}", deploy)
        self.assertIn(f"--stack-name s3-blob-store-{self.suffix}", deploy)
        self.assertNotIn(token, log + r.stdout + r.stderr)

    def test_describe_error_stops_without_deploying(self):
        self.set_stack("CREATE_COMPLETE")
        r, log = self.run_script("--rotate", FAKE_DESCRIBE="error")
        self.assertEqual(r.returncode, 3)
        self.assertIn("couldn't read the state", r.stdout)
        self.assertEqual(self.deploys(log), [])
        self.assertFalse(self.token_file.exists())

    def test_empty_status_reply_stops(self):
        self.set_stack("UPDATE_COMPLETE")
        r, log = self.run_script("--rotate", FAKE_DESCRIBE="empty")
        self.assertEqual(r.returncode, 3)
        self.assertIn("couldn't read the state", r.stdout)
        self.assertEqual(self.deploys(log), [])

    def test_does_not_exist_on_a_successful_call_is_not_believed(self):
        self.set_stack("UPDATE_COMPLETE")
        r, log = self.run_script(FAKE_DESCRIBE="odd")
        self.assertEqual(r.returncode, 3)
        self.assertIn("couldn't read the state", r.stdout)
        self.assertEqual(self.deploys(log), [])

    def test_existing_stack_stops_without_rotate_and_names_the_right_command(self):
        for env in ({}, {"ROTATE": "1"}):
            with self.subTest(env=env):
                self.set_stack("UPDATE_COMPLETE")
                r, log = self.run_script(**env)
                self.assertEqual(r.returncode, 3)
                self.assertIn("already exists", r.stdout)
                self.assertIn("401", r.stdout)
                self.assertIn(f"bash deploy.sh --suffix {self.suffix} --rotate", r.stdout)
                self.assertEqual(self.deploys(log), [])
                self.assertFalse(self.token_file.exists())

    def test_default_store_hint_has_no_suffix(self):
        # Stops at the state check, before the default token path is touched.
        self.set_stack("UPDATE_COMPLETE")
        r, log = self.run_script(suffix=False)
        self.assertEqual(r.returncode, 3)
        self.assertIn("To rotate on purpose: bash deploy.sh --rotate\n", r.stdout)
        self.assertIn("--stack-name s3-blob-store ", log)
        self.assertEqual(self.deploys(log), [])

    def test_rotate_and_check_together_stop(self):
        r, log = self.run_script("--rotate", "--check")
        self.assertEqual(r.returncode, 3)
        self.assertIn("not both", r.stdout)
        self.assertEqual(log, "")

    def test_rotate(self):
        for status in ("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
                       "IMPORT_COMPLETE", "IMPORT_ROLLBACK_COMPLETE"):
            with self.subTest(status=status):
                self.set_stack(status)
                self.token_file.unlink(missing_ok=True)
                r, log = self.run_script("--rotate")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("Rotated.", r.stdout)
                self.assertIn("no longer works", r.stdout)
                self.assertEqual(len(self.deploys(log)), 1)
                self.assert_token_file()

    def test_cli_warning_on_stderr_is_not_a_status(self):
        self.set_stack("UPDATE_COMPLETE")
        r, _ = self.run_script("--rotate", FAKE_WARN="1")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("Rotated.", r.stdout)

    def test_empty_or_repeated_suffix_stops(self):
        self.set_stack("UPDATE_COMPLETE")
        for args, text in ((["--suffix", ""], "needs a value"),
                           (["--suffix", "a", "--suffix", "b"], "once")):
            with self.subTest(args=args):
                r, log = self.run_script("--rotate", *args, suffix=False)
                self.assertEqual(r.returncode, 3)
                self.assertIn(text, r.stdout)
                self.assertEqual(log, "")

    def test_rotate_with_no_store_stops(self):
        for status in ("none", "REVIEW_IN_PROGRESS"):
            with self.subTest(status=status):
                self.set_stack(status)
                r, log = self.run_script("--rotate")
                self.assertEqual(r.returncode, 3)
                self.assertIn("no store to rotate", r.stdout)
                self.assertEqual(self.deploys(log), [])

    def test_unsettled_or_failed_states_stop(self):
        for status, text in (("ROLLBACK_COMPLETE", "failed first deploy"),
                             ("DELETE_FAILED", "DELETE_FAILED"),
                             ("UPDATE_IN_PROGRESS", "busy"),
                             ("UPDATE_ROLLBACK_FAILED", "doesn't handle")):
            with self.subTest(status=status):
                self.set_stack(status)
                r, log = self.run_script("--rotate")
                self.assertEqual(r.returncode, 3)
                self.assertIn(text, r.stdout)
                self.assertEqual(self.deploys(log), [])

    def test_review_in_progress_is_deployed_over(self):
        self.set_stack("REVIEW_IN_PROGRESS")
        r, log = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(self.deploys(log)), 1)

    def test_existing_token_file_is_never_overwritten(self):
        self.token_file.write_text("unsaved", encoding="utf-8")
        r, log = self.run_script()
        self.assertEqual(r.returncode, 3)
        self.assertIn("already exists", r.stdout)
        self.assertIn(f"bash deploy.sh --suffix {self.suffix} --check", r.stdout)
        self.assertEqual(self.token(), "unsaved")
        self.assertEqual(self.deploys(log), [])

    def test_link_or_directory_at_the_token_path_stops(self):
        target = self.dir / "elsewhere"
        self.token_file.symlink_to(target)
        r, log = self.run_script()
        self.assertEqual(r.returncode, 3)
        self.assertIn("isn't a regular file", r.stdout)
        self.assertFalse(target.exists())
        self.assertEqual(self.deploys(log), [])
        r, _ = self.run_script("--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("isn't a regular file", r.stdout)
        self.token_file.unlink()
        self.token_file.mkdir()
        self.addCleanup(lambda: self.token_file.is_dir() and self.token_file.rmdir())
        r, log = self.run_script()
        self.assertEqual(r.returncode, 3)
        self.assertIn("isn't a regular file", r.stdout)

    def test_bad_options_suffix_and_missing_template_stop(self):
        for args, text in ((["--bogus"], "unknown option"), (["--suffix"], "needs a value")):
            with self.subTest(args=args):
                r, _ = self.run_script(*args, suffix=False)
                self.assertEqual(r.returncode, 3)
                self.assertIn(text, r.stdout)
        r, log = self.run_script("--suffix", "Bad_Suffix", suffix=False)
        self.assertEqual(r.returncode, 3)
        self.assertIn("suffix must be", r.stdout)
        self.assertEqual(log, "")
        (self.work / "template.yaml").unlink()
        r, log = self.run_script()
        self.assertEqual(r.returncode, 3)
        self.assertIn("no template.yaml", r.stdout)
        self.assertEqual(self.deploys(log), [])


class FailedDeployTest(Base):
    def test_failed_first_deploy_removes_the_token_file(self):
        for end in ("ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "DELETE_COMPLETE"):
            with self.subTest(end=end):
                self.set_stack("none")
                self.token_file.unlink(missing_ok=True)
                r, _ = self.run_script(FAKE_DEPLOY="fail-create", FAKE_CREATE_END=end)
                self.assertEqual(r.returncode, 1)
                self.assertIn("no store was created", r.stdout)
                self.assertFalse(self.token_file.exists())

    def test_change_set_that_never_ran_removes_the_token_file(self):
        r, _ = self.run_script(FAKE_DEPLOY="changeset-failed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no store was created", r.stdout)
        self.assertFalse(self.token_file.exists())

    def test_failed_rotation_keeps_the_old_token_and_removes_the_new_file(self):
        for mode in ("fail-update", "fail-early"):
            with self.subTest(mode=mode):
                self.set_stack("UPDATE_COMPLETE")
                self.token_file.unlink(missing_ok=True)
                r, _ = self.run_script("--rotate", FAKE_DEPLOY=mode)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("still runs the previous token", r.stdout)
                self.assertFalse(self.token_file.exists())

    def test_failed_rotation_on_a_stack_that_needs_repair_says_so(self):
        self.set_stack("UPDATE_COMPLETE")
        r, _ = self.run_script("--rotate", FAKE_DEPLOY="fail-early",
                               FAKE_DESCRIBE_STATUS="UPDATE_ROLLBACK_FAILED")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("needs repair", r.stdout)

    def test_error_after_the_store_took_the_new_token_counts_as_deployed(self):
        self.set_stack("UPDATE_COMPLETE")
        r, _ = self.run_script("--rotate", FAKE_DEPLOY="applied-then-error")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("now runs the new token", r.stdout)
        self.assertIn("Rotated.", r.stdout)
        self.assert_token_file()

    def test_unknown_outcome_keeps_the_file_and_never_says_it_is_live(self):
        cases = (("interrupted", {}),
                 ("fail-early", {"FAKE_DESCRIBE": "after"}),
                 ("fail-early", {"FAKE_LAMBDA": "error"}),
                 ("fail-early", {"FAKE_LAMBDA": "none"}))
        for mode, env in cases:
            with self.subTest(mode=mode, env=env):
                self.set_stack("UPDATE_COMPLETE")
                self.token_file.unlink(missing_ok=True)
                r, _ = self.run_script("--rotate", FAKE_DEPLOY=mode, **env)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("KEPT", r.stdout)
                self.assertIn(f"bash deploy.sh --suffix {self.suffix} --check", r.stdout)
                self.assertNotIn("save it", r.stdout.lower().replace("(save it)", ""))
                if env.get("FAKE_DESCRIBE") == "after":
                    self.assertIn("Rate exceeded", r.stdout)
                if env.get("FAKE_LAMBDA") == "error":
                    self.assertIn("AccessDenied", r.stdout)
                self.assert_token_file()

    def test_update_that_shows_late_keeps_the_file(self):
        self.set_stack("UPDATE_COMPLETE")
        r, _ = self.run_script("--rotate", FAKE_DEPLOY="late-execute")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("changed while the script waited", r.stdout)
        self.assertIn("KEPT", r.stdout)
        self.assert_token_file()

    def test_the_pause_sees_an_update_that_shows_late(self):
        cases = ((dict(FAKE_LATER="UPDATE_IN_PROGRESS"), 2, "changed while the script waited"),
                 (dict(FAKE_LATER="DELETE_COMPLETE"), 2, "changed while the script waited"),
                 (dict(FAKE_LATER="UPDATE_COMPLETE", FAKE_LATER_NEW="1"), 0, "now runs the new token"))
        for env, rc, text in cases:
            with self.subTest(env=env):
                self.set_stack("UPDATE_COMPLETE")
                self.token_file.unlink(missing_ok=True)
                r, _ = self.run_script("--rotate", FAKE_DEPLOY="delayed", FAKE_DELAY="1",
                                       DEPLOY_SH_SETTLE_SECONDS="3", **env)
                self.assertEqual(r.returncode, rc, r.stdout)
                self.assertIn(text, r.stdout)
                self.assertIn("Checking again in 3s", r.stdout)
                self.assert_token_file()

    def test_default_pause_is_20_seconds_and_bad_values_fall_back(self):
        script = (REPO / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn('settle=${DEPLOY_SH_SETTLE_SECONDS:-20}\n[[ "$settle" =~ ^[0-9]+$ ]] || settle=20', script)

    def test_ctrl_c_during_the_pause_keeps_the_file(self):
        # "abc" isn't whole seconds, so the pause falls back to 20 s; the
        # stack would show the update after 5 s. Ctrl-C comes first.
        self.set_stack("UPDATE_COMPLETE")
        p = subprocess.Popen(["bash", "deploy.sh", "--rotate", "--suffix", self.suffix], cwd=self.work,
                             env=self.env(FAKE_DEPLOY="delayed", FAKE_DELAY="5",
                                          DEPLOY_SH_SETTLE_SECONDS="abc"),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             start_new_session=True)
        out = []
        start = time.monotonic()
        try:
            for line in p.stdout:
                out.append(line)
                if line.startswith("Checking again in 20s"):
                    os.killpg(p.pid, signal.SIGINT)
                    break
            out.append(p.communicate(timeout=20)[0])
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.communicate()
        text = "".join(out)
        self.assertEqual(p.returncode, 2, text)
        self.assertIn("interrupted while waiting", text)
        self.assertIn("KEPT", text)
        self.assertLess(time.monotonic() - start, 15)
        self.assert_token_file()

    def test_unreadable_state_after_a_failed_first_deploy_keeps_the_file(self):
        r, _ = self.run_script(FAKE_DEPLOY="fail-early", FAKE_DESCRIBE="after")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("KEPT", r.stdout)
        self.assert_token_file()

    def test_real_ctrl_c_mid_deploy_keeps_the_file(self):
        self.set_stack("UPDATE_COMPLETE")
        p = subprocess.Popen(["bash", "deploy.sh", "--rotate", "--suffix", self.suffix],
                             cwd=self.work, env=self.env(FAKE_DEPLOY="hang"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            while not (self.dir / "waiting").exists():
                self.assertLess(time.monotonic(), deadline, "fake deploy never started")
                time.sleep(0.05)
            os.killpg(p.pid, signal.SIGINT)
            out, _ = p.communicate(timeout=20)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.communicate()
        self.assertEqual(p.returncode, 2, out)
        self.assertIn("Interrupted", out)
        self.assertIn("KEPT", out)
        self.assert_token_file()


class CheckTest(Base):
    def write_token(self, token):
        self.token_file.write_text(token, encoding="utf-8")
        return hashlib.sha256(token.encode()).hexdigest()

    def test_live(self):
        self.set_stack("UPDATE_COMPLETE", self.write_token("a" * 64))
        r, log = self.run_script("--check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("LIVE", r.stdout)
        self.assertIn("OUTPUTS-TABLE", r.stdout)
        self.assertEqual(self.deploys(log), [])

    def test_live_on_a_stack_that_needs_repair(self):
        digest = self.write_token("a" * 64)
        for status in ("UPDATE_ROLLBACK_FAILED", "ROLLBACK_FAILED", "DELETE_FAILED"):
            with self.subTest(status=status):
                self.set_stack(status, digest)
                r, _ = self.run_script("--check")
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertIn("LIVE", r.stdout)
                self.assertIn("needs repair", r.stdout)

    def test_failed_stack_whose_function_is_gone_is_not_live(self):
        self.write_token("a" * 64)
        for status in ("DELETE_FAILED", "ROLLBACK_FAILED"):
            with self.subTest(status=status):
                self.set_stack(status, "missing")
                r, _ = self.run_script("--check")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("NOT LIVE", r.stdout)

    def test_not_live(self):
        self.write_token("a" * 64)
        for status in ("UPDATE_COMPLETE", "none", "REVIEW_IN_PROGRESS", "ROLLBACK_COMPLETE", "DELETE_COMPLETE"):
            with self.subTest(status=status):
                self.set_stack(status, OLD)
                r, _ = self.run_script("--check")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("NOT LIVE", r.stdout)
                self.assertTrue(self.token_file.exists())

    def test_cant_tell(self):
        digest = self.write_token("a" * 64)
        for status, env in (("UPDATE_IN_PROGRESS", {}), ("UPDATE_COMPLETE", {"FAKE_LAMBDA": "error"}),
                            ("UPDATE_COMPLETE", {"FAKE_DESCRIBE": "error"})):
            with self.subTest(status=status, env=env):
                self.set_stack(status, digest)
                r, _ = self.run_script("--check", **env)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("Can't tell", r.stdout)
        self.set_stack("UPDATE_IN_PROGRESS", digest)
        r, _ = self.run_script("--check")
        self.assertIn(f"bash deploy.sh --suffix {self.suffix} --check again", r.stdout)

    def test_unreadable_token_file(self):
        self.set_stack("UPDATE_COMPLETE", self.write_token("a" * 64))
        self.token_file.chmod(0)
        if os.access(self.token_file, os.R_OK):
            self.skipTest("running as root")
        r, _ = self.run_script("--check")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("couldn't read", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_no_file(self):
        r, _ = self.run_script("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No token file", r.stdout)


class TemplateNamesTest(unittest.TestCase):
    def test_the_names_deploy_sh_reads_are_the_templates(self):
        template = (REPO / "template.yaml").read_text(encoding="utf-8")
        script = (REPO / "deploy.sh").read_text(encoding="utf-8")
        function = template[template.index("\n  BlobFunction:\n"):]
        function = function[:function.index("\n      Code:")]
        self.assertIn("BEARER_TOKEN_SHA256: !Ref BearerTokenSha256", function)
        self.assertIn("--logical-resource-id BlobFunction", script)
        self.assertIn('"Environment.Variables.BEARER_TOKEN_SHA256"', script)


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
