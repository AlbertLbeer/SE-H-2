import tempfile
import unittest
from pathlib import Path

from app.tools.file_writer import FileWriterTool


class FileWriterToolTests(unittest.TestCase):
    def test_writes_and_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = FileWriterTool(root)
            tool.write("nested/deep/file.py", "value = 1\n")

            self.assertEqual((root / "nested" / "deep" / "file.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_overwrites_existing_file_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "code.py"
            target.write_text("old\n", encoding="utf-8")
            tool = FileWriterTool(root)

            tool.write("code.py", "new\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            # 临时文件不应残留
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["code.py"])

    def test_rejects_path_outside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            tool = FileWriterTool(root)
            with self.assertRaises(ValueError):
                tool.write("../escaped.py", "x = 1\n")

    def test_rejects_content_over_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = FileWriterTool(root, max_bytes=4)
            with self.assertRaises(ValueError):
                tool.write("big.py", "12345")


if __name__ == "__main__":
    unittest.main()
