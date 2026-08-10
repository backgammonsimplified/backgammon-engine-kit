from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "calculator_external_batch.R"
)
EXPECTED_SHA = "a385a963ed01a6eac083dae7a1b246b1c150b3eb"


class CalculatorBatchProvenanceStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_matching_expected_sha_is_the_only_resolved_commit_written(self):
        self.assertIn(f'expected_commit <- "{EXPECTED_SHA}"', self.source)
        self.assertIn("resolved_sha <- sha_metadata[[1]]", self.source)
        self.assertIn("resolved_release_commit = resolved_sha", self.source)
        self.assertNotIn("resolved_release_commit = expected_commit", self.source)

    def test_conflicting_sha_metadata_is_rejected(self):
        self.assertIn(
            "length(sha_metadata) != 1 || !identical(sha_metadata[[1]], expected_commit)",
            self.source,
        )
        self.assertIn("Calculator provenance mismatch", self.source)

    def test_matching_tag_without_sha_cannot_prove_resolved_commit(self):
        self.assertIn("if (length(sha_metadata) == 0)", self.source)
        self.assertIn("lacks immutable commit metadata", self.source)
        decision_block = self.source.split("sha_metadata <-", 1)[1].split(
            "provenance <-", 1
        )[0]
        self.assertNotIn("requested_ref %in%", decision_block)
        self.assertNotIn("remote_ref", decision_block.split("stop(sprintf", 1)[0])

    def test_matching_tag_with_conflicting_sha_is_rejected(self):
        self.assertIn(
            "sha_metadata <- unique(Filter(nzchar, c(remote_sha, github_sha1)))",
            self.source,
        )
        self.assertNotIn("provenance_ok", self.source)
        self.assertIn("!identical(sha_metadata[[1]], expected_commit)", self.source)


if __name__ == "__main__":
    unittest.main()
