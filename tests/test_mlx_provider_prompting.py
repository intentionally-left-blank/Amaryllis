from __future__ import annotations

import unittest

from models.providers.mlx_provider import MLXProvider


class _FakeTokenizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool = False,
        add_generation_prompt: bool = False,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "tokenize": tokenize,
                "add_generation_prompt": add_generation_prompt,
            }
        )
        prompt_lines = [f"{item['role']}: {item['content']}" for item in messages]
        if add_generation_prompt:
            prompt_lines.append("assistant:")
        return "\n".join(prompt_lines)


class MLXProviderPromptingTests(unittest.TestCase):
    def test_build_prompt_prefers_chat_template(self) -> None:
        tokenizer = _FakeTokenizer()
        prompt = MLXProvider._build_prompt(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ],
            tokenizer=tokenizer,
        )

        self.assertIn("assistant:", prompt)
        self.assertEqual(len(tokenizer.calls), 1)
        call = tokenizer.calls[0]
        self.assertEqual(call["tokenize"], False)
        self.assertEqual(call["add_generation_prompt"], True)

    def test_normalize_generation_output_cuts_next_user_turn(self) -> None:
        raw = (
            "There are about 7.5 quintillion grains of sand.\n"
            "USER: wow\n"
            "ASSISTANT: indeed"
        )
        normalized = MLXProvider._normalize_generation_output(raw, prompt="")
        self.assertEqual(normalized, "There are about 7.5 quintillion grains of sand.")

    def test_normalize_generation_output_cuts_russian_turn_markers(self) -> None:
        raw = (
            "Угловой момент - это мера вращения.\n\n"
            "ПОЛЬЗОВАТЕЛЬ: А что по оси X?\n"
            "АССИСТЕНТ: По оси X ..."
        )
        normalized = MLXProvider._normalize_generation_output(raw, prompt="")
        self.assertEqual(normalized, "Угловой момент - это мера вращения.")

    def test_normalize_generation_output_removes_assistant_prefix(self) -> None:
        raw = "ASSISTANT: Sure, here is the answer."
        normalized = MLXProvider._normalize_generation_output(raw, prompt="")
        self.assertEqual(normalized, "Sure, here is the answer.")

    def test_build_prompt_fallback_format(self) -> None:
        prompt = MLXProvider._build_prompt(
            messages=[
                {"role": "user", "content": "Ping"},
            ],
            tokenizer=object(),
        )
        self.assertIn("USER: Ping", prompt)
        self.assertTrue(prompt.endswith("ASSISTANT:"))


if __name__ == "__main__":
    unittest.main()
