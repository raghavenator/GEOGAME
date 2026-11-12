"""The single list of games. To add a game: create a package under app/games/
that exposes ``GAME: GameSpec`` and append it to ``GAMES`` below."""
from __future__ import annotations

from app.shared.game import GameSpec

from .flag_reveal import GAME as FLAG_REVEAL

GAMES: tuple[GameSpec, ...] = (
    FLAG_REVEAL,
    # Placeholders shown greyed-out on the hub until they're built.
    GameSpec(
        id="capital-quest",
        title="Capital Quest",
        description="Race the clock to match countries with their capital cities.",
        icon="landmark",
        blueprint=None,
        coming_soon=True,
    ),
    GameSpec(
        id="shape-shifter",
        title="Shape Shifter",
        description="Identify countries from their outline alone. Borders are trickier than they look.",
        icon="map",
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
