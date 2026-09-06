from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.card_programs import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.entry_state_templates import static_entry_state_handler
from quorune.deck import DeckLoader
from quorune.entry_state_conditions import (
    FIXED_ENTRY_CONDITION_HANDLER_ID,
    FixedEntryCondition,
    FixedEntryConditionError,
    FixedEntryMetric,
)
from quorune.model import CardInstance
from quorune.oracle_ir import generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement_effects import ReplacementChoiceRequired
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.entry_state import (
    FixedEntryConditionReplacementHandler,
)
from scripts.build_test_database import build_fixture_database


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def _record(
    text: str,
    *,
    name: str = "Fixed Entry Condition",
    suffix: int = 1,
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{614120000 + suffix:012d}",
        name=name,
        mana_cost="",
        mana_value=0,
        type_line="Land",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=("C",),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def focused_database(directory: str) -> CardDatabase:
    path = Path(directory) / "fixed-entry-conditions.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "typed-land-entry-cards.json",
        ],
        path,
    )
    return CardDatabase(path)


class FixedEntryConditionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()

    def compile(self, record: CardRecord, *, trust_level: str = "trusted"):
        return compile_card_program(
            _NoRulingsDatabase(),
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level=trust_level,
        )

    def test_fixed_entry_condition_family_compiles_capability_closed(self):
        cases = (
            (
                "This land enters tapped unless you control two or fewer other lands.",
                "zone-entry-state-self-other-land-count-v1",
            ),
            (
                "This land enters tapped unless you control two or more other lands.",
                "zone-entry-state-self-other-land-count-v1",
            ),
            (
                "If you control two or more other lands, this land enters tapped.",
                "zone-entry-state-self-other-land-count-tapped-when-met-v1",
            ),
            (
                "This land enters tapped unless you control two or more basic lands.",
                "zone-entry-state-self-basic-land-count-v1",
            ),
            (
                "This land enters tapped unless a player has 13 or less life.",
                "zone-entry-state-self-player-life-maximum-v1",
            ),
            (
                "This land enters tapped unless your opponents control eight or more lands.",
                "zone-entry-state-self-opponent-land-count-v1",
            ),
            (
                "This land enters tapped unless you control a basic land.",
                "zone-entry-state-self-controlled-basic-land-v1",
            ),
            (
                "Named Fortress enters tapped unless you control a legendary creature.",
                "zone-entry-state-self-controlled-legendary-creature-v1",
            ),
            (
                "This land enters tapped unless you control a legendary green creature.",
                "zone-entry-state-self-controlled-legendary-green-creature-v1",
            ),
            (
                "This land enters tapped unless you control a Mount or Vehicle.",
                "zone-entry-state-self-controlled-mount-or-vehicle-v1",
            ),
            (
                "This land enters tapped unless you control three or more other Mountains.",
                "zone-entry-state-self-controlled-basic-subtype-count-v1",
            ),
        )
        for index, (text, template_id) in enumerate(cases, start=1):
            with self.subTest(text=text):
                record = _record(
                    text,
                    name=("Named Fortress" if text.startswith("Named") else "Fixed Entry Condition"),
                    suffix=index,
                )
                lowered = static_entry_state_handler(
                    text,
                    source_name=record.name,
                )
                self.assertIsNotNone(lowered)
                self.assertEqual(template_id, lowered[0])
                self.assertEqual(
                    FIXED_ENTRY_CONDITION_HANDLER_ID,
                    lowered[1]["handler_id"],
                )
                program = self.compile(record)
                self.assertEqual((), program.residuals)
                ability = next(
                    ability
                    for ability in program.abilities
                    if ability.handlers
                    and ability.handlers[0]["handler_id"]
                    == FIXED_ENTRY_CONDITION_HANDLER_ID
                )
                self.assertEqual(
                    ["zone.entry.tapped_state.fixed_condition"],
                    ability.capability_dependencies,
                )
                self.assertTrue(ability.capability_closure["trusted"])
                self.assertIn(
                    "zone.entry.tapped_state",
                    ability.capability_closure["reachable"],
                )

    def test_fixed_entry_condition_shapes_and_unsupported_grammar_fail_closed(self):
        unsupported = (
            "This land enters tapped unless it's your first, second, or third turn of the game.",
            "This creature enters tapped unless it's your turn.",
            "This land enters tapped unless an opponent controls two creatures.",
            "This land enters tapped unless you control X other lands.",
            "This land enters tapped unless you control two or more artifacts.",
            "This land enters tapped unless the chosen player has 13 or less life.",
        )
        for index, text in enumerate(unsupported, start=1):
            with self.subTest(text=text):
                record = _record(text, suffix=100 + index)
                self.assertIsNone(
                    static_entry_state_handler(
                        text,
                        source_name=record.name,
                    )
                )
                program = self.compile(record, trust_level="provisional")
                self.assertTrue(program.residuals)
                self.assertFalse(program.abilities)

        valid = static_entry_state_handler(
            "This land enters tapped unless you control two or fewer other lands.",
            source_name="Fixed Entry Condition",
        )[1]
        handler = FixedEntryConditionReplacementHandler()
        handler.validate(valid)
        malformed = (
            {**valid, "unknown": True},
            {**valid, "schema_version": True},
            {**valid, "event": "continuous"},
            {**valid, "subject": {"types_all": ["creature"]}},
            {
                **valid,
                "condition": {**valid["condition"], "metric": "unknown"},
            },
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(SemanticNodeError):
                    handler.validate(value)

    def test_fixed_entry_condition_compiler_mutant_is_killed(self):
        record = _record(
            "This land enters tapped unless you control two or more other lands."
        )

        def assert_exact() -> None:
            program = self.compile(record, trust_level="provisional")
            self.assertEqual((), program.residuals)
            self.assertTrue(
                any(
                    descriptor["handler_id"]
                    == FIXED_ENTRY_CONDITION_HANDLER_ID
                    for ability in program.abilities
                    for descriptor in ability.handlers
                )
            )

        assert_exact()
        with patch(
            "quorune.compiler.entry_state_templates._fixed_entry_condition",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

    def test_residual_entry_condition_siblings_remain_fail_closed(self):
        variants = (
            (
                "Mines of Moria",
                "Mines of Moria enters tapped unless you control a legendary creature.\n"
                "{T}: Add {R}.\n"
                "{3}{R}, {T}, Exile three cards from your graveyard: Create "
                "two Treasure tokens.",
                "Exile three cards from your graveyard",
            ),
            (
                "Fire Nation Palace",
                "This land enters tapped unless you control a basic land.\n"
                "{T}: Add {R}.\n"
                "{1}{R}, {T}: Target creature you control gains firebending 4 "
                "until end of turn. (Whenever it attacks, add {R}{R}{R}{R}. "
                "This mana lasts until end of combat.)",
                "Target creature you control",
            ),
        )
        for index, (name, text, residual_fragment) in enumerate(
            variants,
            start=1,
        ):
            with self.subTest(name=name):
                program = self.compile(
                    _record(text, name=name, suffix=200 + index),
                    trust_level="provisional",
                )
                self.assertEqual("unresolved", program.trust_closure["trust_basis"])
                self.assertTrue(
                    all(
                        all(
                            effect.get("op") != "draw"
                            for effect in ability.effects
                        )
                        for ability in program.abilities
                    )
                )
                self.assertTrue(
                    any(
                        residual_fragment in str(residual.get("text") or "")
                        for residual in program.residuals
                    )
                )
                self.assertTrue(
                    any(
                        descriptor.get("handler_id")
                        == FIXED_ENTRY_CONDITION_HANDLER_ID
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )


class FixedEntryConditionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_land(self, engine, name: str, ref: str, *, seat: str = "A"):
        record = self.db.lookup(name)
        for program in generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
        ):
            if any(
                descriptor.get("handler_id")
                == FIXED_ENTRY_CONDITION_HANDLER_ID
                for descriptor in program.handlers
            ):
                engine.semantics.put(program)
        card = CardInstance(
            object_id=f"fixed-entry:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="hand",
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=[seat],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["hand"].append(card.object_id)
        return card

    @staticmethod
    def add_basic_lands(engine, seat: str, count: int):
        candidates = [
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.printed_name in {"Plains", "Island", "Swamp", "Mountain", "Forest"}
            and card.zone not in {"battlefield", "command", "outside"}
        ]
        if len(candidates) < count:
            raise AssertionError("The focused deck lacks enough basic lands")
        for card in candidates[:count]:
            engine.move_card(
                card.object_id,
                "battlefield",
                controller=seat,
                log=False,
            )
            card.tapped = False
        return tuple(candidates[:count])

    @staticmethod
    def enter(engine, card):
        engine.move_card(
            card.object_id,
            "battlefield",
            controller=card.owner,
            log=False,
        )
        return card.tapped

    def test_fixed_condition_handler_uses_closed_metric_and_polarity(self):
        handler = FixedEntryConditionReplacementHandler()
        text = "This land enters tapped unless you control two or fewer other lands."
        descriptor = static_entry_state_handler(
            text,
            source_name="Fixed Entry Condition",
        )[1]
        subject = SimpleNamespace(
            destination="battlefield",
            destination_controller="A",
            object_types=("land",),
            object_ref="L1",
            entry_condition_metrics=FrozenMap(
                {metric.value: 0 for metric in FixedEntryMetric}
            ),
        )
        self.assertIsNone(
            handler.subject_replacement_effect(
                descriptor,
                subject=subject,
                component_id="test",
            )
        )
        metrics = dict(subject.entry_condition_metrics)
        metrics[FixedEntryMetric.CONTROLLER_LANDS.value] = 3
        effect = handler.subject_replacement_effect(
            descriptor,
            subject=SimpleNamespace(
                **{**vars(subject), "entry_condition_metrics": FrozenMap(metrics)}
            ),
            component_id="test",
        )
        self.assertIsNotNone(effect)
        with self.assertRaises(FixedEntryConditionError):
            FixedEntryCondition(
                FixedEntryMetric.CONTROLLER_LANDS,
                None,
                None,
                False,
            )

    def test_land_count_and_basic_count_conditions_use_preentry_snapshot(self):
        fast = self.session(6141201)
        self.add_basic_lands(fast.engine, "A", 2)
        self.assertFalse(
            self.enter(
                fast.engine,
                self.add_land(fast.engine, "Blackcleave Cliffs", "fast-two"),
            )
        )
        slow = self.session(6141202)
        self.add_basic_lands(slow.engine, "A", 3)
        self.assertTrue(
            self.enter(
                slow.engine,
                self.add_land(slow.engine, "Blackcleave Cliffs", "fast-three"),
            )
        )
        vista = self.session(6141203)
        self.add_basic_lands(vista.engine, "A", 2)
        self.assertFalse(
            self.enter(
                vista.engine,
                self.add_land(vista.engine, "Canopy Vista", "two-basics"),
            )
        )

    def test_player_life_opponent_land_and_permanent_queries_compose(self):
        life = self.session(6141204)
        life.engine.state.players["C"].life = -1
        self.assertFalse(
            self.enter(
                life.engine,
                self.add_land(life.engine, "Bleeding Woods", "life-thirteen"),
            )
        )
        opponents = self.session(6141205)
        self.add_basic_lands(opponents.engine, "B", 3)
        self.add_basic_lands(opponents.engine, "C", 3)
        self.add_basic_lands(opponents.engine, "D", 2)
        self.assertFalse(
            self.enter(
                opponents.engine,
                self.add_land(opponents.engine, "Turbulent Fen", "eight-lands"),
            )
        )
        legendary = self.session(6141206)
        legendary.engine.create_token(
            "A",
            name="Entry Condition Legend",
            characteristics={
                "type_line": "Legendary Token Creature — Test",
                "power": "2",
                "toughness": "2",
                "colors": ["G"],
            },
        )
        self.assertFalse(
            self.enter(
                legendary.engine,
                self.add_land(legendary.engine, "Chocobo Camp", "legendary"),
            )
        )

    def test_simultaneous_entries_exclude_each_other(self):
        session = self.session(6141207)
        engine = session.engine
        self.add_basic_lands(engine, "A", 2)
        fast = self.add_land(engine, "Blackcleave Cliffs", "simultaneous-fast")
        cave = self.add_land(engine, "Cave of the Frost Dragon", "simultaneous-cave")
        engine._move_cards_simultaneously(
            (
                (fast.object_id, "battlefield"),
                (cave.object_id, "battlefield"),
            ),
            reason="fixed entry simultaneous snapshot",
            log=False,
        )
        self.assertFalse(fast.tapped)
        self.assertTrue(cave.tapped)

    def test_effective_basic_subtypes_feed_entry_condition_without_cycle(self):
        session = self.session(6141208)
        engine = session.engine
        lands = self.add_basic_lands(engine, "A", 3)
        for land in lands:
            land.annotations["continuous_add_subtypes"] = ["Mountain"]
        mine = self.add_land(engine, "Dwarven Mine", "effective-mountains")
        self.assertFalse(self.enter(engine, mine))

    def test_fixed_condition_land_play_replays_exactly(self):
        session = self.session(6141209)
        engine = session.engine
        self.add_basic_lands(engine, "A", 2)
        land = self.add_land(engine, "Blackcleave Cliffs", "replay-fast-land")
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].land_plays_remaining = 1
        engine._grant_priority("A")
        engine._issue_priority("A")
        action = next(
            action
            for action in engine._priority_action_hints("A")["actions"]
            if action.get("action") == "play_land"
            and action.get("card") == land.ref
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(land.tapped)
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "fixed-entry-condition"
            session.save(record)
            replay = replay_record(record, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_unrelated_zone_changes_do_not_collect_entry_metrics(self):
        session = self.session(6141210)
        engine = session.engine
        ordinary = next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Island"
            and card.zone not in {"battlefield", "command", "outside"}
        )
        with patch(
            "quorune.semantic_runtime.zone_replacements.entry_condition_metrics",
            side_effect=AssertionError("unrelated metric collection"),
        ):
            engine.move_card(
                ordinary.object_id,
                "battlefield",
                controller=ordinary.owner,
                log=False,
            )
        self.assertEqual("battlefield", ordinary.zone)

    def test_fixed_condition_competes_with_ambient_entry_replacement(self):
        session = self.session(6141211)
        engine = session.engine
        self.add_basic_lands(engine, "A", 3)
        record = self.db.lookup("Spelunking")
        for program in generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
        ):
            if program.event == "zone.change":
                engine.semantics.put(program)
        ambient = CardInstance(
            object_id="fixed-entry:ambient-spelunking",
            ref="ambient-spelunking",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=list(engine.seats),
        )
        engine.state.cards[ambient.object_id] = ambient
        engine.state.players["A"].zones["battlefield"].append(
            ambient.object_id
        )
        land = self.add_land(
            engine,
            "Blackcleave Cliffs",
            "competing-entry-replacements",
        )
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ReplacementChoiceRequired) as caught:
            self.enter(engine, land)
        self.assertEqual("hand", land.zone)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        fixed_first = next(
            selection
            for selection in caught.exception.pending.choice.legal_selections
            if land.ref in selection
        )
        engine.move_card(
            land.object_id,
            "battlefield",
            controller="A",
            replacement_selections=(fixed_first,),
            log=False,
        )
        self.assertFalse(land.tapped)


if __name__ == "__main__":
    unittest.main()
