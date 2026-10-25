"""Structured outputs each agent must return (validated with pydantic)."""
from pydantic import BaseModel, Field


class Explanation(BaseModel):
    """Tactical Analyst: why a moment matters."""
    why_it_matters: str = Field(description="1-2 sentences on why this moment matters")
    tactical_context: str = Field(description="1 sentence of tactical context")
    significance: int = Field(description="1 (minor) to 5 (match-defining)")
    tags: list[str] = Field(description="short lowercase keywords, e.g. momentum, pressing")


class Narration(BaseModel):
    """Broadcast Narrator: on-screen text for two audiences."""
    caption: str = Field(description="on-screen caption, at most 70 characters")
    commentary: str = Field(description="1-2 sentences in a TV commentator's voice")
    fan_take: str = Field(description="1 plain-language sentence for a casual fan")
    analyst_take: str = Field(description="1-2 data-rich sentences for an analyst")


class Verdict(BaseModel):
    """Fact Checker: does the narration agree with the data?"""
    approved: bool
    problems: list[str] = Field(default_factory=list)


class LocalizedText(BaseModel):
    language: str = Field(description="language code, e.g. fr")
    caption: str
    commentary: str
    fan_take: str


class Localization(BaseModel):
    """Localizer: the same narration in other languages."""
    items: list[LocalizedText]


class Recap(BaseModel):
    """Recap Writer: half-time or full-time story."""
    headline: str
    summary: str
    key_moments: list[str]
    tactical_story: str
    player_of_the_match: str = ""


class PersonalizedView(BaseModel):
    """Personalizer: one moment re-shaped for one viewer."""
    caption: str
    text: str
    why_you_care: str


class PlayerSpotlight(BaseModel):
    """Player Spotlight: a followed player's match so far."""
    headline: str
    text: str
    standout_stats: list[str]
