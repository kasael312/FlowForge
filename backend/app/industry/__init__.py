"""Industry packs registry. Importing this package registers the built-in packs."""

from app.industry import gym_spa  # noqa: F401  (registers gym / spa / both)
from app.industry.base import COMMON_QUESTIONS, IndustryPack, Question, packs

__all__ = ["COMMON_QUESTIONS", "IndustryPack", "Question", "packs"]
