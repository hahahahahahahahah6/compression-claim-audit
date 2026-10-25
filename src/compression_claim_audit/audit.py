"""Core audit logic: docs claims vs Python source evidence.

Static only: nothing is executed, no network is used, and .env file
contents are never opened.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import re
from pathlib import Path

VERSION = "0.1.0"

COMPRESSED_PHRASES = (
    "compressed",
    "compresses",
    "compressed output",
    "compressed data",
    "compressed files",
    "compressed storage",
    "gzip compressed",
)

UNCOMPRESSED_PHRASES = (
    "uncompressed",
    "not compressed",
    "no compression",
    "never compressed",
    "does not compress",
    "uncompressed output",
    "uncompressed data",
    "stored uncompressed",
    "stored without compression",
    "without compression",
)

NEGATION_WORDS = ("no", "not", "never", "without")

DEDICATED_MODULES = {"gzip", "zlib", "bz2", "lzma", "brotli", "zstandard"}
OPEN_MODULES = {"gzip", "bz2", "lzma"}


def _normalise(sentence: str) -> str:
    text = sentence.lower()
    text = text.replace("n't", " not")
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _phrase_re(phrase: str) -> re.Pattern:
    parts = [re.escape(word) for word in phrase.split()]
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b")


_PHRASE_RES = {p: _phrase_re(p) for p in COMPRESSED_PHRASES + UNCOMPRESSED_PHRASES}


def _strip_fenced_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def _sentences_from_lines(lines: list[str]) -> list[str]:
    sentences: list[str] = []
    for line in lines:
        # Inline code spans count as prose: drop the backticks only.
        line = line.replace("`", " ")
        for part in re.split(r"(?<=[.!?])\s+|\n", line):
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def extract_claims(docs_text: str) -> dict[str, list[str]]:
    """Return {family: [matched phrases]} for one docs text blob."""
    found: dict[str, list[str]] = {"compressed": [], "uncompressed": []}
    for sentence in _sentences_from_lines(_strip_fenced_lines(docs_text)):
        norm = _normalise(sentence)
        if not norm:
            continue
        tokens = set(re.findall(r"[a-z]+", norm))
        negated = bool(tokens & set(NEGATION_WORDS))
        for phrase in COMPRESSED_PHRASES:
            if _PHRASE_RES[phrase].search(norm):
                if not negated and phrase not in found["compressed"]:
                    found["compressed"].append(phrase)
        for phrase in UNCOMPRESSED_PHRASES:
            if _PHRASE_RES[phrase].search(norm):
                if phrase not in found["uncompressed"]:
                    found["uncompressed"].append(phrase)
    return {k: v for k, v in found.items() if v}


def collect_docs(root: Path) -> str:
    chunks: list[str] = []
    readme = root / "README.md"
    if readme.is_file() and not readme.is_symlink():
        chunks.append(readme.read_text(encoding="utf-8", errors="replace"))
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in sorted(docs_dir.rglob("*.md")):
            if path.is_file() and not path.is_symlink():
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _is_test_path(rel: Path) -> bool:
    parts = rel.parts
    if "tests" in parts or "fixtures" in parts:
        return True
    name = rel.name
    return name.startswith("test_") or name.endswith("_test.py")


def _is_excluded_path(rel: Path) -> bool:
    parts = rel.parts
    return "docs" in parts or "examples" in parts


def _ignored(rel_posix: str, ignore: list[str]) -> bool:
    for pattern in ignore:
        if fnmatch.fnmatch(rel_posix, pattern):
            return True
        if "/" not in pattern and fnmatch.fnmatch(rel_posix.split("/")[-1], pattern):
            return True
    return False


class _EvidenceVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.shadowed: set[str] = set()

    def _add(self, node: ast.AST, kind: str, detail: str) -> None:
        self.items.append(
            {"line": getattr(node, "lineno", 0), "kind": kind, "detail": detail}
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == "compress":
            self.shadowed.add("compress")
        for arg in list(node.args.args) + list(node.args.kwonlyargs):
            if arg.arg == "compress":
                self.shadowed.add("compress")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == "compress":
            self.shadowed.add("compress")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in DEDICATED_MODULES:
                self._add(node, "import", alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".")[0]
            if root in DEDICATED_MODULES:
                self._add(node, "import", node.module)
        self.generic_visit(node)

    def _mode_value(self, node: ast.Call) -> str | None:
        for keyword in node.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                return keyword.value.value if isinstance(keyword.value.value, str) else None
        return None

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            attr = func.attr
            receiver = func.value
            if attr in ("compress", "compressobj"):
                self._add(node, "call", attr)
            elif attr == "ZipFile":
                compression = None
                for keyword in node.keywords:
                    if keyword.arg == "compression":
                        compression = keyword.value
                if compression is not None:
                    if isinstance(compression, ast.Attribute) and compression.attr == "ZIP_STORED":
                        pass
                    elif isinstance(compression, ast.Name) and compression.id == "ZIP_STORED":
                        pass
                    else:
                        self._add(node, "call", "ZipFile(compression=...)")
            elif attr == "open" and isinstance(receiver, ast.Name) and receiver.id in OPEN_MODULES:
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value if isinstance(node.args[1].value, str) else None
                if mode is None:
                    mode = self._mode_value(node)
                if mode is not None and any(ch in mode for ch in "wxa"):
                    self._add(node, "call", f"{receiver.id}.open({mode!r})")
            elif attr == "open" and isinstance(receiver, ast.Name) and receiver.id == "tarfile":
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value if isinstance(node.args[1].value, str) else None
                if mode is None:
                    mode = self._mode_value(node)
                if mode is not None and any(tok in mode for tok in ("gz", "bz2", "xz")):
                    self._add(node, "call", f"tarfile.open({mode!r})")
        elif isinstance(func, ast.Name):
            if func.id == "ZipFile":
                compression = None
                for keyword in node.keywords:
                    if keyword.arg == "compression":
                        compression = keyword.value
                if compression is not None:
                    if isinstance(compression, ast.Attribute) and compression.attr == "ZIP_STORED":
                        pass
                    elif isinstance(compression, ast.Name) and compression.id == "ZIP_STORED":
                        pass
                    else:
                        self._add(node, "call", "ZipFile(compression=...)")
            elif func.id == "compress" and "compress" not in self.shadowed:
                self._add(node, "call", "compress")
        self.generic_visit(node)


def _file_evidence(path: Path) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    visitor = _EvidenceVisitor()
    # Pre-collect file-local shadowing names so a def anywhere in the
    # file suppresses Name-compress evidence anywhere in the file.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "compress":
                visitor.shadowed.add("compress")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                if arg.arg == "compress":
                    visitor.shadowed.add("compress")
    visitor.visit(tree)
    out: list[dict] = []
    seen: set[tuple] = set()
    for item in visitor.items:
        key = (item["line"], item["kind"], item["detail"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def audit(root: Path, ignore: list[str] | None = None) -> dict:
    ignore = ignore or []
    claims: dict[str, list[str]] = {"compressed": [], "uncompressed": []}
    for phrase_family, phrases in extract_claims(collect_docs(root)).items():
        claims[phrase_family] = phrases
    prod_evidence: list[dict] = []
    test_evidence: list[dict] = []
    prod_source_count = 0
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        rel_posix = rel.as_posix()
        if _ignored(rel_posix, ignore):
            continue
        if _is_excluded_path(rel):
            continue
        items = _file_evidence(path)
        annotated = [
            {"file": rel_posix, "line": it["line"], "kind": it["kind"], "detail": it["detail"]}
            for it in items
        ]
        if _is_test_path(rel):
            test_evidence.extend(annotated)
        else:
            prod_source_count += 1
            prod_evidence.extend(annotated)

    findings: list[dict] = []
    verified: list[dict] = []

    def _finding(rule: str, severity: str, evidence: list) -> dict:
        return {"rule": rule, "severity": severity, "evidence": evidence}

    claimed = [fam for fam in ("compressed", "uncompressed") if claims[fam]]
    for family in claimed:
        if prod_source_count == 0:
            if family == "compressed":
                findings.append(_finding("documented-compressed-claim-false", "HIGH", ["no-source"]))
            else:
                findings.append(_finding("documented-uncompressed-claim-false", "HIGH", ["no-source"]))
            continue
        if family == "compressed":
            if not prod_evidence and not test_evidence:
                findings.append(
                    _finding("documented-compressed-claim-false", "HIGH", ["no compression evidence"])
                )
            elif not prod_evidence:
                findings.append(
                    _finding("documented-compressed-claim-false-test-only", "MEDIUM", test_evidence)
                )
            else:
                verified.append({"family": family, "phrases": claims[family]})
        else:
            if prod_evidence:
                findings.append(
                    _finding("documented-uncompressed-claim-false", "HIGH", prod_evidence)
                )
            elif test_evidence:
                findings.append(
                    _finding(
                        "documented-uncompressed-claim-false-test-only", "MEDIUM", test_evidence
                    )
                )
            else:
                verified.append({"family": family, "phrases": claims[family]})

    status = "FAIL" if findings else "PASS"
    return {
        "status": status,
        "claims": {k: v for k, v in claims.items() if v},
        "findings": findings,
        "verified_claims": verified,
    }
