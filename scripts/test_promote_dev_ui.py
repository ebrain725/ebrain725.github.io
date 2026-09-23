import tempfile
import unittest
from pathlib import Path

from promote_dev_ui import parse_paths, stage


class PromotionTests(unittest.TestCase):
    def test_rejects_non_ui_and_traversal(self):
        for value in (
            "public/data/prices.csv",
            "public/data/wrong.js",
            "public/../scripts/sync.py",
            "public/assets/dev-environment.js",
            ".github/workflows/deploy-pages.yml",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_paths(value)

    def test_badge_removed_and_production_script_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dev, prod = root / "dev", root / "prod"
            (dev / "public").mkdir(parents=True)
            (prod / "public").mkdir(parents=True)
            (dev / "public/index.html").write_text(
                '<script src="assets/app.js"></script>'
                '<script src="assets/dev-environment.js?v=1" defer></script>',
                encoding="utf-8",
            )
            (prod / "public/index.html").write_text(
                '<script src="assets/app.js"></script>'
                '<script src="assets/policy-motie-extension.js"></script>',
                encoding="utf-8",
            )
            (prod / "public/assets").mkdir()
            (prod / "public/assets/app.js").write_text("", encoding="utf-8")
            (prod / "public/assets/policy-motie-extension.js").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "policy-motie-extension"):
                stage(dev, prod, "public/index.html")
            self.assertIn("policy-motie-extension.js", (prod / "public/index.html").read_text())
            (dev / "public/index.html").write_text(
                '<script src="assets/app.js"></script>'
                '<script src="assets/policy-motie-extension.js"></script>'
                '<script src="assets/dev-environment.js?v=1" defer></script>',
                encoding="utf-8",
            )
            stage(dev, prod, "public/index.html")
            result = (prod / "public/index.html").read_text()
            self.assertNotIn("dev-environment.js", result)
            self.assertIn("policy-motie-extension.js", result)


if __name__ == "__main__":
    unittest.main()
