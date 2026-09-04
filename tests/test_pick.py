import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pick  # noqa: E402


class CaseGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        bank = root / "data" / "人力资源" / "题目"
        bank.mkdir(parents=True)
        self.ledger = root / "_错题本.md"

        common = {
            "科目": "人力资源",
            "知识点ID": "HR-KP0001",
            "知识点": "案例考点",
            "模块": "测试模块",
            "章节": "第1章",
            "题源类型": "测试",
            "考试年份": None,
            "题型": "案例分析题",
            "选项": {"A": "选项A", "B": "选项B"},
            "答案": "A",
            "解析": "案例子题解析",
            "案例组": "CASE-1",
            "案例材料": "这是原始案例材料。",
            "源页码": 1,
            "存疑": None,
            "质量": "可刷",
            "重题组": None,
        }
        rows = [
            dict(common, 题目ID="HR-C1", 题干="案例子题一", 卷内题号=1),
            dict(common, 题目ID="HR-C2", 题干="案例子题二", 卷内题号=2),
        ]
        with (bank / "测试.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        pick.BANK = str(root / "data")
        pick.WRONG = str(self.ledger)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def capture(func, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            func(args)
        return output.getvalue()

    def pick_args(self, **overrides):
        values = {
            "科目": "人力资源",
            "范围": None,
            "考点": None,
            "题型": None,
            "星级": None,
            "n": 1,
            "真题": False,
            "排除已做": False,
            "保留重题": False,
            "均匀": True,
            "藏答案": True,
            "seed": 1,
        }
        values.update(overrides)
        return SimpleNamespaceProxy(values)

    def test_pick_counts_case_as_one_question_and_expands_all_subquestions(self):
        output = self.capture(pick.cmd_pick, self.pick_args())

        self.assertIn("# 抽到 1 题", output)
        self.assertIn("HR-C1", output)
        self.assertIn("HR-C2", output)
        self.assertIn("1=HR-C1,HR-C2", output)
        self.assertIn("材料：这是原始案例材料。", output)
        self.assertEqual(output.count("材料：这是原始案例材料。"), 1)
        self.assertIn("HR-C1:用户答案 HR-C2:用户答案", output)

    def test_wrong_expands_wrong_subquestion_to_full_case(self):
        self.ledger.write_text(
            "## 做题流水（程序读，别手改这段）\n\n```tsv\n"
            "2026-09-04\tHR-C1\t错\tB\n```\n",
            encoding="utf-8",
        )
        args = SimpleNamespaceProxy(
            {"科目": "人力资源", "n": 1, "藏答案": True, "seed": None}
        )
        output = self.capture(pick.cmd_wrong, args)

        self.assertIn("HR-C1", output)
        self.assertIn("HR-C2", output)
        self.assertIn("1=HR-C1,HR-C2", output)
        self.assertIn("材料：这是原始案例材料。", output)
        self.assertEqual(output.count("材料：这是原始案例材料。"), 1)
        self.assertIn("HR-C1:用户答案 HR-C2:用户答案", output)

    def test_exclude_done_keeps_case_group_until_all_subquestions_done(self):
        self.ledger.write_text(
            "## 做题流水（程序读，别手改这段）\n\n```tsv\n"
            "2026-09-04\tHR-C1\t对\tA\n```\n",
            encoding="utf-8",
        )
        output = self.capture(pick.cmd_pick, self.pick_args(排除已做=True))
        self.assertIn("HR-C1", output)
        self.assertIn("HR-C2", output)

        self.ledger.write_text(
            "## 做题流水（程序读，别手改这段）\n\n```tsv\n"
            "2026-09-04\tHR-C1\t对\tA\n"
            "2026-09-04\tHR-C2\t对\tA\n```\n",
            encoding="utf-8",
        )
        output = self.capture(pick.cmd_pick, self.pick_args(排除已做=True))
        self.assertIn("没有符合条件的题", output)

    def test_case_subquestions_keep_separate_ledger_and_knowledge_entries(self):
        args = SimpleNamespaceProxy(
            {
                "记录": ["HR-C1:错:B", "HR-C2:对:A"],
                "日期": "2026-09-04",
            }
        )
        self.capture(pick.cmd_logs, args)
        book = self.ledger.read_text(encoding="utf-8")

        self.assertIn("HR-C1", book)
        self.assertIn("HR-C2", book)
        self.assertIn("2026-09-04\tHR-C1\t错\tB", book)
        self.assertIn("2026-09-04\tHR-C2\t对\tA", book)


class SimpleNamespaceProxy:
    def __init__(self, values):
        self.__dict__.update(values)


if __name__ == "__main__":
    unittest.main()
