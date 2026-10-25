# compression-claim-audit

A stdlib-only Python CLI that offline-audits documented size-reduction
promises in a project's docs against its Python source. It executes
nothing, uses no network, never reads `.env` file contents, and never
opens a file outside the audited tree except to read it for static
analysis.

The question it answers is narrow: docs promise that output is stored
in size-reduced form, or that output is stored at full size — does the
code actually do that? A document can promise a format such as JSON and
still be silent about whether the bytes are shrunk; shrinking is a
separate promise from format, from read pattern, and from cipher
protection.

## Usage

```sh
pip install compression-claim-audit
compression-claim-audit /path/to/project
compression-claim-audit --json /path/to/project
compression-claim-audit --fail-on HIGH /path/to/project
compression-claim-audit --ignore "tests/*" /path/to/project
```

Exit codes: `0` PASS, `2` FAIL, `1` usage error. Flags: `--json`,
`--verbose`, `--ignore` (glob, repeatable, matched against relative
paths), `--fail-on HIGH|MEDIUM` (default `MEDIUM`: any finding fails;
`--fail-on HIGH` makes a MEDIUM-only run exit `0` while still printing
the MEDIUM findings), `--version`.

Docs sources are the root `README.md` plus `docs/**/*.md` only.
Fenced code blocks are excluded from promises; inline code spans count
as prose. Production Python source excludes `tests/`, `test_*.py`,
`*_test.py`, `fixtures/`, `docs/`, `examples/`, and symlinked files.
Evidence found only under `tests/`/`fixtures/` is reported separately
as a test-only finding.

## Rules

The exact promise phrases and rule names live only in the fenced block
below, so that running this tool on its own tree passes with no
promises found. Prose in this README deliberately paraphrases them.

```
Promise families (normalised: lowercase, n't -> not, hyphens -> spaces):
  size-reduced family:
    compressed, compresses, compressed output, compressed data,
    compressed files, compressed storage, gzip compressed
  full-size family:
    uncompressed, not compressed, no compression, never compressed,
    does not compress, uncompressed output, uncompressed data,
    stored uncompressed, stored without compression, without compression

Rules:
  documented-compressed-claim-false (HIGH):
    size-reduced promise present, zero production shrinking evidence,
    zero test-only shrinking evidence
  documented-compressed-claim-false-test-only (MEDIUM):
    size-reduced promise present, zero production evidence,
    test-only evidence present
  documented-uncompressed-claim-false (HIGH):
    full-size promise present, production shrinking evidence present
  documented-uncompressed-claim-false-test-only (MEDIUM):
    full-size promise present, zero production evidence,
    test-only evidence present

Shrinking evidence (one item per import statement or call):
  - import of gzip, zlib, bz2, lzma, brotli, or zstandard
  - .compress / .compressobj attribute call on any receiver, or a
    bare compress() call (unless shadowed by a file-local def/class/
    parameter of the same name)
  - ZipFile() call with a compression keyword that is not ZIP_STORED
  - tarfile.open() with a mode literal containing gz, bz2, or xz
  - gzip.open / bz2.open / lzma.open with a write-mode literal
    (containing w, x, or a)
```

A promise family present in a tree with no production Python source at
all yields one HIGH finding for that family. A project with shrinking
evidence but no size-reduction promise yields no finding. A full-size
promise with zero shrinking evidence anywhere is verified, not a
finding. Verified promises are listed in `--json` output as
`verified_claims`.

Matching is word-boundary matching on normalised text, not substring
matching: a full-size word does not accidentally match the
size-reduced word inside it, and a read-back word alone is not a
promise. Negation is asymmetric: a sentence containing a negation word
(`no`, `not`, `never`, `without`) suppresses size-reduced promises on
that sentence, but does not suppress full-size promises, because the
full-size phrases are themselves negative wordings.

## Differentiation

- format-claim-audit: data formats, never whether bytes are shrunk.
- streaming-claim-audit: read patterns and memory, never shrinking.
- encryption-claim-audit: cipher protection, never shrinking.
- readonly-claim-audit: filesystem writes at all, never shrinking of
  what is written.
- bandit/ruff: code issues, never docs promises.
- gzip / zlib / zipfile / tarfile documentation: libraries that provide
  shrinking, never docs-vs-code truth.

## Honest limitations

- Promise detection is exact-phrase matching on normalised prose, not
  semantic judgement; reworded promises outside the listed phrases are
  missed.
- An import of a shrinking module counts as evidence even if the code
  only ever reads shrunken input back; this over-approximation is
  deliberate and documented.
- Dead code counts: a shrinking call in uncalled code is still evidence.
- Receiver types are not inferred: a `.compress` call on any receiver
  counts, and a file-local `compress` name only suppresses the bare
  `compress()` call form.
- A container-format import alone is not evidence: a zip or tar archive
  can store at full size, so only a shrinking keyword or mode counts.
- Variable modes and variable keyword values are handled as specified
  in the fenced evidence table; unknown values are skipped, except that
  a variable ZipFile compression keyword counts (documented
  over-approximation).
- String literals and comments never count as evidence; only AST
  imports and calls count.
- This tool checks shrinking-use existence, not whether output is
  actually smaller: no size ratio is measured.
- Dynamic imports, aliased-module method calls (for example an aliased
  module's open call), and third-party shrinking libraries outside the
  listed table are out of scope.

## Development

```sh
python3 -m build
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
