from django.test import TestCase

from .json_utils import extract_json_object, extract_response_text


class JsonUtilsTests(TestCase):
    def test_extracts_json_from_code_fence(self):
        data = extract_json_object("```json\n{\"ok\": true}\n```")
        self.assertEqual(data, {"ok": True})

    def test_extracts_response_text_from_output_items_when_output_text_missing(self):
        class Content:
            def __init__(self, text):
                self.text = text

        class Item:
            def __init__(self, *content):
                self.content = list(content)

        class Response:
            output_text = ""
            output = [Item(Content('{"ok": true}'))]

        self.assertEqual(extract_response_text(Response()), '{"ok": true}')
