Strict Python source character mappings
=======================================

These inert data tables record CPython 3.14.7 strict byte-to-Unicode behavior.
Rust consumes them directly when reading immutable source snapshots. Development
CPython produced observations; the product contains the compiled Rust decoder
and numeric data.

The first table set covers 70 single-byte character maps, strict ASCII and 16
stateless CJK codecs. Undefined entries fail decoding. UTF-32LE entries use
ffffffff for undefined and the high bit for a two-scalar COMBINED entry. Separate
EUC-JP/JIS tables handle three-byte characters. EUC-KR's eight-byte Hangul form is
reconstructed from all 11,172 measured syllables. GB18030's four-byte pointer
ranges were derived from all 1,587,600 combinations, including invalid ranges.

All 256 single-byte and 65,536 double-byte inputs are independently hashed per
codec, along with all GB18030 four-byte combinations and EUC-KR Hangul forms.
The native tests reproduce the complete observation streams. Original raw bytes
remain in the request; the decoder maps UTF-8 scalar boundaries back to original
byte offsets. A boundary inside a character that expands to two scalars is
explicitly unrepresentable. Token boundaries must have a proven raw position.

Regenerate with the fixed development reference:
  python3.14 -I -B tests/rust-parity/python/generate_codec_tables.py --write
Verify without changing any expected data:
  python3.14 -I -B tests/rust-parity/python/generate_codec_tables.py --check

The adaptation stores decoder observations in dense little-endian scalar tables,
compresses GB18030 mappings into numeric ranges and implements decoding and
source-position mapping in Rust. These are the changes relative to the upstream
CPython representation. Source URLs, hashes and observation counts are in
manifest.json. The unmodified upstream LICENSE and CJK mapping notes are retained
in LICENSE.txt and NOTICE-cjkcodecs.txt. Unicode terms are retained alongside the
project's other Unicode data.

Stateful ISO-2022/HZ and UTF-7/escape/IDNA source encodings remain under a separate
production-contract audit. This table set is a measured decoder milestone; it
cannot close the entire Python source-codec or Rust-replacement feature ledger.
