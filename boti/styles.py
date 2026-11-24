"""Validated edit controls; no operator-provided FFmpeg or ASS expressions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boti.editing import clean_text

RENDER_REVISION = 3
V1_FIELDS = {
    "caption_color",
    "caption_size",
    "caption_position",
    "caption_motion",
    "color_enhance",
    "progress_bar",
}
ACCENTS = {"classic": "66E8FF", "yellow": "00DFFF", "lime": "99EFD5", "cyan": "FFD47A"}
V2_FIELDS = V1_FIELDS | {
    "title_enabled",
    "title_text",
    "title_size",
    "title_appearance",
    "title_seconds",
    "title_full",
}


class EditStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caption_color: Literal["classic", "yellow", "lime", "cyan"] = "classic"
    caption_size: Literal["normal", "large"] = "normal"
    caption_position: Literal["lower", "middle"] = "lower"
    caption_motion: Literal["none", "pop"] = "none"
    color_enhance: bool = False
    progress_bar: bool = False
    vignette: Literal["off", "soft"] = "off"
    background_dim: bool = False
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
        if self.required_revision >= 3:
            return self.model_dump()
        return self.model_dump(include=V2_FIELDS if self.title_enabled else V1_FIELDS)

    @property
    def required_revision(self):
        if self.vignette != "off" or self.background_dim:
            return 3
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
