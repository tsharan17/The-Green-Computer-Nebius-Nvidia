"""What the platform hands over, and how much to trust each file.

Labels (shown everywhere a file is offered):
  generated_validated  produced here AND checked by running it (e.g. an INT8 model whose outputs
                       were compared with the original on synthetic inputs)
  generated            data produced here from stored analyses (reports, plans, comparisons, traces)
  template             instructions or scripts to adapt and run yourself; nothing was executed
Nothing is ever labelled as a changed model, PCB, RTL or chip design unless that transformation
really ran and was validated here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Label = Literal["generated_validated", "generated", "template"]

LABEL_TEXT = {
    "generated_validated": "Generated and validated here",
    "generated": "Generated from your stored analysis",
    "template": "Template: adapt and run it yourself (not executed here)",
}


@dataclass
class GeneratedArtifact:
    filename: str
    content: bytes
    media_type: str
    label: Label
    description: str
    validation: dict[str, Any] = field(default_factory=dict)


class ArtifactGenerator(Protocol):
    kind: str

    def generate(self, ctx: Any) -> GeneratedArtifact: ...
