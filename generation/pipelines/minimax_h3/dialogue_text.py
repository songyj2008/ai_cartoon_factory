"""Keep spoken dialogue opaque to visual reference transformations."""
import re
from typing import Callable


_DIALOGUE_RE = re.compile(r"(<d\s*>.*?</d\s*>)", re.IGNORECASE | re.DOTALL)


def transform_outside_dialogue(text: str, transform: Callable[[str], str]) -> str:
    parts = _DIALOGUE_RE.split(text)
    return "".join(part if index % 2 else transform(part) for index, part in enumerate(parts))
