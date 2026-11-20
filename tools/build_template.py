#!/usr/bin/env python3
"""Generate template.yaml from template.skeleton.yaml + lambda/index.py.

The Lambda handler has ONE canonical source: lambda/index.py.
The skeleton carries a single line containing @@HANDLER@@ at the
indentation the ZipFile block scalar needs; this script replaces
that line with the handler source, indented to match.

Guards (the reason this script exists):
  - CloudFormation's ZipFile limit is 4,096 characters. The build
    FAILS if the handler source exceeds it, and warns past 4,000.
  - CloudFormation's TemplateBody limit is 51,200 bytes. The one-paste
    deploy passes the template as a body (`aws cloudformation deploy
    --template-file` with no S3 bucket), so the build FAILS if the
    generated template exceeds it, and warns past 46,080 (90%).
  - After generating, the script extracts the ZipFile block back
    out of template.yaml and checks it matches lambda/index.py
    character for character, so a committed template.yaml can be
    verified with:  python3 tools/build_template.py --check

Usage:
  python3 tools/build_template.py           # (re)generate template.yaml
  python3 tools/build_template.py --check   # verify template.yaml is current
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKELETON = ROOT / "template.skeleton.yaml"
HANDLER = ROOT / "lambda" / "index.py"
OUTPUT = ROOT / "template.yaml"
ZIPFILE_LIMIT = 4096
ZIPFILE_WARN = 4000
TEMPLATE_BODY_LIMIT = 51200
TEMPLATE_BODY_WARN = 46080


def indent_of(line):
    return line[: len(line) - len(line.lstrip())]


def generate():
    handler = HANDLER.read_text(encoding="utf-8")
    if len(handler) > ZIPFILE_LIMIT:
        raise SystemExit(
            f"FAIL: lambda/index.py is {len(handler)} characters, over the "
            f"CloudFormation ZipFile limit of {ZIPFILE_LIMIT}. Shorten the "
            f"handler or switch the template to an S3-hosted code package "
            f"(which would give up the single any-region template)."
        )
    if len(handler) > ZIPFILE_WARN:
        print(f"warning: handler is {len(handler)} characters; "
              f"the ZipFile limit is {ZIPFILE_LIMIT}", file=sys.stderr)
    out = []
    replaced = 0
    for line in SKELETON.read_text(encoding="utf-8").splitlines():
        if "@@HANDLER@@" in line:
            pad = indent_of(line)
            for code_line in handler.rstrip("\n").splitlines():
                out.append(pad + code_line if code_line else "")
            replaced += 1
        else:
            out.append(line)
    if replaced != 1:
        raise SystemExit(f"FAIL: expected exactly one @@HANDLER@@ line, found {replaced}")
    return "\n".join(out) + "\n", handler


def extract_zipfile(template_text):
    """Pull the ZipFile block scalar back out (indentation-based)."""
    lines = template_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "ZipFile: |":
            pad = indent_of(line)
            block = []
            for nxt in lines[i + 1:]:
                if nxt and indent_of(nxt) <= pad:
                    break
                block.append(nxt[len(pad) + 2:] if nxt else "")
            return "\n".join(block) + "\n"
    raise SystemExit("FAIL: no ZipFile block found in template")


def main():
    text, handler = generate()
    extracted = extract_zipfile(text)
    if extracted != handler:
        raise SystemExit("FAIL: ZipFile block in the generated template does "
                         "not match lambda/index.py")
    size = len(text.encode("utf-8"))
    if size > TEMPLATE_BODY_LIMIT:
        raise SystemExit(
            f"FAIL: the generated template is {size} bytes, over "
            f"CloudFormation's TemplateBody limit of {TEMPLATE_BODY_LIMIT}. "
            f"The one-paste deploy sends the template as a body, so it "
            f"would need an S3 bucket past this size. template.yaml was "
            f"not written."
        )
    if size > TEMPLATE_BODY_WARN:
        print(f"warning: the generated template is {size} bytes; "
              f"the TemplateBody limit is {TEMPLATE_BODY_LIMIT}", file=sys.stderr)
    sizes = (f"handler {len(handler)} of {ZIPFILE_LIMIT} ZipFile characters; "
             f"template {size} of {TEMPLATE_BODY_LIMIT} TemplateBody bytes")
    if "--check" in sys.argv:
        if not OUTPUT.is_file():
            raise SystemExit("FAIL: template.yaml is missing; run tools/build_template.py")
        if OUTPUT.read_text(encoding="utf-8") != text:
            raise SystemExit("FAIL: template.yaml is stale; run tools/build_template.py")
        print(f"ok: template.yaml is current; {sizes}")
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote template.yaml; {sizes}; ZipFile round-trip verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
