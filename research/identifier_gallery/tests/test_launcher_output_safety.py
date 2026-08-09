from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_oracle_gallery_output.py"
LAUNCHER = Path(__file__).parents[1] / "scripts" / "run_oracle_gallery.sh"


class LauncherOutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "engine-kit"
        (self.repo / "artifacts").mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def validate(self, output: str | Path | None) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(SCRIPT), str(self.repo)]
        if output is not None:
            command.append(str(output))
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def assert_rejected(self, output: str | Path) -> None:
        completed = self.validate(output)
        self.assertEqual(completed.returncode, 2, completed)
        self.assertIn("refusing unsafe gallery output", completed.stderr)

    def test_default_output_is_accepted(self):
        completed = self.validate(None)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            Path(completed.stdout.strip()),
            self.repo / "artifacts" / "oracle-identifier-comparison",
        )

    def test_normal_child_beneath_artifacts_is_accepted(self):
        output = self.repo / "artifacts" / "safe" / "run"
        completed = self.validate(output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(Path(completed.stdout.strip()), output)

    def test_repository_root_is_rejected(self):
        self.assert_rejected(self.repo)

    def test_artifacts_root_is_rejected(self):
        self.assert_rejected(self.repo / "artifacts")

    def test_parent_directory_is_rejected(self):
        self.assert_rejected(self.repo.parent)

    def test_arbitrary_external_directory_is_rejected(self):
        self.assert_rejected(self.root / "external")

    def test_artifacts_prefix_lookalike_is_rejected(self):
        self.assert_rejected(self.repo / "artifacts-evil" / "run")

    def test_filesystem_root_is_rejected(self):
        self.assert_rejected(Path(self.repo.anchor))

    def test_empty_output_is_rejected(self):
        self.assert_rejected("")

    def test_path_traversal_outside_artifacts_is_rejected(self):
        self.assert_rejected(self.repo / "artifacts" / ".." / "outside")

    def test_launcher_validates_canonical_output_before_recursive_removal(self):
        source = LAUNCHER.read_text(encoding="utf-8")
        validation = source.index("validate_oracle_gallery_output.py")
        removal = source.index('rm -rf "$OUTPUT"')
        self.assertLess(validation, removal)
        self.assertIn("${ORACLE_GALLERY_OUTPUT-", source)
        self.assertNotIn("${ORACLE_GALLERY_OUTPUT:-", source)


if __name__ == "__main__":
    unittest.main()
