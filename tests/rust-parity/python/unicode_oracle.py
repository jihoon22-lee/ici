"""Verify Unicode 16 mappings against every scalar in the fixed CPython runtime."""

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[3]
    fixture = json.loads(
        Path(__file__).with_name("unicode-casefold.json").read_text(encoding="utf-8")
    )
    assert unicodedata.unidata_version == fixture["unicode_version"]
    data = root / "crates/ici-analysis/data/unicode-16"
    for name, field in (
        ("CaseFolding.txt", "source_sha256"),
        ("DerivedCoreProperties.txt", "xid_source_sha256"),
        ("LICENSE.txt", "license_sha256"),
    ):
        assert hashlib.sha256((data / name).read_bytes()).hexdigest() == fixture[field]
    folding = hashlib.sha256()
    identifiers = hashlib.sha256()
    lowercase = hashlib.sha256()
    representation = hashlib.sha256()
    decimal = hashlib.sha256()
    word = hashlib.sha256()
    for code in range(0x110000):
        if 0xD800 <= code <= 0xDFFF:
            continue
        character = chr(code)
        identifiers.update(bytes((character.isidentifier(), ("x" + character).isidentifier())))
        mapping = " ".join(f"{ord(item):06X}" for item in character.casefold())
        folding.update(f"{code:06X};{mapping}\n".encode("ascii"))
        mapping = " ".join(f"{ord(item):06X}" for item in character.lower())
        lowercase.update(f"{code:06X};{mapping}\n".encode("ascii"))
        representation.update(f"{code:06X};{character!r}\n".encode())
        decimal.update(bytes((unicodedata.decimal(character, 255),)))
        word.update(bytes((re.fullmatch(r"\w", character) is not None,)))
    assert folding.hexdigest() == fixture["all_scalar_fold_sha256"]
    assert identifiers.hexdigest() == fixture["all_scalar_identifier_sha256"]
    assert lowercase.hexdigest() == fixture["all_scalar_lower_sha256"]
    assert representation.hexdigest() == fixture["all_scalar_repr_sha256"]
    assert decimal.hexdigest() == fixture["all_scalar_decimal_sha256"]
    assert word.hexdigest() == fixture["all_scalar_word_sha256"]
    assert (
        hashlib.sha256(bytes(range(256)).decode("cp437").encode()).hexdigest()
        == fixture["all_cp437_sha256"]
    )
    for value, expected in fixture["lower_samples"]:
        assert value.lower() == expected
    print(f"All Unicode {unicodedata.unidata_version} scalar mappings and CP437 bytes match")
    print(sys.version.split()[0])


if __name__ == "__main__":
    main()
