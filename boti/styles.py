"""Validated edit controls; no operator-provided FFmpeg or ASS expressions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boti.editing import clean_text

RENDER_REVISION = 2
V1_FIELDS = {
    "caption_color",
    "caption_size",
    "caption_position",
    "caption_motion",
    "color_enhance",
    "progress_bar",
}
ACCENTS = {"classic": "66E8FF", "yellow": "00DFFF", "lime": "99EFD5", "cyan": "FFD47A"}


class EditStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caption_color: Literal["classic", "yellow", "lime", "cyan"] = "classic"
    caption_size: Literal["normal", "large"] = "normal"
    caption_position: Literal["lower", "middle"] = "lower"
    caption_motion: Literal["none", "pop"] = "none"
    color_enhance: bool = False
    progress_bar: bool = False
    title_enabled: bool = False
    title_text: str = Field(default="", max_length=120)
    title_size: Literal["normal", "large"] = "normal"
    title_appearance: Literal["box", "outline"] = "box"
    title_seconds: int = Field(default=4, ge=1, le=15)
    title_full: bool = False

    @field_validator("title_text")
    @classmethod
    def plain_title(cls, value):
        return clean_text(value)

    def render_options(self):
        # v0.10 workers reject unknown style fields, even when those options are off.
        return self.model_dump() if self.title_enabled else self.model_dump(include=V1_FIELDS)

    @property
    def required_revision(self):
        if self.title_enabled:
            return 2
        return 1 if self.model_dump(include=V1_FIELDS) != EditStyle().model_dump(include=V1_FIELDS) else 0


def render_revision(payload):
    return max(
        payload.get("render_revision", 0),
        EditStyle.model_validate(payload.get("editing") or {}).required_revision,
    )


def top_title(payload):
    editing = EditStyle.model_validate(payload.get("editing") or {})
    if editing.title_enabled:
        return clean_text(payload.get("top_title_override") or editing.title_text or payload.get("title", ""))
    return clean_text(payload.get("title", "")) if (payload.get("rules") or {}).get("hook_title") else ""
