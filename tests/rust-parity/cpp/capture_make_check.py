"""Development-only frozen `make check` transcript parsing captures; never called by Rust.

Builds parity-fixtures/cpp/test-qmake out of source with qmake6, runs the real
`make check TESTARGS=-xunitxml`, and feeds that transcript plus bounded synthetic transcripts
(wrapper-prefixed and plain invocations, a second binary that never ran, a non-zero exit
without an Error line, DOCTYPE, malformed and concatenated xunit documents, ctest-style
statuses, error/skipped/text-only details, sanitizer reports, an over-long message) to the
frozen `parse_make_check_stdout`, `parse_qtest_xunit`, `_qmake_results` and
`_attach_sanitizer_output`. The project root is replaced by <ROOT>.
"""

import json
import random
import shutil
import subprocess
import sys
import tempfile
import xml.parsers.expat
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))

from ici.core._cmake_test_results import (  # noqa: E402
    _attach_sanitizer_output,
    _qmake_results,
    parse_ctest_junit,
    parse_ctest_stdout,
    parse_make_check_stdout,
    parse_qtest_xunit,
)

QMAKE = shutil.which("qmake6")
assert QMAKE is not None


def suite(name: str, cases: str) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8" ?>\n<testsuite name="{name}">\n{cases}</testsuite>\n'
    )


PASSING = suite("A", '  <testcase name="one" classname="A"/>\n')
FAILING = suite(
    "B",
    '  <testcase name="bad" classname="B">\n'
    '    <failure type="fail" message="&apos;x&apos; &amp; &lt;y&gt; returned FALSE. ()"/>\n'
    "  </testcase>\n"
    '  <testcase name="err" classname="B"><error message="">trace\n text </error></testcase>\n'
    '  <testcase name="two" classname="B"><failure message="m1"/><error message="m2"/></testcase>\n',
)
STATES = suite(
    "S",
    '  <testcase name="xf" result="xfail"/>\n'
    '  <testcase name="xp" result="xpass"/>\n'
    '  <testcase name="odd" result="weird"/>\n'
    '  <testcase name="bl" result="BLACKLISTED"/>\n'
    '  <testcase name="notrun" status="notrun"/>\n'
    '  <testcase name="unk" status="flaky&apos;s"/>\n'
    '  <testcase name="sk"><skipped/></testcase>\n'
    '  <testcase name="sk2"><skipped>  reason text  </skipped></testcase>\n'
    '  <testcase name="  spaced  " result=" Pass "/>\n',
)
SANITIZED = suite(
    "Z",
    '  <testcase name="leak"><failure message="boom"/>'
    "<system-out>==1==ERROR: LeakSanitizer: detected memory leaks</system-out></testcase>\n"
    '  <testcase name="quiet"><system-err>SUMMARY: UndefinedBehaviorSanitizer: x</system-err>'
    "</testcase>\n",
)
LONG = suite("L", f'  <testcase name="long"><failure message="{"y" * 600}"/></testcase>\n')
SYNTHETIC = {
    "plain-pass": ("./test_a -xunitxml\n" + PASSING, 0),
    "wrapper-fail": (
        "cd /w/.. && /w/target_wrapper.sh  ./test_b -xunitxml\n"
        + FAILING
        + "make[1]: *** [Makefile:322: check] Error 2\nmake: *** [Makefile:207: sub-tests-check] Error 2\n",
        2,
    ),
    "second-never-ran": (
        "./test_a -xunitxml\n"
        + PASSING
        + "./test_b -xunitxml\n"
        + FAILING
        + "make: *** [check] Error 1\n./test_c\n",
        2,
    ),
    "nonzero-no-error-line": ("./test_a\n./test_b\nsome output\n", 1),
    "repeated-name": ("./test_a\n./test_a -x\n(cd sub && ./test_x)\n", 0),
    "doctype": ("./test_a\n<!DOCTYPE x>\n" + FAILING, 2),
    "xunit-only": (PASSING + FAILING + STATES, 1),
    "malformed-block": ("<testsuite name='M'><testcase name='a'></testsuite>\n" + FAILING, 1),
    "unnamed-suite": (
        '<testsuite><testcase name="bare"><failure message="f"/></testcase></testsuite>',
        1,
    ),
    "sanitized-xunit": (SANITIZED, 1),
    "sanitizer-transcript-pass": (
        "./test_a\n" + PASSING + "==9==ERROR: AddressSanitizer: heap-use-after-free on x\n",
        0,
    ),
    "sanitizer-no-case": ("==9==WARNING: ThreadSanitizer: data race\n", 66),
    "long-message": ("./test_l\n" + LONG + "make: *** [check] Error 1\n", 2),
    "crlf-and-formfeed": ("./test_a\r\nmake: *** [x] Error 1\x0c./test_b\r", 2),
    "empty": ("", 0),
    # ElementTree namespaces: a default or bound namespace renames the tag away from `testcase`;
    # an unbound prefix is a parse error.
    "xml-default-namespace": (
        '<testsuite xmlns="urn:x" name="N"><testcase name="a"/></testsuite>',
        1,
    ),
    "xml-bound-prefix": (
        '<testsuite xmlns:x="urn:x" name="N"><x:testcase name="a"/><testcase name="b"/></testsuite>',
        1,
    ),
    "xml-unbound-prefix": ('<testsuite name="N"><x:testcase name="a"/></testsuite>', 1),
    # Well-formedness expat refuses: each block is dropped, the sound one stays.
    "xml-control-character": (
        '<testsuite name="N"><testcase name="a\x01"/></testsuite>' + PASSING,
        1,
    ),
    "xml-less-than-in-attribute": (
        '<testsuite name="N"><testcase name="a<b"/></testsuite>' + PASSING,
        1,
    ),
    "xml-bad-references": (
        '<testsuite name="R"><testcase name="&#0;"/></testsuite>'
        '<testsuite name="S"><testcase name="&#xD800;"/></testsuite>'
        '<testsuite name="U"><testcase name="&undefined;"/></testsuite>' + PASSING,
        1,
    ),
    "xml-cdata-end-in-text": (
        '<testsuite name="N"><testcase name="a"><failure>x ]]> y</failure></testcase></testsuite>',
        1,
    ),
    "xml-double-dash-comment": (
        '<testsuite name="N"><!-- a -- b --><testcase name="a"/></testsuite>',
        1,
    ),
    "xml-bad-attribute": (
        '<testsuite name="N"><testcase 1a="x" name="a"/></testsuite>'
        '<testsuite name="O"><testcase name=a/></testsuite>' + PASSING,
        1,
    ),
    # Attribute value normalization and text assembly.
    "xml-attribute-newlines": (
        '<testsuite name="N"><testcase name="a"><failure message="x\ny&#10;z"/></testcase>'
        "</testsuite>",
        1,
    ),
    "xml-text-assembly": (
        '<testsuite name="N"><testcase name="a"><failure>a &amp; b<![CDATA[ c]]>&#x41;<i/>tail'
        "</failure></testcase></testsuite>",
        1,
    ),
    # Python `\\s`, `strip` and `\\w`: \\x1f is whitespace, a combining mark is no word character.
    "unit-separator": (
        "\x1f./test_a\x1f-x\n\x1fmake: *** [x] Error 1\n"
        '<testcase name="\x1fa\x1f"/>\n'
        '<testsuite name="T"><testcase name=" n\u00a0" status="\u2003run\u2003"/></testsuite>\n',
        1,
    ),
    "invocation-word-characters": ("./t\u00e9st_\u0663\n./mark\u0301\n./under\u203fscore\n", 1),
    "marker-word-boundaries": (
        "./a\nxERROR: AddressSanitizer: y\n\u0301SUMMARY:\x1fThreadSanitizer:\x1fz\n",
        0,
    ),
    "leak-marker-word-boundary": ("LeakSanitizer: detected memory leaks\u0301\n", 0),
    "leak-marker-no-boundary": ("LeakSanitizer: detected memory leaksx\n", 0),
    "testsuite-word-boundary": (
        '<testsuite\u0301/><testsuite name="S"><testcase name="a"/></testsuite>\n'
        '<testsuiteX/><testsuite name="U"><testcase name="b"/></testsuite>\n',
        1,
    ),
    "ctest-lines": (
        "1/6 Test #1: a ...   Passed    0.01 sec\n"
        "2/6 Test #2: b ...***Failed    0.02 sec\n"
        "3/6 Test #3: c ...***Not Run (Disabled)   0.00 sec\n"
        "4/6 Test #4: d ...***\u017fkipped   0.00 sec\n"
        "\x1f5/6\x1fTest\x1f#5:\x1fe\x1f...\x1fPassed\x1f0.01\x1fsec\x1f\n"
        "6/6 Test #6: f ...\u00a0Exception\u00a0  0.1.2 sec\n",
        1,
    ),
    # repr and casefold of a state the framework printed.
    "state-repr-and-casefold": (
        suite(
            "T",
            '  <testcase name="nbsp" status="x\u00a0y"/>\n'
            '  <testcase name="accent" status="caf\u00e9"/>\n'
            '  <testcase name="control" status="x&#9;y"/>\n'
            '  <testcase name="zero-width" result="z\u200bz"/>\n'
            '  <testcase name="long-s" status="\u017fkip"/>\n'
            '  <testcase name="sharp-s" result="pa\u00df"/>\n',
        ),
        1,
    ),
}

# What `ElementTree.fromstring` (expat in namespace mode) refuses or reads differently from a
# lenient XML reader: each fragment in every position of a report, then seeded compositions. The
# documents are stored, so the replay needs no generator.
_XML_FRAGMENTS = (
    "<a>", "</a>", "<a/>", "<x:a/>", "<xml:a/>", "<xmlns:a/>", "<1/>", "<a:b:c/>", "<:a/>", "<a:/>",
    "< a>", "<a >", "<a/ >", "<a\n/>", "</a ", "</", "<", ">", "<!", "<!x>", "/>", "?>", '"', "'",
    "&amp;", "&lt;", "&gt;", "&quot;", "&apos;", "&#65;", "&#x41;", "&#X41;", "&#0;", "&#xD800;",
    "&#xFFFE;", "&#x10FFFF;", "&#x110000;", "&#1;", "&#9;", "&#10;", "&#13;", "&undefined;", "&",
    "&;", "&#;", "&#x;", "&amp", "]]>", "]]", "]", " ", "\t", "\n", "\r", "\r\n", "text",
    "<!-- c -->", "<!-- a -- b -->", "<!--->", "<!---->", "<!-- x --->",
    "<?pi?>", "<?pi data?>", "<?pi?x?>", "<?x:y?>", "<?XML x?>", "<?xml?>", "<?xmlx ?>",
    '<?xml version="1.0"?>', '<?xml version="1.0" encoding="latin-1"?>',
    '<?xml version="1.1" standalone="yes"?>', '<?xml encoding="x" version="1.0"?>',
    '<?xml version="1.0" standalone="maybe"?>', "<?xml version=1.0?>",
    "<?xml version = '1.0'  encoding = \"a-b_c.d\" ?>", '<?xml version="1.0" encoding="1a"?>',
    "<![CDATA[x]]>", "<![CDATA[ ]] > ]]>", "<![CDATA[", "<![cdata[x]]>",
    'xmlns="u"', 'xmlns=""', 'xmlns:x="u"', 'xmlns:x=""', "b", "b=", "b=1", 'b="1"c="2"',
    'b="1" b="2"', 'x:b="1"', 'xml:lang="k"', 'xmlns:x="u" x:b="1" xmlns:y="u" y:b="2"',
    'xmlns:xml="http://www.w3.org/XML/1998/namespace"', 'xmlns:xml="u"',
    'xmlns:x="http://www.w3.org/XML/1998/namespace"', 'xmlns:xmlns="u"',
    'xmlns:x="http://www.w3.org/2000/xmlns/"', 'xmlns="http://www.w3.org/2000/xmlns/"',
    'xmlns:="u"', 'b="<"', 'b="&"', 'b="&lt;&#10;\n\r\n\t"',
    "\x01", "\x7f", "\x85", "\xa0", "\u2028", "\ufeff", "\ufffe", "\uffff", "\U00010000",
    "\u00e9", "<\u00e9/>", "<a\u00e9/>", "<\u0301/>", "<a\u0301/>", "<\u00b7/>", "<a\u00b7/>",
    "<\U00010000/>", '<\u00e9:\u00e9 xmlns:\u00e9="u"/>', "\x1c", "\x0b", "\x0c",
)  # fmt: skip
_XML_SHAPES = (
    '<testsuite><testcase name="a">{}</testcase></testsuite>',
    '<testsuite><testcase name="a" {}/></testsuite>',
    '<testsuite {}><testcase name="a"/></testsuite>',
    '<testcase name="{}"/>',
    '<testcase name="a" status="{}"/>',
    '<testcase name="a"><failure message="{}"/></testcase>',
    '<testcase name="a"><failure>{}</failure></testcase>',
    '<testcase name="a"><skipped><![CDATA[{}]]></skipped></testcase>',
    '<testcase name="a"><system-out><!--{}-->x</system-out></testcase>',
    '{}<testcase name="a"/>',
    '<testcase name="a"/>{}',
)


def _xml_tree(chooser: random.Random, depth: int = 0) -> str:
    name = chooser.choice(("testcase", "failure", "x:testcase", "testsuite", "\u00e9", "_a.b-c"))
    attributes = "".join(
        " " + chooser.choice(
            (
                'name="n"', "message='&lt;m&gt;'", 'status="skipped"', 'xmlns="w"', 'xmlns=""',
                'xmlns:y="v"', 'y:name="2"', 'xml:lang="k"', 'name\n=\t"\n\r\n"', 'name="&#10;&#13;"',
            )
        )
        for _ in range(chooser.randint(0, 2))
    )  # fmt: skip
    if depth > 2 or chooser.random() < 0.3:
        return f"<{name}{attributes}/>"
    body = "".join(
        chooser.choice(
            (
                _xml_tree(chooser, depth + 1), "text", "\r\n", "\r", " ", "&amp;", "&#13;",
                "<![CDATA[q\r\nz]]>", "<!-- c -->", "<?pi d?>", "\u00e9", "]", "] ]>",
            )
        )
        for _ in range(chooser.randint(0, 3))
    )  # fmt: skip
    return f"<{name}{attributes}>{body}</{name}>"


def _xml_documents() -> list[str]:
    documents = [
        shape.replace("{}", fragment) for fragment in _XML_FRAGMENTS for shape in _XML_SHAPES
    ]
    chooser = random.Random(20261106)
    for _ in range(400):
        document = f'<r xmlns:x="u">{_xml_tree(chooser)}</r>'
        if chooser.random() < 0.3:
            at = chooser.randint(0, len(document))
            document = document[:at] + chooser.choice(_XML_FRAGMENTS) + document[at:]
        documents.append(document)
    return list(dict.fromkeys(documents))


def _expat_name_ranges(template: str) -> list[int]:
    """expat's own name table (`namingBitmap`) as flat `first, last` pairs below U+10000."""

    def accepted(character: str) -> bool:
        parser = xml.parsers.expat.ParserCreate(None, "}")
        try:
            parser.Parse(template.format(character), True)
        except xml.parsers.expat.ExpatError:
            return False
        return True

    ranges: list[int] = []
    start = None
    for code in range(0x80, 0x10001):
        named = code < 0xFFFE and not 0xD800 <= code <= 0xDFFF and accepted(chr(code))
        if named and start is None:
            start = code
        if not named and start is not None:
            ranges += [start, code - 1]
            start = None
    return ranges


def view(cases) -> list[dict]:
    return [asdict(case) for case in cases]


def frozen(text: str, returncode: int) -> dict:
    merged = _qmake_results(text, returncode)
    return {
        "make": view(parse_make_check_stdout(text, returncode)),
        "xunit": view(parse_qtest_xunit(text)),
        "qmake": view(merged),
        "attached": view(_attach_sanitizer_output(merged, text)),
        "ctest": view(parse_ctest_stdout(text)),
    }


with tempfile.TemporaryDirectory(prefix="ici-make-check-") as temporary:
    work = Path(temporary)
    shutil.copytree(ROOT / "parity-fixtures/cpp/test-qmake", work / "src")
    build = work / "build"
    build.mkdir()
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(work), "LC_ALL": "C.UTF-8"}
    subprocess.run([QMAKE, str(work / "src/project.pro")], cwd=build, env=environment, check=True)
    subprocess.run(
        ["make", "--jobs=2"], cwd=build, env=environment, check=True, capture_output=True
    )
    result = subprocess.run(
        ["make", "check", "TESTARGS=-xunitxml"],
        cwd=build,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    # Timestamps and the host name vary per run; the replay feeds this exact transcript.
    real = (result.stdout + result.stderr).replace(str(work), "<ROOT>")
    real_code = result.returncode

transcripts = {"test-qmake": (real, real_code), **SYNTHETIC}
cases = {
    name: {"transcript": text, "returncode": code, "frozen": frozen(text, code)}
    for name, (text, code) in transcripts.items()
}
junit = [
    {"document": document, "frozen": view(parse_ctest_junit(document))}
    for document in _xml_documents()
]
(ROOT / "tests/rust-parity/cpp/make-check-oracle.json").write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "qmake": Path(QMAKE).name,
            "cases": cases,
            "junit": junit,
            "expat_name_start": _expat_name_ranges("<{}/>"),
            "expat_name_char": _expat_name_ranges("<a{}/>"),
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} make check transcripts, {len(junit)} CTest reports")
