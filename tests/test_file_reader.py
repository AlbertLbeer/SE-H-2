import tempfile
import unittest
from pathlib import Path

from app.tools.file_reader import FileReaderTool


class FileReaderToolTests(unittest.TestCase):
    def test_reads_utf8_file_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("print('ok')\n", encoding="utf-8")
            tool = FileReaderTool(root)
            self.assertEqual(tool.execute("sample.py"), "print('ok')\n")

    def test_rejects_path_outside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = FileReaderTool(root)
            with self.assertRaises(ValueError):
                tool.execute("../outside.py")

    def test_rejects_large_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text("12345", encoding="utf-8")
            tool = FileReaderTool(root, max_bytes=2)
            with self.assertRaises(ValueError):
                tool.execute("large.txt")


if __name__ == "__main__":
    unittest.main()

