"""The single list of games. To add a game: create a package under app/games/
that exposes ``GAME: GameSpec`` and append it to ``GAMES`` below."""
from __future__ import annotations

from app.shared.game import GameSpec

from .border_bridge import GAME as BORDER_BRIDGE
from .capital_quest import GAME as CAPITAL_QUEST
from .flag_reveal import GAME as FLAG_REVEAL
from .pinpoint import GAME as PINPOINT

GAMES: tuple[GameSpec, ...] = (
    FLAG_REVEAL,
    BORDER_BRIDGE,
    CAPITAL_QUEST,
    PINPOINT,
    # Placeholders shown greyed-out on the hub until they're built.
    GameSpec(
        id="shape-shifter",
        title="Shape Shifter",
        description="Name the country from its outline alone, no map around it.",
        icon="shapes",
        blueprint=None,
        coming_soon=True,
    ),
)


def playable_games() -> list[GameSpec]:
    return [g for g in GAMES if g.playable]


def get_game(game_id: str) -> GameSpec | None:
    return next((g for g in GAMES if g.id == game_id), None)


def _check_unique_ids() -> None:
    ids = [g.id for g in GAMES]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise RuntimeError(f"Duplicate game ids in registry: {dupes}")


_check_unique_ids()
