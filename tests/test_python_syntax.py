import json
import tempfile
import unittest
from pathlib import Path

from app.tools.file_reader import FileReaderTool
from app.tools.python_syntax import PythonSyntaxTool


class PythonSyntaxToolTests(unittest.TestCase):
    def test_valid_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valid.py").write_text("value = 1 + 2\n", encoding="utf-8")
            result = json.loads(PythonSyntaxTool(FileReaderTool(root)).execute("valid.py"))
            self.assertTrue(result["ok"])

    def test_reports_syntax_error_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "invalid.py").write_text("if True print('bad')\n", encoding="utf-8")
            result = json.loads(PythonSyntaxTool(FileReaderTool(root)).execute("invalid.py"))
            self.assertFalse(result["ok"])
            self.assertIsInstance(result["line"], int)
            self.assertIsInstance(result["column"], int)
            self.assertTrue(result["message"])


if __name__ == "__main__":
    unittest.main()

