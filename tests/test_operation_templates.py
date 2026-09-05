import unittest

from scripts.prompt_studio_core import OPERATION_INSTRUCTIONS, build_operation_instruction


MODELS = {
    "Auto / checkpoint default",
    "Pony / Illustrious",
    "NoobAI",
    "Flux",
    "Anima",
    "Krea 2",
}
OPERATIONS = {"Generate", "Convert", "Expand", "Polish"}


class OperationTemplateTests(unittest.TestCase):
    def test_every_model_has_every_operation_template(self):
        self.assertEqual(set(OPERATION_INSTRUCTIONS), OPERATIONS)
        for templates in OPERATION_INSTRUCTIONS.values():
            self.assertEqual(set(templates), MODELS)
            self.assertTrue(all(text.strip() for text in templates.values()))

    def test_unknown_values_fall_back_to_checkpoint_default(self):
        expected = OPERATION_INSTRUCTIONS["Convert"]["Auto / checkpoint default"]
        self.assertEqual(build_operation_instruction("unknown", "unknown"), expected)


if __name__ == "__main__":
    unittest.main()
