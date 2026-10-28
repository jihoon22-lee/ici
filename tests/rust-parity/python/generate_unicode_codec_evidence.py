"""Pin strict CPython Unicode codec and Unicode-name observations for native Rust.

Development only: CPython 3.14.7 -I -B. Expected data changes only with --write.
"""

import argparse
import base64
import codecs
import encodings
import encodings.aliases
import hashlib
import json
import pkgutil
import struct
import subprocess
import sys
import unicodedata
import warnings
from pathlib import Path

VARIANTS = {
    "unicode_escape": "Codec::UnicodeEscape(false)",
    "raw_unicode_escape": "Codec::UnicodeEscape(true)",
    "utf_7": "Codec::Utf7",
    "utf_16": "Codec::Utf16(None)",
    "utf_16_le": "Codec::Utf16(Some(true))",
    "utf_16_be": "Codec::Utf16(Some(false))",
    "utf_32": "Codec::Utf32(None)",
    "utf_32_le": "Codec::Utf32(Some(true))",
    "utf_32_be": "Codec::Utf32(Some(false))",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def result(raw, name):
    try:
        text = raw.decode(name, errors="strict")
    except UnicodeError:
        return None, "invalid"
    try:
        return text.encode("utf-8"), "scalar"
    except UnicodeError:
        return None, "non_scalar"


def add_result(digest, raw, name, counts):
    output, kind = result(raw, name)
    counts[kind] += 1
    digest.update(raw)
    if output is None:
        digest.update(b"\x00")
    else:
        digest.update(b"\x01" + struct.pack("<I", len(output)) + output)


def short_observations(name):
    digest = hashlib.sha256()
    counts = {"invalid": 0, "scalar": 0, "non_scalar": 0}
    for width in (1, 2):
        digest.update(bytes([width]))
        for value in range(256**width):
            add_result(digest, value.to_bytes(width, "big"), name, counts)
    return {"sha256": digest.hexdigest(), "counts": counts}


def scalar_bytes(value, name):
    if name in ("unicode_escape", "raw_unicode_escape"):
        return f"\\U{value:08X}".encode()
    if name == "utf_7":
        if value < 0x10000:
            raw = value.to_bytes(2, "big")
        else:
            scalar = value - 0x10000
            raw = (0xD800 + (scalar >> 10)).to_bytes(2, "big") + (
                0xDC00 + (scalar & 1023)
            ).to_bytes(2, "big")
        return b"+" + base64.b64encode(raw).rstrip(b"=") + b"-"
    little = name.endswith("_le") or (name in ("utf_16", "utf_32") and sys.byteorder == "little")
    order = "little" if little else "big"
    if name.startswith("utf_16"):
        if value < 0x10000:
            return value.to_bytes(2, order)
        scalar = value - 0x10000
        return (0xD800 + (scalar >> 10)).to_bytes(2, order) + (0xDC00 + (scalar & 1023)).to_bytes(
            2, order
        )
    return value.to_bytes(4, order)


def scalar_observations(name):
    digest = hashlib.sha256()
    counts = {"invalid": 0, "scalar": 0, "non_scalar": 0}
    for value in range(0x110000):
        digest.update(struct.pack("<I", value))
        add_result(digest, scalar_bytes(value, name), name, counts)
    return {"sha256": digest.hexdigest(), "counts": counts}


def name_observations():
    names = hashlib.sha256()
    lookups = hashlib.sha256()
    count = 0
    for value in range(0x110000):
        names.update(struct.pack("<I", value))
        name = unicodedata.name(chr(value), None)
        if name is None:
            names.update(b"\x00")
            continue
        count += 1
        encoded = name.encode()
        names.update(b"\x01" + struct.pack("<H", len(encoded)) + encoded)
        for label in (
            name,
            name.lower(),
            name.title(),
            name.replace(" ", "  "),
            name.replace("-", ""),
            name + " ",
        ):
            raw = b"\\N{" + label.encode() + b"}"
            output, _ = result(raw, "unicode_escape")
            lookups.update(struct.pack("<H", len(raw)) + raw)
            lookups.update(
                b"\x00" if output is None else b"\x01" + struct.pack("<I", len(output)) + output
            )
    return {
        "named_scalars": count,
        "names_sha256": names.hexdigest(),
        "lookup_sha256": lookups.hexdigest(),
    }


def source_census(root, aliases):
    known = set(aliases) | {"charmap", "hz", "hzgb", "hz_gb", "hz_gb_2312"}
    for manifest in ("manifest.json", "stateful-manifest.json"):
        known.update(
            json.loads((root / "crates/ici-analysis/data/python-codecs" / manifest).read_text())[
                "aliases"
            ]
        )
    records = {}
    names = {module.name for module in pkgutil.iter_modules(encodings.__path__)} | set(
        encodings.aliases.aliases
    )
    for name in sorted(names):
        try:
            codec = codecs.lookup(name)
        except LookupError:
            continue
        if not codec._is_text_encoding:
            disposition = "non_text_codec"
        elif name in known:
            disposition = "native_decoder"
        elif name == "undefined":
            disposition = "always_rejecting_decoder"
        else:
            disposition = "pending_source_audit"
        samples = []
        for prefix, separator, suffix in (
            ("# coding:", "", "\ndef hello(value):\n    return value\n"),
            ("#coding:", "", "\n"),
            ("#coding=", "", ""),
            ("\t#coding:", "", "\n"),
            ("#!/usr/bin/python\n# coding:", "", "\nx=1\n"),
        ):
            raw = (prefix + separator + name + suffix).encode()
            try:
                compile(raw, "main.py", "exec")
                accepted = True
            except Exception:
                accepted = False
            samples.append(
                {"bytes_base64": base64.b64encode(raw).decode(), "source_accepted": accepted}
            )
        records[name] = {
            "canonical_codec": codec.name,
            "disposition": disposition,
            "samples": samples,
        }
    return records


def generate():
    root = Path(__file__).resolve().parents[3]
    alias_file = root / "crates/ici-analysis/data/unicode-16/NameAliases.txt"
    name_aliases = {}
    for line in alias_file.read_text().splitlines():
        if line and not line.startswith("#"):
            scalar, name, _ = line.split(";")
            value = int(scalar, 16)
            assert unicodedata.lookup(name) == chr(value)
            assert (b"\\N{" + name.encode() + b"}").decode("unicode_escape") == chr(value)
            name_aliases[name] = value
    aliases = {name: name for name in VARIANTS}
    aliases.update(
        {alias: name for alias, name in encodings.aliases.aliases.items() if name in VARIANTS}
    )
    native = [
        "// Generated CPython 3.14.7 / Unicode 16 observations; see data/python-codecs/README.txt.",
        "const UNICODE_ALIASES: &[(&str, Codec)] = &[",
    ]
    native += [f'("{alias}", {VARIANTS[name]}),' for alias, name in sorted(aliases.items())]
    native += ["];", "pub(crate) const NAME_ALIASES: &[(&str, u32)] = &["]
    native += [f'("{name}", {value}),' for name, value in sorted(name_aliases.items())]
    native.append("];")
    source = subprocess.run(
        ["rustfmt", "--edition", "2024", "--emit", "stdout"],
        input="\n".join(native),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.encode()
    records = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in (*VARIANTS, "charmap"):
            print("observing", name, flush=True)
            records[name] = {"short": short_observations(name)}
            if name in VARIANTS:
                records[name]["all_codepoints"] = scalar_observations(name)
    manifest = {
        "format": "ici.python-unicode-codec-observations/v1",
        "reference_python": "3.14.7",
        "unicode_version": unicodedata.unidata_version,
        "platform_byteorder": sys.byteorder,
        "source_url": "https://github.com/python/cpython/blob/v3.14.7/Objects/unicodeobject.c",
        "alias_source_url": "https://www.unicode.org/Public/16.0.0/ucd/NameAliases.txt",
        "name_aliases_sha256": sha(alias_file.read_bytes()),
        "generated_rust_sha256": sha(source),
        "aliases": {**aliases, "charmap": "iso8859_1"},
        "codecs": records,
        "unicode_names": name_observations(),
        "name_aliases_count": len(name_aliases),
        "source_codec_census": source_census(root, aliases),
    }
    paths = {
        root / "crates/ici-analysis/src/unicode_codec_tables.rs": source,
        root / "crates/ici-analysis/data/python-codecs/unicode-manifest.json": (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode(),
    }
    return paths


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    assert sys.version_info[:3] == (3, 14, 7) and unicodedata.unidata_version == "16.0.0"
    assert sys.flags.isolated and sys.dont_write_bytecode
    for path, data in generate().items():
        if args.write:
            path.write_bytes(data)
        else:
            assert path.read_bytes() == data, path
    print("Strict Unicode codec/name evidence " + ("written" if args.write else "verified"))


if __name__ == "__main__":
    main()
