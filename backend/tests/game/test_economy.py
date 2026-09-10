"""The payout table, pinned. Pure functions, no event loop, no database."""

from __future__ import annotations

import pytest

from app.game.economy import (
    PARTICIPATION,
    RANK_TIERS,
    coins_awarded,
    next_tier,
    rank_tier,
    wallet_key,
)


class TestCoinsAwarded:
    def test_showing_up_pays(self) -> None:
        # Last place, nothing scored: a currency that pays zero for a whole
        # game makes the second game less likely than the first.
        assert coins_awarded(final_score=0, final_rank=4, player_count=4) == PARTICIPATION

    def test_points_convert_by_flooring(self) -> None:
        # 300 points -> 15 coins, plus participation. Floor, not round: the
        # payout must never exceed what the score justifies.
        assert coins_awarded(final_score=300, final_rank=4, player_count=4) == 25
        assert coins_awarded(final_score=319, final_rank=4, player_count=4) == 25

    def test_podium_pays_on_top_of_performance(self) -> None:
        assert coins_awarded(final_score=600, final_rank=1, player_count=6) == 10 + 30 + 50
        assert coins_awarded(final_score=600, final_rank=2, player_count=6) == 10 + 30 + 25
        assert coins_awarded(final_score=600, final_rank=3, player_count=6) == 10 + 30 + 10
        assert coins_awarded(final_score=600, final_rank=4, player_count=6) == 10 + 30

    def test_last_place_never_takes_a_podium_bonus(self) -> None:
        # Second of two is last. Paying a podium bonus for losing would make
        # the tier ladder measure games played rather than games won.
        assert coins_awarded(final_score=100, final_rank=2, player_count=2) == 15
        # ...but winning a two-player game is still winning.
        assert coins_awarded(final_score=100, final_rank=1, player_count=2) == 65

    def test_negative_scores_cannot_drain_the_wallet(self) -> None:
        # Scoring cannot go negative today. If it ever does, the floor here
        # is the difference between a bad game and a stolen balance.
        assert coins_awarded(final_score=-500, final_rank=3, player_count=3) == PARTICIPATION


class TestRankTier:
    def test_walks_the_ladder(self) -> None:
        assert rank_tier(0) == "ROOKIE"
        assert rank_tier(249) == "ROOKIE"
        assert rank_tier(250) == "EXPLORER"
        assert rank_tier(999) == "EXPLORER"
        assert rank_tier(1_000) == "OTAKU"
        assert rank_tier(15_000) == "LEGEND"
        assert rank_tier(10_000_000) == "LEGEND"

    def test_every_tier_is_reachable(self) -> None:
        # A threshold that no balance can land in is a typo, not a tier.
        for threshold, name in RANK_TIERS:
            assert rank_tier(threshold) == name

    def test_thresholds_ascend(self) -> None:
        thresholds = [t for t, _ in RANK_TIERS]
        assert thresholds == sorted(thresholds)
        assert len(set(thresholds)) == len(thresholds)

    def test_a_balance_below_the_floor_still_gets_a_label(self) -> None:
        assert rank_tier(-1) == "ROOKIE"


class TestNextTier:
    def test_reports_the_gap(self) -> None:
        assert next_tier(0) == ("EXPLORER", 250)
        assert next_tier(200) == ("EXPLORER", 50)
        assert next_tier(250) == ("OTAKU", 750)

    def test_the_top_of_the_ladder_has_nothing_above_it(self) -> None:
        assert next_tier(15_000) is None
        assert next_tier(99_999) is None


class TestWalletKey:
    @pytest.mark.parametrize("name", ["Aoi", "aoi", "AOI", "  aoi  "])
    def test_reaches_one_wallet(self, name: str) -> None:
        assert wallet_key(name) == "aoi"

    def test_collapses_interior_whitespace(self) -> None:
        # Otherwise "ao  i" farms a second wallet that renders identically to
        # "ao i" in any HTML that collapses whitespace — which is all of it.
        assert wallet_key("ao  i") == wallet_key("ao i") == "ao i"
        assert wallet_key("ao\ti") == "ao i"

    def test_casefolds_rather_than_lowercases(self) -> None:
        assert wallet_key("STRAẞE") == wallet_key("straße")

    def test_distinct_names_stay_distinct(self) -> None:
        assert wallet_key("aoi") != wallet_key("aoii")
