import tempfile
import unittest
from pathlib import Path

from pipeline.production_guide import ensure_agent_guide, published_task_slots


REQUIRED = (
    "task.toml",
    "instruction.md",
    "solution/solution.patch",
    "tests/test.sh",
)


def make_task(root: Path, number: int, complete: bool = True) -> None:
    task = root / "output" / f"task-{number:04d}"
    for relative in REQUIRED if complete else REQUIRED[:2]:
        path = task / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")


class ProductionGuideTests(unittest.TestCase):
    def test_published_task_slots_ignores_incomplete_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_task(root, 1)
            make_task(root, 2, complete=False)
            self.assertEqual(published_task_slots(root), ["task-0001"])


    def test_guide_is_created_once_at_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(1, 16):
                make_task(root, number)

            first = ensure_agent_guide(root)
            guide = root / "AGENTS.md"
            content = guide.read_text(encoding="utf-8")

            self.assertEqual(first["count"], 15)
            self.assertTrue(first["generated"])
            self.assertIn("600-1500", content)
            self.assertNotIn("ANTHROPIC_API_KEY", content)

            second = ensure_agent_guide(root)
            self.assertFalse(second["generated"])
            self.assertTrue(second["existing"])
            self.assertEqual(guide.read_text(encoding="utf-8"), content)


    def test_guide_is_not_created_before_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(1, 15):
                make_task(root, number)

            result = ensure_agent_guide(root)

            self.assertEqual(result["count"], 14)
            self.assertFalse(result["generated"])
            self.assertFalse((root / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
