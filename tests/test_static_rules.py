import unittest

from app.tools.static_rules import RULE_CATALOG, rule_of, rule_title, scan

CLEAN_CODE = '''
def total(values):
    """求和，输入为空时返回 0。"""
    result = 0
    for item in values:
        result += item
    return result


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()
'''


def rules_of(source):
    return [finding.rule_id for finding in scan(source, "sample.py")]


class StaticRulesTests(unittest.TestCase):
    def test_clean_code_has_no_findings(self):
        self.assertEqual(scan(CLEAN_CODE, "clean.py"), [])

    def test_detects_off_by_one(self):
        source = "def f(nums):\n    for i in range(len(nums) + 1):\n        print(nums[i])\n"
        findings = scan(source, "a.py")
        self.assertEqual([f.rule_id for f in findings], ["PY001"])
        self.assertEqual(findings[0].severity, "high")
        self.assertTrue(findings[0].auto_fixable)
        self.assertEqual(findings[0].line, 2)

    def test_no_off_by_one_for_correct_range(self):
        source = "def f(nums):\n    for i in range(len(nums)):\n        print(nums[i])\n"
        self.assertEqual(rules_of(source), [])

    def test_detects_mutable_default(self):
        source = "def f(item, cache=[]):\n    cache.append(item)\n    return cache\n"
        self.assertIn("PY002", rules_of(source))

    def test_detects_bare_except(self):
        source = "try:\n    value = 1\nexcept:\n    value = 0\n"
        self.assertIn("PY003", rules_of(source))

    def test_detects_eval_and_exec(self):
        self.assertIn("PY004", rules_of("result = eval('1 + 1')\n"))
        self.assertIn("PY004", rules_of("exec('print(1)')\n"))

    def test_detects_shell_true(self):
        source = "import subprocess\nsubprocess.run('ls', shell=True)\n"
        self.assertIn("PY005", rules_of(source))

    def test_detects_none_comparison(self):
        source = "def f(value):\n    if value != None:\n        return 1\n    return 0\n"
        self.assertIn("PY006", rules_of(source))

    def test_detects_os_system(self):
        source = "import os\nos.system('echo hi')\n"
        self.assertIn("PY007", rules_of(source))

    def test_detects_hardcoded_secret(self):
        source = 'API_TOKEN = "demo-token-value"\n'
        self.assertIn("PY008", rules_of(source))

    def test_detects_syntax_error(self):
        findings = scan("if value > 0\n    pass\n", "bad.py")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "PY009")
        self.assertTrue(findings[0].auto_fixable)
        self.assertEqual(findings[0].line, 1)

    def test_syntax_error_that_cannot_be_auto_fixed(self):
        findings = scan("def f(:\n    pass\n", "bad.py")
        self.assertEqual(findings[0].rule_id, "PY009")
        self.assertFalse(findings[0].auto_fixable)

    def test_detects_open_without_with(self):
        source = "def load(path):\n    return open(path).read()\n"
        self.assertIn("PY010", rules_of(source))

    def test_multi_issue_file_reports_all_rules(self):
        source = (
            "import os\n"
            'TOKEN = "abcdef123456"\n'
            "def f(items, cache=[]):\n"
            "    os.system('ls')\n"
            "    for i in range(len(items) + 1):\n"
            "        print(items[i])\n"
            "    try:\n"
            "        return eval('1')\n"
            "    except:\n"
            "        return None\n"
        )
        found = set(rules_of(source))
        self.assertTrue({"PY002", "PY003", "PY004", "PY007", "PY008", "PY001"} <= found)

    def test_findings_sorted_by_line(self):
        source = "import os\nos.system('ls')\nvalue = eval('1')\n"
        findings = scan(source, "a.py")
        lines = [f.line for f in findings]
        self.assertEqual(lines, sorted(lines))

    def test_rule_catalog_is_consistent(self):
        for rule_id, meta in RULE_CATALOG.items():
            self.assertRegex(rule_id, r"^PY\d{3}$")
            self.assertIn(meta["severity"], {"high", "medium", "low"})
            self.assertIsInstance(meta["auto_fixable"], bool)
            self.assertTrue(meta["title"])
            self.assertTrue(meta["suggestion"])
            self.assertEqual(rule_of(rule_id).title, rule_title(rule_id))


if __name__ == "__main__":
    unittest.main()
