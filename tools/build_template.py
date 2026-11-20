#!/usr/bin/env python3
"""Generate template.yaml from template.skeleton.yaml + lambda/index.py.

The Lambda handler has ONE canonical source: lambda/index.py.
The skeleton carries a single line containing @@HANDLER@@ at the
indentation the ZipFile block scalar needs; this script replaces
that line with the handler source, indented to match.

Guards (the reason this script exists):
  - Inline ZipFile code is kept to 4,096 characters, the limit
    CloudFormation long documented for it (its current reference gives
    only a 4 MB zip limit, so this is the repo's own budget until a
    larger inline handler is tested on a real stack; see #11). The
    build FAILS if the handler source exceeds it, and warns past 4,000.
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
Any other argument is refused.
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


def read_source(path):
    if not path.is_file():
        raise SystemExit(f"FAIL: {path.relative_to(ROOT)} is missing")
    return path.read_text(encoding="utf-8")


def generate():
    handler = read_source(HANDLER)
    if len(handler) > ZIPFILE_LIMIT:
        raise SystemExit(
            f"FAIL: lambda/index.py is {len(handler)} characters, over the "
            f"{ZIPFILE_LIMIT}-character ZipFile limit. Shorten the handler, "
            f"or test a larger inline handler on a real stack before raising "
            f"the limit."
        )
    if len(handler) > ZIPFILE_WARN:
        print(f"warning: handler is {len(handler)} characters; "
              f"the ZipFile limit is {ZIPFILE_LIMIT}", file=sys.stderr)
    if not handler.endswith("\n") or handler.endswith("\n\n"):
        raise SystemExit("FAIL: lambda/index.py must end with exactly one newline "
                         "(a YAML block scalar keeps one, so the two would differ)")
    out = []
    replaced = 0
    for line in read_source(SKELETON).splitlines():
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
    unknown = [a for a in sys.argv[1:] if a != "--check"]
    if unknown:
        raise SystemExit(f"FAIL: unknown argument(s): {' '.join(unknown)}; "
                         f"usage: build_template.py [--check]")
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
