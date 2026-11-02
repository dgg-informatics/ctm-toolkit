"""Fake OpenAI-compatible clients shared by the LLM stage tests."""
from types import SimpleNamespace


def _response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class QueuedClient:
    """Returns queued chat-completion responses in call order.

    A queued ``Exception`` instance is raised instead of returned.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.call_count += 1
        self.prompts.append(kwargs["messages"][1]["content"])
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _response(item)


class ContentFilterError(Exception):
    """Shaped like Azure's content-filter 400: ``code='content_filter'``."""
    code = "content_filter"
