"""Validated edit controls; no operator-provided FFmpeg or ASS expressions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

RENDER_REVISION = 1
ACCENTS = {"classic": "66E8FF", "yellow": "00DFFF", "lime": "99EFD5", "cyan": "FFD47A"}


class EditStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caption_color: Literal["classic", "yellow", "lime", "cyan"] = "classic"
    caption_size: Literal["normal", "large"] = "normal"
    caption_position: Literal["lower", "middle"] = "lower"
    caption_motion: Literal["none", "pop"] = "none"
    color_enhance: bool = False
    progress_bar: bool = False

    @property
    def required_revision(self):
        return RENDER_REVISION if self != EditStyle() else 0


def render_revision(payload):
    return max(
        payload.get("render_revision", 0),
        EditStyle.model_validate(payload.get("editing") or {}).required_revision,
    )
