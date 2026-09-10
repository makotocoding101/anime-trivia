"""Coins and rank tiers (§07's sibling: what a game is worth after it ends).

Scoring decides who won a round; this decides what the whole game paid out
and what standing that buys. Kept here, in the pure domain, for the same
reason scoring is: the payout table is the kind of thing that gets tuned
often and argued about, so it should be adjustable with a unit test rather
than by playing a game and squinting at the result.

Integers throughout, like scoring — a currency stored as a float is a bug
with a delay on it.

Identity note: v1 has no accounts (out of scope), so a wallet is
keyed by normalised display name and nothing stops two people picking the
same one. That is a deliberate, stated limitation of the feature rather than
an oversight — see `wallet_key`.
"""

from __future__ import annotations

#: Paid for finishing a game at all. A player who came last and scored zero
#: still leaves with something, because a currency that punishes showing up
#: makes the second game less likely than the first.
PARTICIPATION = 10

#: Points-to-coins. A strong game is ~600 points, so this pays ~30.
POINTS_PER_COIN = 20

#: Podium bonus, index 0 = first place.
PLACEMENT_BONUS = (50, 25, 10)

#: Coin thresholds, ascending. The last one whose threshold is met wins.
RANK_TIERS: tuple[tuple[int, str], ...] = (
    (0, "ROOKIE"),
    (250, "EXPLORER"),
    (1_000, "OTAKU"),
    (2_500, "VETERAN"),
    (6_000, "SENSEI"),
    (15_000, "LEGEND"),
)


def coins_awarded(*, final_score: int, final_rank: int, player_count: int) -> int:
    """What one player earned from one finished game.

    The placement bonus is withheld from last place however few players there
    were: in a two-player game, "second" is last, and paying a podium bonus
    for losing would make the tier ladder measure games played rather than
    games won. First place in a two-player game is still a win and still pays.
    """
    performance = max(0, final_score) // POINTS_PER_COIN
    placement = 0
    if 1 <= final_rank <= len(PLACEMENT_BONUS) and final_rank < player_count:
        placement = PLACEMENT_BONUS[final_rank - 1]
    return PARTICIPATION + performance + placement


def rank_tier(coins: int) -> str:
    """The label beside a player's name. Derived, never stored — changing the
    ladder is then an edit here, not a migration over every wallet."""
    label = RANK_TIERS[0][1]
    for threshold, name in RANK_TIERS:
        if coins >= threshold:
            label = name
        else:
            break
    return label


def next_tier(coins: int) -> tuple[str, int] | None:
    """The tier above, and the coins still needed to reach it. None at the
    top of the ladder — a progress bar with nowhere to go is worse than no
    progress bar."""
    for threshold, name in RANK_TIERS:
        if coins < threshold:
            return name, threshold - coins
    return None


def wallet_key(name: str) -> str:
    """The identity a wallet hangs on.

    Casefold rather than lower(): it is the Unicode-correct fold, so "STRAẞE"
    and "straße" reach the same wallet. Interior whitespace is collapsed so
    "ao  i" cannot farm a second wallet that renders identically to "ao i".

    This is emphatically not authentication. Anyone who types an existing
    name plays as that wallet. Making it otherwise means accounts, which v1
    does not have.
    """
    return " ".join(name.split()).casefold()
