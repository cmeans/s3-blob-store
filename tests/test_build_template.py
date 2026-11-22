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

    def test_crlf_template_fails_check(self):
        path = self.tmp / "template.yaml"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        r = self.run_tool("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("template.yaml is stale", r.stderr)

    def test_regenerated_template_matches_committed_bytes(self):
        # On Linux this can't catch a switch back to write_text (which writes
        # LF here, CRLF only on Windows); no Windows CI runs it.
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        data = (self.tmp / "template.yaml").read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertEqual(data, (REPO / "template.yaml").read_bytes())

    def test_another_inline_function_before_the_handler_builds(self):
        skeleton = self.tmp / "template.skeleton.yaml"
        text = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8")
        helper = ("  HelperFunction:\n"
                  "    Type: AWS::Lambda::Function\n"
                  "    Properties:\n"
                  "      Code:\n"
                  "        ZipFile: |\n"
                  "          def handler(event, context):\n"
                  "              return None\n")
        self.assertIn("\nResources:\n", text)
        skeleton.write_text(text.replace("\nResources:\n", "\nResources:\n" + helper, 1),
                            encoding="utf-8")
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ZipFile round-trip verified", r.stdout)

    def test_marker_must_follow_zipfile_line(self):
        skeleton = self.tmp / "template.skeleton.yaml"
        lines = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8").splitlines()
        i = next(n for n, ln in enumerate(lines) if "@@HANDLER@@" in ln)
        lines.insert(i, lines[i].replace("@@HANDLER@@", "# comment"))
        skeleton.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("must come right after a `ZipFile: |` line", r.stderr)

    def test_handler_characters_yaml_cannot_carry_are_refused(self):
        handler = self.tmp / "lambda" / "index.py"
        text = (REPO / "lambda" / "index.py").read_text(encoding="utf-8")
        for ch in ("\x1b", "\x00", "\x7f", "\x85", "\u2028", "\u2029", "\r"):
            with self.subTest(ch=f"U+{ord(ch):04X}"):
                handler.write_text(f"# {ch}\n" + text, encoding="utf-8", newline="")
                r = self.run_tool()
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"line 1 has U+{ord(ch):04X}", r.stderr)
        for ok in ("\t", "\u00a0", "\ufeff", "é"):
            with self.subTest(ok=f"U+{ord(ok):04X}"):
                handler.write_text(f"# {ok}\n" + text, encoding="utf-8", newline="")
                r = self.run_tool()
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_noncharacters_are_refused(self):
        handler = self.tmp / "lambda" / "index.py"
        text = (REPO / "lambda" / "index.py").read_text(encoding="utf-8")
        for code in (0xFFFE, 0xFFFF):
            with self.subTest(code=f"U+{code:04X}"):
                handler.write_text(f"# {chr(code)}\n" + text, encoding="utf-8", newline="")
                r = self.run_tool()
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"line 1 has U+{code:04X}", r.stderr)

    def test_skeleton_line_breaks_are_refused(self):
        # str.splitlines would turn these into real line breaks, so text after
        # one in a comment would become live YAML.
        skeleton = self.tmp / "template.skeleton.yaml"
        text = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8")
        for code in (0x2028, 0x2029, 0x85, 0x0C, 0x1D):
            with self.subTest(code=f"U+{code:04X}"):
                skeleton.write_text(f"# note{chr(code)}Transform: X\n" + text, encoding="utf-8")
                r = self.run_tool()
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"template.skeleton.yaml line 1 has U+{code:04X}", r.stderr)

    def test_indented_first_handler_line_is_refused(self):
        # YAML takes the block's indentation from its first non-empty line.
        handler = self.tmp / "lambda" / "index.py"
        text = (REPO / "lambda" / "index.py").read_text(encoding="utf-8")
        for first in ("  # indented comment\n", "      \n", "\t\n"):
            with self.subTest(first=repr(first)):
                handler.write_text(first + text, encoding="utf-8")
                r = self.run_tool()
                self.assertEqual(r.returncode, 1)
                self.assertIn("line 1 is the first non-empty line and is whitespace-only", r.stderr)
        for first in ("\n", "\t# tab-led comment\n"):
            with self.subTest(ok=repr(first)):
                handler.write_text(first + text, encoding="utf-8")
                r = self.run_tool()
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_unreadable_inputs_and_outputs_fail_without_traceback(self):
        cases = [
            ("lambda/index.py", lambda p: p.write_bytes(b"# caf\xe9\n"), "lambda/index.py is not valid UTF-8 (byte 5: 0xe9)"),
            ("template.skeleton.yaml", lambda p: p.write_bytes(b"# caf\xe9\n"), "template.skeleton.yaml is not valid UTF-8 (byte 5: 0xe9)"),
            ("lambda/index.py", lambda p: (p.unlink(), p.mkdir()), "lambda/index.py is not a file"),
        ]
        for rel, breakit, message in cases:
            with self.subTest(case=message):
                self.setUp()
                breakit(self.tmp / rel)
                r = self.run_tool()
                self.assertEqual(r.returncode, 1)
                self.assertIn(message, r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_unwritable_template_fails_without_traceback(self):
        import os
        if os.geteuid() == 0:
            self.skipTest("root can write a read-only file")
        out = self.tmp / "template.yaml"
        out.write_text("stale\n", encoding="utf-8")
        out.chmod(0o444)
        self.addCleanup(out.chmod, 0o644)
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL: can't write template.yaml", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_iam_grant_and_handler_use_the_same_blob_prefix(self):
        # The prefix is defined once, in the skeleton's Mappings; the role's
        # grant and BLOB_PREFIX both read it, and the handler's fallback
        # default matches it.
        import re
        skeleton = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8")
        handler = (REPO / "lambda" / "index.py").read_text(encoding="utf-8")
        mapped = re.search(r"^Mappings:\n  Store:\n    Blobs:\n      Prefix: (\S+)$", skeleton, re.M)
        default = re.search(r'BLOB_PREFIX",\s*"([^"]+)"', handler)
        self.assertTrue(mapped and default)
        self.assertEqual(mapped.group(1), default.group(1))
        self.assertIn('"${BlobBucket.Arn}/${Prefix}*"\n                  - Prefix: !FindInMap [Store, Blobs, Prefix]', skeleton)
        self.assertIn("BLOB_PREFIX: !FindInMap [Store, Blobs, Prefix]", skeleton)
        # No hard-coded copy left beside the mapping.
        self.assertNotIn("/blobs/*", skeleton)
        self.assertNotIn('BLOB_PREFIX: "', skeleton)

    def test_invoke_grant_is_scoped_to_the_function_url(self):
        # The security half of #11: lambda:InvokeFunction only through the URL.
        import re
        skeleton = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8")
        block = re.search(r"\n  FunctionInvokePermission:\n(.*?)(?=\n\S|\n  \S)", skeleton, re.S)
        self.assertTrue(block)
        self.assertIn("Action: lambda:InvokeFunction", block.group(1))
        self.assertIn("InvokedViaFunctionUrl: true", block.group(1))

    def test_both_routes_share_the_suffix_rule_and_runtime(self):
        # The template and the Terraform route are kept equal by hand;
        # this catches either one changing alone.
        import re
        skeleton = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8")
        tf_vars = (REPO / "alternatives" / "terraform" / "variables.tf").read_text(encoding="utf-8")
        tf_main = (REPO / "alternatives" / "terraform" / "main.tf").read_text(encoding="utf-8")
        cfn_pattern = re.search(r'NameSuffix:.*?AllowedPattern: "([^"]+)"', skeleton, re.S).group(1)
        tf_pattern = re.search(r'regex\("([^"]+)", var\.name_suffix\)', tf_vars).group(1)
        self.assertEqual(cfn_pattern, tf_pattern)
        cfn_runtime = re.search(r"Runtime: (\S+)", skeleton).group(1)
        tf_runtime = re.search(r'runtime\s*=\s*"([^"]+)"', tf_main).group(1)
        self.assertEqual(cfn_runtime, tf_runtime)

    def test_reserved_concurrency_is_optional_and_never_zero(self):
        # #38: the cap is off by default in both routes, and the routes' "0
        # means not set" never reaches Lambda as 0, which would stop every call.
        import re
        skeleton = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8")
        param = re.search(r"\n  ReservedConcurrency:\n(.*?)(?=\n\n)", skeleton, re.S).group(1)
        self.assertIn('Default: "0"', param)
        pattern = re.search(r'AllowedPattern: "([^"]+)"', param).group(1)
        for value, ok in (("0", True), ("1", True), ("5", True), ("9999", True),
                          ("", False), ("00", False), ("05", False), ("-1", False),
                          ("2.5", False), ("10000", False), (" 5", False), ("٥", False)):
            with self.subTest(value=value):
                self.assertEqual(bool(re.fullmatch(pattern, value)), ok)
        self.assertRegex(skeleton, r'HasReservedConcurrency: !Not\n\s+- !Equals\n\s+- !Ref ReservedConcurrency\n\s+- "0"\n')
        function = skeleton[skeleton.index("\n  BlobFunction:\n"):skeleton.index("\n      Code:")]
        self.assertRegex(function, r"ReservedConcurrentExecutions: !If\n\s+- HasReservedConcurrency\n"
                                   r"\s+- !Ref ReservedConcurrency\n\s+- !Ref AWS::NoValue\n")
        tf_vars = (REPO / "alternatives" / "terraform" / "variables.tf").read_text(encoding="utf-8")
        tf_main = (REPO / "alternatives" / "terraform" / "main.tf").read_text(encoding="utf-8")
        var = tf_vars[tf_vars.index('variable "reserved_concurrency"'):]
        self.assertRegex(var, r"default\s*=\s*0\n")
        self.assertRegex(var, r"nullable\s*=\s*false\n")
        # Whole numbers 0-9999, as the template's pattern (checked above).
        self.assertIn("condition     = var.reserved_concurrency == floor(var.reserved_concurrency)"
                      " && var.reserved_concurrency >= 0 && var.reserved_concurrency <= 9999\n", var)
        self.assertRegex(tf_main, r"reserved_concurrent_executions\s*=\s*var\.reserved_concurrency == 0 \? -1 : var\.reserved_concurrency\n")

    def test_handler_marker_must_appear_exactly_once(self):
        skeleton = self.tmp / "template.skeleton.yaml"
        text = (REPO / "template.skeleton.yaml").read_text(encoding="utf-8")
        marker_line = next(ln for ln in text.splitlines() if "@@HANDLER@@" in ln)
        for count, body in ((0, text.replace(marker_line + "\n", "")),
                            (2, text.replace(marker_line, marker_line + "\n" + marker_line))):
            with self.subTest(markers=count):
                skeleton.write_text(body, encoding="utf-8")
                r = self.run_tool()
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"expected exactly one @@HANDLER@@ line, found {count}", r.stderr)

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
        self.assertIn(f"{ZIPFILE_LIMIT + 1} characters, over the {ZIPFILE_LIMIT}-character ZipFile limit", r.stderr)
        self.assertEqual((self.tmp / "template.yaml").read_bytes(), before)

    def pad_handler_to(self, chars):
        """Pad lambda/index.py with a trailing comment to exactly chars characters."""
        handler = self.tmp / "lambda" / "index.py"
        text = (REPO / "lambda" / "index.py").read_text(encoding="utf-8")
        handler.write_text(text + "#" * (chars - len(text) - 1) + "\n", encoding="utf-8")
        self.assertEqual(len(handler.read_text(encoding="utf-8")), chars)

    def test_zipfile_warning_starts_past_4000(self):
        self.pad_handler_to(4000)
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("warning: handler", r.stderr)
        self.pad_handler_to(4001)
        r = self.run_tool()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning: handler is 4001 characters", r.stderr)

    def test_zipfile_round_trip_mismatch_fails(self):
        # The marker two spaces deeper than the ZipFile block expects: the
        # block scalar then carries two extra spaces on every line, and the
        # extracted code no longer matches lambda/index.py.
        skeleton = self.tmp / "template.skeleton.yaml"
        text = skeleton.read_text(encoding="utf-8")
        self.assertEqual(text.count("@@HANDLER@@"), 1)
        lines = [("  " + ln if "@@HANDLER@@" in ln else ln) for ln in text.splitlines()]
        skeleton.write_text("\n".join(lines) + "\n", encoding="utf-8")
        before = (self.tmp / "template.yaml").read_bytes()
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not match lambda/index.py", r.stderr)
        self.assertEqual((self.tmp / "template.yaml").read_bytes(), before)

    def test_handler_final_newline_must_be_exactly_one(self):
        handler = self.tmp / "lambda" / "index.py"
        text = (REPO / "lambda" / "index.py").read_text(encoding="utf-8").rstrip("\n")
        for ending in ("", "\n\n"):
            with self.subTest(ending=repr(ending)):
                handler.write_text(text + ending, encoding="utf-8")
                r = self.run_tool()
                self.assertEqual(r.returncode, 1)
                self.assertIn("lambda/index.py must end with exactly one newline", r.stderr)

    def test_missing_sources_fail_without_traceback(self):
        for rel in ("lambda/index.py", "template.skeleton.yaml"):
            with self.subTest(rel=rel):
                self.setUp()
                (self.tmp / rel).unlink()
                r = self.run_tool()
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"FAIL: {rel} is missing", r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_unknown_argument_is_refused(self):
        before = (self.tmp / "template.yaml").read_bytes()
        for args in (["--chek"], ["--check", "--bogus"]):
            with self.subTest(args=args):
                r = self.run_tool(*args)
                self.assertEqual(r.returncode, 1)
                self.assertIn("unknown argument", r.stderr)
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
