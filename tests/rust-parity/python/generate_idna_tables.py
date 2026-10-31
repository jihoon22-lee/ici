"""Generate the native IDNA source-codec tables and CPython observations (development only).

Run with CPython 3.14.7 as ``python3.14 -I -B generate_idna_tables.py [--write]``.
The tables are numeric data taken from the reference interpreter's ``stringprep``
module (RFC 3454 tables over Unicode 3.2) and ``unicodedata.ucd_3_2_0``. The
manifest pins observation digests that the native tests recompute:

* nameprep of every code point (including lone surrogates),
* Unicode 3.2 NFKC of every scalar, of canonical first/second pairs and of
  deterministic pseudo-random strings (splitmix64; the Rust test replays it),
* IDNA decoding of a recorded label corpus and actual byte-source compilation.

Expected data changes only with ``--write``.
"""

from __future__ import annotations

import argparse
import codecs
import contextlib
import hashlib
import json
import platform
import stringprep
import sys
import unicodedata
from encodings import idna
from pathlib import Path
from unicodedata import ucd_3_2_0

ROOT = Path(__file__).resolve().parents[3]
TABLES = ROOT / "crates/ici-analysis/src/idna_tables.rs"
MANIFEST = ROOT / "crates/ici-analysis/data/python-codecs/idna-manifest.json"
SURROGATES = range(0xD800, 0xE000)
RANDOM_STRINGS = 60_000
PROHIBITED = (
    stringprep.in_table_c12,
    stringprep.in_table_c22,
    stringprep.in_table_c3,
    stringprep.in_table_c4,
    stringprep.in_table_c5,
    stringprep.in_table_c6,
    stringprep.in_table_c7,
    stringprep.in_table_c8,
    stringprep.in_table_c9,
)


def ranges(predicate) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for code in range(0x110000):
        if predicate(chr(code)):
            if result and result[-1][1] == code - 1:
                result[-1] = (result[-1][0], code)
            else:
                result.append((code, code))
    return result


def outcome(function, text: str) -> str:
    try:
        value = function(text)
    except UnicodeError:
        return "E"
    if isinstance(value, bytes):
        return "b" + value.hex()
    return ",".join(f"{ord(ch):x}" for ch in value)


def splitmix(state: int) -> tuple[int, int]:
    state = (state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    value = state
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return state, value ^ (value >> 31)


def random_pool() -> list[int]:
    """Characters whose interaction stresses mapping, ordering, composition and bidi."""
    pool: set[int] = set(range(0x20, 0x7F))
    for code in range(0x110000):
        if code in SURROGATES:
            continue
        char = chr(code)
        if (
            unicodedata.combining(char)
            or ucd_3_2_0.combining(char)
            or unicodedata.decomposition(char)
            or stringprep.in_table_b1(char)
            or stringprep.map_table_b2(char) != char
        ) and code < 0x30000:
            pool.add(code)
    pool.update(range(0x1100, 0x1200))  # conjoining jamo
    pool.update([0x05D0, 0x05D1, 0x0627, 0x0628, 0x0661, 0x200E, 0x200F, 0x00AD, 0x1B05, 0x1B35])
    pool.update([0xD800, 0xDC00, 0xFFFD, 0xE000, 0x2F868, 0x2F874, 0x2F91F, 0x2F95F, 0x2F9BF])
    return sorted(pool)


def random_strings(pool: list[int]):
    state = 0x1C1DA0
    for _ in range(RANDOM_STRINGS):
        state, value = splitmix(state)
        length = 1 + value % 8
        characters = []
        for _ in range(length):
            state, value = splitmix(state)
            characters.append(pool[value % len(pool)])
        yield characters


def digest(lines) -> tuple[int, str]:
    hasher = hashlib.sha256()
    count = 0
    for line in lines:
        hasher.update(line.encode("ascii") + b"\n")
        count += 1
    return count, hasher.hexdigest()


def label_corpus(pool: list[int]) -> list[bytes]:
    """Valid, non-canonical, case-variant and malformed ACE labels."""
    labels: set[bytes] = {
        b"xn--bcher-kva",
        b"XN--BCHER-KVA",
        b"xn--Bcher-kva",
        b"xn--bcher-kva-",
        b"xn--",
        b"xn---",
        b"xn--abc-",
        b"xn--zz",
        b"xn--99999999999999999999",
        b"xn--a" + b"9" * 70,
        b"xn--ls8h",
        b"xn--mgbh0fb",
        b"xn--4gbrim",
        b"xn--d1acpjx3f.xn--p1ai",
        b"xn--nxasmq6b",
        b"xn--xn--a-ecp",
        b"xn--55qx5d",
        b"xn--fiqs8s",
        b"xn--hxajbheg2az3al",
        b"xn--\xc3\xa9-xyz",
        b"xn--ab\xffc",
        b"plain",
        b"",
    }
    state = 0x1D1A
    for characters in random_strings(pool):
        text = "".join(map(chr, characters))
        for candidate in (text, text.lower(), text.upper()):
            with contextlib.suppress(UnicodeError):
                labels.add(idna.ToASCII(candidate))
            with contextlib.suppress(UnicodeError):
                labels.add(b"xn--" + candidate.encode("punycode"))
        state, value = splitmix(state)
        if value % 7 == 0 and labels:
            label = sorted(labels)[value % len(labels)]
            if label:
                index = value % len(label)
                labels.add(label[:index] + b"aZ9-"[value % 4 : value % 4 + 1] + label[index + 1 :])
        if len(labels) >= 2_500:
            break
    return sorted(labels)


def source_samples() -> list[bytes]:
    return [
        b"#coding:idna\nimport a.xn--bcher-kva.b\n",
        b"#coding:idna\nimport a.XN--BCHER-KVA.b\n",
        b"#coding:idna\nimport a.xn--bcher-kva\n",
        b"#coding:idna\nimport a.xn--bcher-kva",
        b"#coding:idna\nx = 1  # see xn--bcher-kva\n",
        b"#coding:idna\nx = 'a.xn--bcher-kva.b'\n",
        b"#coding:idna\nx = 'a.xn--bcher-kvb.b'\n",
        b"#coding:idna\nx = 'a.xn--Bcher-kva.b'\n",
        b"#coding:idna\nx = 'a.xn--55qx5d.b'\r\nprint(x)\r\n",
        b"#coding:idna\nx = 'a.xn--zz.b'\n",
        b"#coding:idna\nx = 'caf\xc3\xa9'\n",
        b"#coding:idna\nx = 1\n" + b"y" * 2000 + b".z = 2\n",
        b"#coding:idna\nimport os.xn--" + b"a" * 1100 + b".b\n",
        b"# -*- coding: idna -*-\ndef f(value):\n    return value.xn--nxasmq6b.real\n",
        b"#coding:punycode\nabc-",
        b"#coding:punycode\n",
        b"#coding:punycode\nx = 1\n",
    ]


def compile_outcome(source: bytes) -> dict:
    try:
        tree = compile(source, "<idna>", "exec", flags=0x400, dont_inherit=True)
    except (SyntaxError, ValueError, UnicodeError) as error:
        return {"source_accepted": False, "error": type(error).__name__}
    import ast

    return {"source_accepted": True, "ast": ast.dump(tree)}


def write_rust(
    tables: dict[str, list[tuple[int, int]]], b2: list[tuple[int, list[int]]], corrections
):
    lines = [
        "// @generated by tests/rust-parity/python/generate_idna_tables.py from CPython 3.14.7",
        "// stringprep (RFC 3454, Unicode 3.2) and unicodedata.ucd_3_2_0; do not edit.",
        "",
    ]
    for name, values in tables.items():
        lines.append(f"pub(crate) const {name}: &[(u32, u32)] = &[")
        lines.extend(f"    (0x{start:X}, 0x{end:X})," for start, end in values)
        lines.append("];")
    data: list[int] = []
    entries = []
    for code, mapped in b2:
        entries.append((code, len(data), len(mapped)))
        data.extend(mapped)
    lines.append("/// RFC 3454 B.2 mappings that differ from identity: (code, start, length).")
    lines.append("pub(crate) const B2_MAP: &[(u32, u16, u8)] = &[")
    lines.extend(f"    (0x{code:X}, {start}, {length})," for code, start, length in entries)
    lines.append("];")
    lines.append("pub(crate) const B2_DATA: &[u32] = &[")
    for offset in range(0, len(data), 12):
        lines.append("    " + " ".join(f"0x{value:X}," for value in data[offset : offset + 12]))
    lines.append("];")
    lines.append("/// Unicode 3.2 normalization corrections applied before decomposition.")
    lines.append("pub(crate) const CORRECTIONS: &[(u32, u32)] = &[")
    lines.extend(f"    (0x{code:X}, 0x{value:X})," for code, value in corrections)
    lines.append("];")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if platform.python_version() != "3.14.7":
        raise SystemExit("generate with CPython 3.14.7")
    assert ucd_3_2_0.unidata_version == "3.2.0" and unicodedata.unidata_version == "16.0.0"
    tables = {
        "B1": ranges(stringprep.in_table_b1),
        "PROHIBITED": ranges(lambda ch: any(table(ch) for table in PROHIBITED)),
        "RAND_AL": ranges(stringprep.in_table_d1),
        "L_CAT": ranges(stringprep.in_table_d2),
        "UNASSIGNED_32": ranges(
            lambda ch: ord(ch) not in SURROGATES and ucd_3_2_0.category(ch) == "Cn"
        ),
    }
    b2 = []
    for code in range(0x110000):
        mapped = stringprep.map_table_b2(chr(code))
        if mapped != chr(code):
            b2.append((code, [ord(ch) for ch in mapped]))
    corrections = []
    for code in range(0x110000):
        char = chr(code)
        if code in SURROGATES or ucd_3_2_0.category(char) == "Cn":
            continue
        old, new = ucd_3_2_0.normalize("NFD", char), unicodedata.normalize("NFD", char)
        if old != new:
            assert len(old) == 1, hex(code)
            corrections.append((code, ord(old)))
    assert len(corrections) == 5, corrections
    rust = write_rust(tables, b2, corrections)

    pool = random_pool()
    firsts, seconds = set(), set()
    for code in range(0x110000):
        decomposition = unicodedata.decomposition(chr(code))
        if decomposition and not decomposition.startswith("<"):
            parts = [int(part, 16) for part in decomposition.split()]
            if len(parts) == 2:
                firsts.add(parts[0])
                seconds.add(parts[1])
    scalars = [code for code in range(0x110000) if code not in SURROGATES]
    labels = label_corpus(pool)
    observations = {
        "nameprep_codepoints": digest(
            f"{code:x}:{outcome(idna.nameprep, chr(code))}" for code in range(0x110000)
        ),
        "nfkc32_scalars": digest(
            f"{code:x}:{outcome(lambda s: ucd_3_2_0.normalize('NFKC', s), chr(code))}"
            for code in scalars
        ),
        "nfkc32_pairs": digest(
            f"{a:x},{b:x}:{outcome(lambda s: ucd_3_2_0.normalize('NFKC', s), chr(a) + chr(b))}"
            for a in sorted(firsts)
            for b in sorted(seconds)
        ),
        "nameprep_random": digest(
            f"{','.join(f'{c:x}' for c in chars)}:"
            f"{outcome(idna.nameprep, ''.join(map(chr, chars)))}"
            for chars in random_strings(pool)
        ),
        "nfkc32_random": digest(
            f"{','.join(f'{c:x}' for c in chars)}:"
            f"{outcome(lambda s: ucd_3_2_0.normalize('NFKC', s), ''.join(map(chr, chars)))}"
            for chars in random_strings(pool)
            if not any(c in SURROGATES for c in chars)
        ),
    }
    manifest = {
        "format": "ici.python-idna-observations/v1",
        "reference_python": platform.python_version(),
        "unicode": {"current": unicodedata.unidata_version, "stringprep": "3.2.0"},
        "source_urls": [
            "https://github.com/python/cpython/blob/v3.14.7/Lib/encodings/idna.py",
            "https://github.com/python/cpython/blob/v3.14.7/Lib/encodings/punycode.py",
            "https://github.com/python/cpython/blob/v3.14.7/Lib/stringprep.py",
            "https://github.com/python/cpython/blob/v3.14.7/Modules/unicodedata.c",
            "https://github.com/python/cpython/blob/v3.14.7/Parser/tokenizer/helpers.c",
        ],
        "tables_sha256": hashlib.sha256(rust.encode()).hexdigest(),
        "random_pool": [f"{code:x}" for code in pool],
        "random_strings": RANDOM_STRINGS,
        "pairs": {
            "firsts": [f"{code:x}" for code in sorted(firsts)],
            "seconds": [f"{code:x}" for code in sorted(seconds)],
        },
        "observations": {key: {"count": n, "sha256": h} for key, (n, h) in observations.items()},
        "labels": [
            {"hex": label.hex(), "decoded": outcome(lambda b: codecs.decode(b, "idna"), label)}
            for label in labels
        ],
        "sources": [
            {
                "hex": source.hex(),
                "codec_decoded": outcome(
                    lambda b: codecs.decode(
                        b, "punycode" if b.startswith(b"#coding:punycode") else "idna"
                    ),
                    source,
                ),
                **compile_outcome(source),
            }
            for source in source_samples()
        ],
    }
    text = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    if args.write:
        TABLES.write_text(rust, encoding="utf-8")
        MANIFEST.write_text(text, encoding="utf-8")
    else:
        assert TABLES.read_text(encoding="utf-8") == rust, "idna tables differ"
        assert MANIFEST.read_text(encoding="utf-8") == text, "idna manifest differs"
    print(
        f"{len(labels)} labels, {len(manifest['sources'])} sources, "
        f"{sum(n for n, _ in observations.values())} observations",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
