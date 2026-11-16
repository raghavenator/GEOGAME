"""The homepage feed: an ordered, data-driven list of full-screen cards.

Each entry is either a registry game (rendered with its embed macros and
preloaded) or a placeholder card. To add a game to the feed, give its GameSpec
``embed_template``/``embed_context`` and add ``game("<id>", tint=...)`` below.
The list is validated against the registry when the app starts.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.shared.game import GameSpec

# Background tints a card can ask for; each maps to --feed-tint-<name> in tokens.css.
TINTS: tuple[str, ...] = ("blue", "green", "amber", "violet")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True)
class FeedItem:
    id: str                                # URL hash and DOM id, e.g. #flag-reveal
    kind: Literal["game", "placeholder"]
    tint: str = "blue"
    game_id: str | None = None             # kind == "game"
    title: str = ""                        # kind == "placeholder"
    copy: str = ""
    badge: str = ""


def game(game_id: str, tint: str = "blue") -> FeedItem:
    return FeedItem(id=game_id, kind="game", game_id=game_id, tint=tint)


def placeholder(item_id: str, title: str, copy: str, tint: str = "violet", badge: str = "Coming soon") -> FeedItem:
    return FeedItem(id=item_id, kind="placeholder", tint=tint, title=title, copy=copy, badge=badge)


FEED: tuple[FeedItem, ...] = (
    game("flag-reveal", tint="blue"),
    game("border-bridge", tint="green"),
    game("capital-quest", tint="amber"),
    game("pinpoint", tint="blue"),
    game("currency-quest", tint="green"),
    placeholder("coming-soon", "More games on the way",
                "Outlines, coastlines and more are in the works. Check back soon for something new to swipe to."),
)


@dataclass(frozen=True)
class FeedCard:
    item: FeedItem
    game: GameSpec | None

    @property
    def title(self) -> str:
        return self.game.title if self.game else self.item.title


class FeedError(ValueError):
    pass


def resolve_feed(items: Sequence[FeedItem], games: Mapping[str, GameSpec]) -> list[FeedCard]:
    """Check the feed config and attach each game's spec. Raises FeedError."""
    if not items:
        raise FeedError("The feed needs at least one item.")
    seen: set[str] = set()
    cards: list[FeedCard] = []
    for item in items:
        if not _SLUG.fullmatch(item.id):
            raise FeedError(f"Feed item id {item.id!r} must be a lower-case slug.")
        if item.id in seen:
            raise FeedError(f"Duplicate feed item id {item.id!r}.")
        seen.add(item.id)
        if item.tint not in TINTS:
            raise FeedError(f"Feed item {item.id!r} has unknown tint {item.tint!r}; use one of {', '.join(TINTS)}.")
        if item.kind == "game":
            spec = games.get(item.game_id or "")
            if spec is None:
                raise FeedError(f"Feed item {item.id!r} references unknown game {item.game_id!r}.")
            if not spec.embeddable:
                raise FeedError(f"Game {item.game_id!r} can't be embedded in the feed (not playable or no embed template).")
            cards.append(FeedCard(item, spec))
        elif item.kind == "placeholder":
            if not item.title:
                raise FeedError(f"Placeholder {item.id!r} needs a title.")
            cards.append(FeedCard(item, None))
        else:
            raise FeedError(f"Feed item {item.id!r} has unknown kind {item.kind!r}.")
    return cards
