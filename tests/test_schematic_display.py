import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from schematic_display import prepare_schematic_display


class SchematicDisplayTests(unittest.TestCase):
    def test_simple_default_and_inline_are_unchanged(self):
        text = "* RC\r\nR1 in out 1k\r\nC1 out 0 100n\r\n.end\r\n"
        self.assertEqual(prepare_schematic_display(text), text)
        self.assertEqual(prepare_schematic_display(text, "inline"), text)

    def test_dense_values_keep_precision_and_independent_parameters(self):
        text = "* ladder\n" + "".join(f"L{i} n{i} n{i+1} 14.763031u\n" for i in range(1, 9)) + ".end\n"
        result = prepare_schematic_display(text)
        for i in range(1, 9):
            self.assertIn(f"L{i} n{i} n{i+1} {{L{i}v}}", result)
            self.assertIn(f"L{i}v=14.763031u", result)
        self.assertEqual(prepare_schematic_display(result), result)
        self.assertEqual(prepare_schematic_display(text, "inline"), text)
        self.assertTrue(all(len(line) <= 88 for line in result.splitlines() if line.startswith(".param")))
        edited = result.replace("L1 n1 n2 {L1v}", "L1 n1 n2 15.987654321u")
        refreshed = prepare_schematic_display(edited)
        self.assertIn("L1 n1 n2 {L1v_2}", refreshed)
        self.assertIn("L1v_2=15.987654321u", refreshed)
        self.assertEqual(prepare_schematic_display(refreshed), refreshed)

    def test_collision_scopes_expressions_options_and_comments(self):
        text = ("* scoped\r\n.param R1V=123 base=10\r\n"
                "R1 in out 1.234567890k ; exact\r\nC1 out 0 {base}\r\n"
                "L1 in x {base * 2}\r\nR2 x 0 10 tc=0.1\r\n"
                "R3 x 0 20\r\n+ tc=0.2\r\n"
                ".subckt child a b\r\nR4 a b 9.87654321k\r\n.ends child\r\n.end\r\n")
        result = prepare_schematic_display(text, "compact")
        self.assertIn("R1 in out {R1v_2} ; exact\r\n", result)
        self.assertIn(".param R1v_2=1.234567890k\r\n", result)
        self.assertIn("hide=R1,C1\r\n", result)
        for card in ["C1 out 0 {base}", "L1 in x {base * 2}", "R2 x 0 10 tc=0.1", "R3 x 0 20", "R4 a b 9.87654321k"]:
            self.assertIn(card + "\r\n", result)
        self.assertNotIn("\n", result.replace("\r\n", ""))
        inline = prepare_schematic_display(result, "inline")
        self.assertNotIn("display=compact", inline)
        self.assertIn("R1v_2=1.234567890k", inline)

    def test_short_dense_values_do_not_automatically_hide(self):
        text = "* resistors\n" + "".join(f"R{i} n{i} 0 1k\n" for i in range(1, 9)) + ".end\n"
        self.assertEqual(prepare_schematic_display(text), text)


if __name__ == "__main__":
    unittest.main()
