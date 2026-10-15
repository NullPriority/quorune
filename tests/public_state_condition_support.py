from __future__ import annotations

from pathlib import Path
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.continuous_conditions import FixedPublicStateConditionSnapshot
from quorune.semantic_runtime import ContinuousEffectSourceContext
from scripts.build_test_database import build_fixture_database


CAPABILITY_ID = "continuous.characteristics.fixed_public_state"

TEMPLATE_ID = "continuous-fixed-public-state-characteristics-v1"

QUERY_HANDLER_ID = "ability.static.query-characteristic-modifier.v1"

FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "fixed-public-state-characteristics-cards.json"
)

def focused_card_database(directory: str) -> CardDatabase:
    path = Path(directory) / "fixed-public-state-characteristics.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE,
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-public-state-interaction-cards.json",
        ],
        path,
    )
    return CardDatabase(path)

def source_context(
    *,
    active_player: str | None = "A",
    hand_count: int = 3,
    graveyard_count: int = 0,
    controller_life: int = 40,
    opponent_life: tuple[int, ...] = (40, 40, 40),
    turn_sequence: int = 4,
    entered_turn_sequence: int = 1,
    counters: tuple[tuple[str, int], ...] = (),
    draw_count: int = 0,
    spell_count: int = 0,
    noncreature_spell_count: int = 0,
    instant_sorcery_count: int = 0,
    opponent_poison: tuple[int, ...] = (),
    controller_is_monarch: bool = False,
    source_query_matches: bool | None = None,
    attached_query_matches: bool | None = None,
    condition_quantity: int | None = None,
) -> ContinuousEffectSourceContext:
    return ContinuousEffectSourceContext(
        source_object_id="public-state-source",
        source_ref="PUBLIC-STATE-SOURCE",
        source_controller="A",
        source_timestamp=7,
        component_id="public-state:0",
        source_logical_object_id="public-state-source@1",
        public_state=FixedPublicStateConditionSnapshot(
            source_controller="A",
            active_player=active_player,
            controller_hand_count=hand_count,
            controller_graveyard_card_count=graveyard_count,
            controller_life=controller_life,
            opponent_life_totals=opponent_life,
            turn_sequence=turn_sequence,
            source_entered_battlefield_turn_sequence=(
                entered_turn_sequence
            ),
            source_counters=counters,
            controller_draw_count=draw_count,
            controller_spell_cast_count=spell_count,
            controller_noncreature_spell_cast_count=(
                noncreature_spell_count
            ),
            controller_instant_sorcery_cast_count=instant_sorcery_count,
            opponent_poison_counter_counts=opponent_poison,
            controller_is_monarch=controller_is_monarch,
            source_query_matches=source_query_matches,
            attached_query_matches=attached_query_matches,
            condition_quantity=condition_quantity,
        ),
    )
