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


def read_source(path, newline=None):
    if not path.is_file():
        raise SystemExit(f"FAIL: {path.relative_to(ROOT)} is missing")
    with path.open(encoding="utf-8", newline=newline) as f:
        return f.read()


def generate():
    # Read as is (no newline translation), so a CR is seen and refused below
    # rather than silently turned into a line break.
    handler = read_source(HANDLER, newline="")
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
    check_handler_chars(handler)
    skeleton = read_source(SKELETON).splitlines()
    markers = [i for i, line in enumerate(skeleton) if "@@HANDLER@@" in line]
    if len(markers) != 1:
        raise SystemExit(f"FAIL: expected exactly one @@HANDLER@@ line, found {len(markers)}")
    marker = markers[0]
    # The `ZipFile: |` line just before the marker; the round-trip check
    # extracts from there.
    zipfile_line = marker - 1
    if zipfile_line < 0 or skeleton[zipfile_line].strip() != "ZipFile: |":
        raise SystemExit("FAIL: the @@HANDLER@@ line must come right after a "
                         "`ZipFile: |` line in template.skeleton.yaml")
    pad = indent_of(skeleton[marker])
    code = [pad + ln if ln else "" for ln in handler.rstrip("\n").splitlines()]
    out = skeleton[:marker] + code + skeleton[marker + 1:]
    return "\n".join(out) + "\n", handler, zipfile_line


def check_handler_chars(handler):
    """Refuse characters a YAML block scalar can't carry as written.

    YAML forbids most control characters, and U+0085, U+2028 and U+2029 are
    line breaks to it (and to str.splitlines), so any of them would change the
    code CloudFormation runs or make the template invalid.
    """
    for lineno, line in enumerate(handler.split("\n"), 1):
        for ch in line:
            o = ord(ch)
            if ch != "\t" and (o < 0x20 or 0x7F <= o <= 0x9F or ch in "  "
                               or 0xD800 <= o <= 0xDFFF or o in (0xFFFE, 0xFFFF)):
                hint = ("use LF line endings" if ch == "\r"
                        else "use an escape in the Python source instead")
                raise SystemExit(f"FAIL: lambda/index.py line {lineno} has U+{o:04X}, "
                                 f"which can't go in a YAML block scalar as written; "
                                 f"{hint}")


def extract_zipfile(template_text, zipfile_line):
    """Pull the handler's ZipFile block scalar back out (indentation-based).

    zipfile_line is the index of the `ZipFile: |` line that precedes the
    @@HANDLER@@ marker, so another inline function elsewhere in the template
    is never mistaken for the handler.
    """
    lines = template_text.splitlines()
    pad = indent_of(lines[zipfile_line])
    block = []
    for nxt in lines[zipfile_line + 1:]:
        if nxt and indent_of(nxt) <= pad:
            break
        block.append(nxt[len(pad) + 2:] if nxt else "")
    return "\n".join(block) + "\n"


def main():
    unknown = [a for a in sys.argv[1:] if a != "--check"]
    if unknown:
        raise SystemExit(f"FAIL: unknown argument(s): {' '.join(unknown)}; "
                         f"usage: build_template.py [--check]")
    text, handler, zipfile_line = generate()
    extracted = extract_zipfile(text, zipfile_line)
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
        # Bytes, not text: read_text would translate CRLF and call a CRLF
        # copy current, though its size and SHA-256 differ from the build's.
        if OUTPUT.read_bytes() != text.encode("utf-8"):
            raise SystemExit("FAIL: template.yaml is stale; run tools/build_template.py")
        print(f"ok: template.yaml is current; {sizes}")
        return 0
    # Bytes, so the file is LF on every platform (write_text would write
    # CRLF on Windows).
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(f"wrote template.yaml; {sizes}; ZipFile round-trip verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
