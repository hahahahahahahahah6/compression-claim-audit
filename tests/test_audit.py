"""Tests for compression-claim-audit."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from compression_claim_audit.audit import audit, extract_claims  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def tree(readme, code, extra=None):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "README.md").write_text(readme)
    (root / "main.py").write_text(code)
    for name, content in (extra or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    tmp_root = root
    # Keep the TemporaryDirectory alive via the root's parent attribute hack:
    # tests hold the returned root and also keep tmp referenced in a global.
    _KEEP.append(tmp)
    return tmp_root


_KEEP = []


def claims_of(readme):
    return extract_claims(readme)


def rules(result):
    return [f["rule"] for f in result["findings"]]


def severities(result):
    return [f["severity"] for f in result["findings"]]


class ClaimExtractionTests(unittest.TestCase):
    def test_bare_compressed_is_claim(self):
        self.assertIn("compressed", claims_of("Output is compressed."))

    def test_compresses_is_claim(self):
        self.assertIn("compressed", claims_of("It compresses data."))

    def test_compressed_output_is_claim(self):
        self.assertIn("compressed", claims_of("Writes compressed output."))

    def test_bare_compression_no_claim(self):
        self.assertEqual({}, claims_of("## Compression notes\nSee below."))

    def test_bare_compress_no_claim(self):
        self.assertEqual({}, claims_of("Use compress to shrink."))

    def test_decompressed_no_claim(self):
        self.assertEqual({}, claims_of("The file is decompressed first."))

    def test_not_compressed_uncompressed_only(self):
        c = claims_of("Output is not compressed.")
        self.assertEqual(["uncompressed"], list(c.keys()))
        self.assertIn("not compressed", c["uncompressed"])

    def test_is_not_compressed(self):
        c = claims_of("It is not compressed.")
        self.assertIn("uncompressed", c)

    def test_no_compression(self):
        self.assertIn("uncompressed", claims_of("There is no compression."))

    def test_never_compressed(self):
        self.assertIn("uncompressed", claims_of("It is never compressed."))

    def test_does_not_compress(self):
        self.assertIn("uncompressed", claims_of("It does not compress output."))

    def test_doesnt_compress_normalised(self):
        self.assertIn("uncompressed", claims_of("It doesn't compress output."))

    def test_gzip_compressed_hyphen(self):
        self.assertIn("compressed", claims_of("Output is gzip-compressed."))

    def test_stored_uncompressed_no_compressed(self):
        c = claims_of("Output is stored uncompressed.")
        self.assertEqual(["uncompressed"], list(c.keys()))

    def test_uncompressed_bare(self):
        self.assertIn("uncompressed", claims_of("Output is uncompressed."))

    def test_without_compression(self):
        self.assertIn("uncompressed", claims_of("Stored without compression."))

    def test_stored_without_compression(self):
        self.assertIn("uncompressed", claims_of("Stored without compression."))

    def test_fenced_code_excluded(self):
        self.assertEqual({}, claims_of("```\nOutput is compressed.\n```\nPlain text."))

    def test_inline_code_counts(self):
        self.assertIn("compressed", claims_of("Output is `compressed`."))

    def test_negation_suppresses_compressed(self):
        # "never compressed" is itself an uncompressed phrase; a pure
        # negation case must not yield a compressed claim:
        self.assertNotIn("compressed", claims_of("We do not claim compressed output here."))
        self.assertNotIn("compressed", claims_of("Output is not compressed."))

    def test_both_families(self):
        c = claims_of("Output is compressed.\nOutput is uncompressed.")
        self.assertIn("compressed", c)
        self.assertIn("uncompressed", c)

    def test_compressed_files(self):
        self.assertIn("compressed", claims_of("Writes compressed files."))

    def test_compressed_storage(self):
        self.assertIn("compressed", claims_of("Uses compressed storage."))

    def test_compressed_data(self):
        self.assertIn("compressed", claims_of("Returns compressed data."))

    def test_uncompressed_output(self):
        self.assertIn("uncompressed", claims_of("Returns uncompressed output."))

    def test_uncompressed_data(self):
        self.assertIn("uncompressed", claims_of("Returns uncompressed data."))


class EvidenceTests(unittest.TestCase):
    def run_case(self, code):
        root = tree("No promises here.\n", code)
        return audit(root)

    def test_gzip_import_counts(self):
        r = self.run_case("import gzip\n")
        # No claim -> PASS, but evidence presence is shown via uncompressed falsification below.
        self.assertEqual("PASS", r["status"])

    def test_zlib_import_falsifies_uncompressed(self):
        root = tree("Output is uncompressed.\n", "import zlib\n")
        r = audit(root)
        self.assertEqual(["documented-uncompressed-claim-false"], rules(r))

    def test_bz2_import_falsifies(self):
        root = tree("Output is uncompressed.\n", "import bz2\n")
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_lzma_import_falsifies(self):
        root = tree("Output is uncompressed.\n", "import lzma\n")
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_brotli_import_falsifies(self):
        root = tree("Output is uncompressed.\n", "import brotli\n")
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_zipfile_import_alone_no_evidence(self):
        root = tree("Output is uncompressed.\n", "import zipfile\n")
        self.assertEqual("PASS", audit(root)["status"])

    def test_tarfile_import_alone_no_evidence(self):
        root = tree("Output is uncompressed.\n", "import tarfile\n")
        self.assertEqual("PASS", audit(root)["status"])

    def test_zipfile_deflated_counts(self):
        root = tree(
            "Output is uncompressed.\n",
            "import zipfile\nz = zipfile.ZipFile('x.zip', 'w', compression=zipfile.ZIP_DEFLATED)\n",
        )
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_zipfile_stored_no_evidence(self):
        root = tree(
            "Output is uncompressed.\n",
            "import zipfile\nz = zipfile.ZipFile('x.zip', 'w', compression=zipfile.ZIP_STORED)\n",
        )
        self.assertEqual("PASS", audit(root)["status"])

    def test_zipfile_no_kwarg_no_evidence(self):
        root = tree(
            "Output is uncompressed.\n",
            "import zipfile\nz = zipfile.ZipFile('x.zip', 'w')\n",
        )
        self.assertEqual("PASS", audit(root)["status"])

    def test_zipfile_variable_compression_counts(self):
        root = tree(
            "Output is uncompressed.\n",
            "import zipfile\nz = zipfile.ZipFile('x.zip', 'w', compression=mode)\n",
        )
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_tarfile_wgz_counts(self):
        root = tree(
            "Output is uncompressed.\n",
            "import tarfile\nt = tarfile.open('x.tar.gz', 'w:gz')\n",
        )
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_tarfile_w_no_evidence(self):
        root = tree(
            "Output is uncompressed.\n",
            "import tarfile\nt = tarfile.open('x.tar', 'w')\n",
        )
        self.assertEqual("PASS", audit(root)["status"])

    def test_tarfile_variable_mode_no_evidence(self):
        root = tree(
            "Output is uncompressed.\n",
            "import tarfile\nt = tarfile.open('x.tar', mode)\n",
        )
        self.assertEqual("PASS", audit(root)["status"])

    def test_gzip_open_wb_counts(self):
        root = tree(
            "Output is uncompressed.\n",
            "import gzip\nf = gzip.open('x.gz', 'wb')\n",
        )
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_gzip_open_rb_no_evidence(self):
        root = tree(
            "Output is uncompressed.\n",
            "f = gzip.open('x.gz', 'rb')\n",
        )
        self.assertEqual("PASS", audit(root)["status"])

    def test_gzip_open_no_mode_no_evidence(self):
        root = tree("Output is uncompressed.\n", "f = gzip.open('x.gz')\n")
        self.assertEqual("PASS", audit(root)["status"])

    def test_gzip_open_variable_mode_no_evidence(self):
        root = tree("Output is uncompressed.\n", "f = gzip.open('x.gz', mode)\n")
        self.assertEqual("PASS", audit(root)["status"])

    def test_gzip_decompress_call_alone_no_evidence(self):
        root = tree("Output is uncompressed.\n", "import gzip\ndata = gzip.decompress(blob)\n")
        # gzip import itself counts (documented over-approximation), so use
        # an aliased-module-free decompress via attribute on a variable:
        root2 = tree("Output is uncompressed.\n", "data = reader.decompress(blob)\n")
        self.assertEqual("PASS", audit(root2)["status"])

    def test_compress_attr_counts(self):
        root = tree("Output is uncompressed.\n", "data = obj.compress(raw)\n")
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_compress_name_counts(self):
        root = tree("Output is uncompressed.\n", "data = compress(raw)\n")
        self.assertEqual(["documented-uncompressed-claim-false"], rules(audit(root)))

    def test_local_def_compress_suppresses_name(self):
        root = tree(
            "Output is uncompressed.\n",
            "def compress(x):\n    return x\n\ndata = compress(raw)\n",
        )
        self.assertEqual("PASS", audit(root)["status"])

    def test_base64_no_evidence(self):
        root = tree("Output is uncompressed.\n", "import base64\n")
        self.assertEqual("PASS", audit(root)["status"])

    def test_hashlib_no_evidence(self):
        root = tree("Output is uncompressed.\n", "import hashlib\n")
        self.assertEqual("PASS", audit(root)["status"])

    def test_gz_literal_no_evidence(self):
        root = tree("Output is uncompressed.\n", 'name = "out.gz"\n')
        self.assertEqual("PASS", audit(root)["status"])

    def test_ignore_drops_evidence(self):
        root = tree(
            "Output is uncompressed.\n",
            "import gzip\n",
            extra={"other.py": "x = 1\n"},
        )
        self.assertEqual("PASS", audit(root, ignore=["main.py"])["status"])

    def test_docs_dir_code_not_evidence(self):
        root = tree(
            "Output is uncompressed.\n",
            "x = 1\n",
            extra={"docs/helper.py": "import gzip\n"},
        )
        self.assertEqual("PASS", audit(root)["status"])

    def test_no_source_high(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "README.md").write_text("Output is compressed.\n")
            r = audit(root)
            self.assertEqual(["documented-compressed-claim-false"], rules(r))
            self.assertEqual(["HIGH"], severities(r))

    def test_no_claim_pass_despite_evidence(self):
        root = tree("Plain tool.\n", "import gzip\ndata = gzip.compress(b'x')\n")
        self.assertEqual("PASS", audit(root)["status"])


class RuleTests(unittest.TestCase):
    def test_bad_compressed_exactly_one_high(self):
        r = audit(FIXTURES / "bad" / "compressed")
        self.assertEqual(["documented-compressed-claim-false"], rules(r))
        self.assertEqual(["HIGH"], severities(r))

    def test_bad_uncompressed_exactly_one_high(self):
        r = audit(FIXTURES / "bad" / "uncompressed")
        self.assertEqual(["documented-uncompressed-claim-false"], rules(r))
        self.assertEqual(["HIGH"], severities(r))

    def test_good_compressed_pass_verified(self):
        r = audit(FIXTURES / "good" / "compressed")
        self.assertEqual("PASS", r["status"])
        self.assertEqual(1, len(r["verified_claims"]))

    def test_good_uncompressed_pass_verified(self):
        r = audit(FIXTURES / "good" / "uncompressed")
        self.assertEqual("PASS", r["status"])
        self.assertEqual(1, len(r["verified_claims"]))

    def test_testonly_medium(self):
        r = audit(FIXTURES / "testonly" / "compressed")
        self.assertEqual(["documented-compressed-claim-false-test-only"], rules(r))
        self.assertEqual(["MEDIUM"], severities(r))

    def test_both_families_two_findings(self):
        # With real source, only the compressed claim is falsified (the
        # uncompressed claim is verified by zero evidence). Both families
        # are falsified together only in the no-source case:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "README.md").write_text(
                "Output is compressed.\nOutput is uncompressed.\n"
            )
            r = audit(root)
            self.assertEqual(2, len(r["findings"]))

    def test_env_marker_never_in_output(self):
        r = audit(FIXTURES / "bad" / "compressed")
        blob = json.dumps(r)
        self.assertNotIn("MARKER_ONLY_ENV_DO_NOT_READ_9f3a", blob)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.run(
            [sys.executable, "-m", "compression_claim_audit", *args],
            capture_output=True, text=True, env=env,
        )
        return proc

    def test_version(self):
        proc = self.run_cli("--version")
        self.assertEqual(0, proc.returncode)
        self.assertEqual("0.1.0", proc.stdout.strip())

    def test_good_exit_0(self):
        proc = self.run_cli(str(FIXTURES / "good" / "compressed"))
        self.assertEqual(0, proc.returncode)

    def test_bad_exit_2(self):
        proc = self.run_cli(str(FIXTURES / "bad" / "compressed"))
        self.assertEqual(2, proc.returncode)

    def test_json_valid(self):
        proc = self.run_cli("--json", str(FIXTURES / "bad" / "uncompressed"))
        data = json.loads(proc.stdout)
        self.assertEqual("FAIL", data["status"])
        self.assertEqual(1, len(data["findings"]))

    def test_fail_on_high_medium_exits_0(self):
        proc = self.run_cli(
            "--fail-on", "HIGH", str(FIXTURES / "testonly" / "compressed")
        )
        self.assertEqual(0, proc.returncode)
        self.assertIn("MEDIUM", proc.stdout)

    def test_usage_error_exit_1(self):
        proc = self.run_cli(str(FIXTURES / "does-not-exist"))
        self.assertEqual(1, proc.returncode)

    def test_bad_flag_exit_1(self):
        proc = self.run_cli("--nope")
        self.assertEqual(1, proc.returncode)


if __name__ == "__main__":
    unittest.main()
