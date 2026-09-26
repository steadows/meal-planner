from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from meals.contracts import ClaudeRunnerError

Response = dict[str, Any] | list[Any] | BaseModel | ClaudeRunnerError


@dataclass(frozen=True)
class ClaudeCall:
    prompt: str
    schema: type[BaseModel] | None
    chrome: bool
    timeout: int


class FakeClaudeRunner:
    """Stands in for `claude_runner.run`: replays queued responses in order and records every call."""

    def __init__(self, responses: Iterable[Response] = ()) -> None:
        self._responses: deque[Response] = deque(responses)
        self.calls: list[ClaudeCall] = []

    def queue(self, *responses: Response) -> None:
        self._responses.extend(responses)

    def run(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        chrome: bool = False,
        timeout: int = 600,
    ) -> Any:
        self.calls.append(ClaudeCall(prompt, schema, chrome, timeout))
        if not self._responses:
            raise AssertionError(f"FakeClaudeRunner: no response queued for prompt {prompt[:80]!r}")
        response = self._responses.popleft()
        if isinstance(response, ClaudeRunnerError):
            raise response
        payload = response.model_dump(mode="json") if isinstance(response, BaseModel) else response
        if schema is None:
            return payload
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise ClaudeRunnerError(
                f"output failed {schema.__name__} validation", raw_output=str(payload)
            ) from exc
