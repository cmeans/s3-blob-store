"""Tests for tools/build_template.py and its two size guards.

Each test copies the build tool, the skeleton, the handler and the
committed template into a temporary directory and runs the tool there as a
subprocess, so nothing in the repository is touched. Run with:

    python3 -m unittest discover -s tests -v
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILES = ["tools/build_template.py", "template.skeleton.yaml", "lambda/index.py", "template.yaml"]

ZIPFILE_LIMIT = 4096
TEMPLATE_BODY_LIMIT = 51200
TEMPLATE_BODY_WARN = 46080


class BuildTemplateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="build-template-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        for rel in FILES:
            dst = self.tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / rel, dst)

    def run_tool(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(self.tmp / "tools" / "build_template.py"), *args],
            capture_output=True, text=True, check=False,
        )

    def pad_skeleton_to(self, total_bytes):
        """Append a YAML comment so the generated template is total_bytes long."""
        skeleton = self.tmp / "template.skeleton.yaml"
        original = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8")
        skeleton.write_text(original, encoding="utf-8")
        self.assertEqual(self.run_tool().returncode, 0)
        current = len((self.tmp / "template.yaml").read_bytes())
        # The tool joins lines with "\n" and adds one at the end, so an added
        # line "# xxx" costs len("# ") + k + 1 bytes.
        k = total_bytes - current - 3
        self.assertGreaterEqual(k, 0)
        if not original.endswith("\n"):
            original += "\n"
        skeleton.write_text(original + "# " + "x" * k + "\n", encoding="utf-8")

    def test_committed_template_is_current(self):
        r = self.run_tool("--check")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("ok: template.yaml is current", r.stdout)

    def test_stale_template_fails_check(self):
        (self.tmp / "template.yaml").write_text("stale\n", encoding="utf-8")
        r = self.run_tool("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("template.yaml is stale", r.stderr)

    def test_missing_template_fails_check(self):
        (self.tmp / "template.yaml").unlink()
        r = self.run_tool("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("template.yaml is missing", r.stderr)

    def test_template_at_body_limit_builds(self):
        self.pad_skeleton_to(TEMPLATE_BODY_LIMIT)
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len((self.tmp / "template.yaml").read_bytes()), TEMPLATE_BODY_LIMIT)
        self.assertIn("warning: the generated template is 51200 bytes", r.stderr)

    def test_template_over_body_limit_fails_and_writes_nothing(self):
        before = (self.tmp / "template.yaml").read_bytes()
        self.pad_skeleton_to(TEMPLATE_BODY_LIMIT + 1)
        (self.tmp / "template.yaml").write_bytes(before)
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("51201 bytes, over CloudFormation's TemplateBody limit of 51200", r.stderr)
        self.assertEqual((self.tmp / "template.yaml").read_bytes(), before)

    def test_template_over_body_limit_fails_check_too(self):
        self.pad_skeleton_to(TEMPLATE_BODY_LIMIT + 1)
        r = self.run_tool("--check")
        self.assertEqual(r.returncode, 1)
        # The size failure, not the stale-template one that would follow it.
        self.assertIn("51201 bytes, over CloudFormation's TemplateBody limit", r.stderr)
        self.assertNotIn("stale", r.stderr)

    def test_warning_starts_past_threshold(self):
        self.pad_skeleton_to(TEMPLATE_BODY_WARN)
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("TemplateBody limit", r.stderr)
        self.pad_skeleton_to(TEMPLATE_BODY_WARN + 1)
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning: the generated template is 46081 bytes", r.stderr)

    def test_handler_over_zipfile_limit_fails(self):
        handler = self.tmp / "lambda" / "index.py"
        text = handler.read_text(encoding="utf-8")
        handler.write_text(text + "#" * (ZIPFILE_LIMIT - len(text) + 1), encoding="utf-8")
        before = (self.tmp / "template.yaml").read_bytes()
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"{ZIPFILE_LIMIT + 1} characters, over the CloudFormation ZipFile limit", r.stderr)
        self.assertEqual((self.tmp / "template.yaml").read_bytes(), before)

    def test_handler_at_zipfile_limit_builds(self):
        handler = self.tmp / "lambda" / "index.py"
        text = handler.read_text(encoding="utf-8")
        handler.write_text(text + "#" * (ZIPFILE_LIMIT - len(text) - 1) + "\n", encoding="utf-8")
        self.assertEqual(len(handler.read_text(encoding="utf-8")), ZIPFILE_LIMIT)
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
