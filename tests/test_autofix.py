import ast
import unittest

from app.tools.autofix import apply_fix
from app.tools.static_rules import scan


def fix_first(source, rule_id):
    finding = next(f for f in scan(source, "sample.py") if f.rule_id == rule_id)
    return apply_fix(source, finding)


class AutoFixTests(unittest.TestCase):
    def test_fix_off_by_one(self):
        source = "def f(nums):\n    for i in range(len(nums) + 1):\n        print(nums[i])\n"
        fixed, note = fix_first(source, "PY001")

        self.assertIsNotNone(fixed, note)
        self.assertIn("range(len(nums))", fixed)
        self.assertNotIn("+ 1", fixed)
        ast.parse(fixed)

    def test_fix_off_by_one_with_non_ascii_identifier(self):
        # ast 的 col_offset 是字节偏移，这里验证中文标识符下的切片位置仍然正确
        source = "def f(数据):\n    for i in range(len(数据) + 1):\n        print(数据[i])\n"
        fixed, note = fix_first(source, "PY001")

        self.assertIsNotNone(fixed, note)
        self.assertIn("range(len(数据))", fixed)
        ast.parse(fixed)

    def test_fix_bare_except(self):
        source = "try:\n    value = 1\nexcept:\n    value = 0\n"
        fixed, note = fix_first(source, "PY003")

        self.assertIsNotNone(fixed, note)
        self.assertIn("except Exception:", fixed)
        ast.parse(fixed)

    def test_fix_none_comparison(self):
        source = "def f(value):\n    if value == None:\n        return 1\n    return 0\n"
        fixed, note = fix_first(source, "PY006")

        self.assertIsNotNone(fixed, note)
        self.assertIn("value is None", fixed)
        ast.parse(fixed)

    def test_fix_missing_colon(self):
        source = "if value > 0\n    pass\n"
        fixed, note = fix_first(source, "PY009")

        self.assertIsNotNone(fixed, note)
        self.assertEqual(fixed, "if value > 0:\n    pass\n")
        ast.parse(fixed)

    def test_unfixable_rule_returns_none(self):
        fixed, note = fix_first("result = eval('1')\n", "PY004")

        self.assertIsNone(fixed)
        self.assertIn("没有对应的自动修复器", note)

    def test_fix_keeps_rest_of_file_untouched(self):
        source = (
            "# 头部注释\n"
            "def f(nums):\n"
            "    total = 0\n"
            "    for i in range(len(nums) + 1):\n"
            "        total += nums[i]\n"
            "    return total\n"
        )
        fixed, _ = fix_first(source, "PY001")

        self.assertIn("# 头部注释", fixed)
        self.assertTrue(fixed.endswith("    return total\n"))


if __name__ == "__main__":
    unittest.main()
