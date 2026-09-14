from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase, CardRecord
from quorune.continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.deck import DeckLoader
from quorune.damage import (
    recipient_snapshot,
    resolve_damage_batch,
    source_snapshot,
)
from quorune.damage_values import DamageProposal
from quorune.life_state import pay_life_cost
from quorune.model import CardInstance
from quorune.compiler.fixed_public_alternative_costs import (
    compile_fixed_public_alternative_cost,
)
from quorune.model import PlayerState, TurnHistoryEvent
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.public_alternative_costs import (
    COMBAT_DAMAGE_HISTORY_MARKER,
    COMMANDER_DAMAGE_HISTORY_MARKER,
    FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID,
    FixedPublicAlternativeCondition,
    FixedPublicAlternativeConditionKind,
    FixedPublicAlternativeCostError,
    FixedPublicAlternativeCostSpec,
    fixed_public_alternative_condition_met,
)
from quorune.rules.capabilities import CapabilityRegistry
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def card_record(
    text: str,
    *,
    name: str = "Alternative Cost Fixture",
    type_line: str = "Instant",
    keywords: tuple[str, ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000701001",
        name=name,
        mana_cost="{4}{U}",
        mana_value=5.0,
        type_line=type_line,
        oracle_text=text,
        power="3" if "Creature" in type_line else None,
        toughness="3" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedPublicAlternativeCostCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = trusted_registry()

    def compile(self, text: str, **kwargs):
        return compile_oracle_card(
            card_record(text, **kwargs),
            capability_registry=self.registry,
            capability_profile="commander_review",
        )

    def test_fixed_public_alternative_costs_compile_closed_plain_and_keyword_grammar(self):
        plain = (
            "You may exile two blue cards from your hand rather than pay "
            "this spell's mana cost.\nDraw a card."
        )
        result = self.compile(plain)
        self.assertEqual("exact", result.status, result.to_dict())
        descriptor = result.faces[0].nodes[0].handlers[0]
        self.assertEqual(
            FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID,
            descriptor["handler_id"],
        )
        cost = FixedPublicAlternativeCostSpec.from_dict(
            descriptor["alternative_cost"]
        )
        self.assertEqual(2, cost.additional_costs[0]["count"])
        self.assertEqual("exile_one_from_hand", cost.additional_costs[0]["operation"])

        mana_and_return = self.compile(
            "You may pay {1} and return a basic land you control to its "
            "owner's hand rather than pay this spell's mana cost.\n"
            "{T}: Add {U}."
        )
        self.assertEqual("exact", mana_and_return.status, mana_and_return.to_dict())
        spec = FixedPublicAlternativeCostSpec.from_dict(
            mana_and_return.faces[0].nodes[0].handlers[0]["alternative_cost"]
        )
        self.assertEqual(1, dict(spec.mana_requirements)["GENERIC"])
        self.assertEqual(
            "return_one_to_owner_hand",
            spec.additional_costs[0]["operation"],
        )
        for printed, canonical in (
            ("Forests", "forest"),
            ("Islands", "island"),
            ("Mountains", "mountain"),
            ("Plains", "plains"),
            ("Swamps", "swamp"),
        ):
            with self.subTest(land_type=printed):
                result = self.compile(
                    f"You may return two {printed} you control to their "
                    "owner's hand rather than pay this spell's mana cost."
                )
                self.assertEqual("exact", result.status, result.to_dict())
                spec = FixedPublicAlternativeCostSpec.from_dict(
                    result.faces[0].nodes[0].handlers[0]["alternative_cost"]
                )
                self.assertEqual(
                    (canonical,),
                    spec.additional_costs[0]["predicate"]["subtypes_all"],
                )

        cases = (
            (
                "Spectacle {1}{B} (You may cast this spell for its spectacle "
                "cost rather than its mana cost if an opponent lost life this turn.)",
                "Creature — Human Rogue",
                ("Spectacle",),
                FixedPublicAlternativeConditionKind.OPPONENT_LOST_LIFE,
            ),
            (
                "Surge {1}{U} (You may cast this spell for its surge cost if "
                "you or a teammate has cast another spell this turn.)",
                "Creature — Human Wizard",
                ("Surge",),
                FixedPublicAlternativeConditionKind.CONTROLLER_CAST_ANOTHER_SPELL,
            ),
            (
                "Prowl {1}{U} (You may cast this for its prowl cost if you "
                "dealt combat damage to a player this turn with a Rogue.)",
                "Creature — Faerie Rogue",
                ("Prowl",),
                FixedPublicAlternativeConditionKind.PROWL,
            ),
            (
                "Freerunning {1}{U} (You may cast this spell for its freerunning "
                "cost if you dealt combat damage to a player this turn with an "
                "Assassin or commander.)",
                "Creature — Human Assassin",
                ("Freerunning",),
                FixedPublicAlternativeConditionKind.FREERUNNING,
            ),
        )
        for text, type_line, keywords, condition in cases:
            with self.subTest(keyword=keywords[0]):
                result = self.compile(
                    text,
                    type_line=type_line,
                    keywords=keywords,
                )
                self.assertEqual("exact", result.status, result.to_dict())
                spec = FixedPublicAlternativeCostSpec.from_dict(
                    result.faces[0].nodes[0].handlers[0]["alternative_cost"]
                )
                self.assertEqual(condition, spec.condition.kind)

    def test_fixed_public_alternative_costs_reject_open_costs_conditions_and_composition(self):
        unsupported = (
            "You may tap two untapped creatures you control rather than pay this spell's mana cost.",
            "You may pay {X} rather than pay this spell's mana cost.",
            "You may pay {W/U} rather than pay this spell's mana cost.",
            "If an opponent searched their library this turn, you may pay {0} rather than pay this spell's mana cost.",
            "You may pay 1 life and exile a blue card from your hand rather than pay this spell's mana cost.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    compile_fixed_public_alternative_cost(
                        material_line=text,
                        oracle_line=text,
                        line_index=0,
                    )
                )

        composed = self.compile(
            "You may pay {1}{U} rather than pay this spell's mana cost.\n"
            "Kicker {1}\nDraw a card.",
            keywords=("Kicker",),
        )
        self.assertNotEqual("exact", composed.status)
        self.assertTrue(composed.material_residuals)

        independent_activation = self.compile(
            "Spectacle {B}{R} (You may cast this spell for its spectacle cost "
            "rather than its mana cost if an opponent lost life this turn.)\n"
            "{B}: This creature gains deathtouch until end of turn.",
            type_line="Creature — Human Rogue",
            keywords=("Spectacle",),
        )
        self.assertEqual(
            "exact", independent_activation.status, independent_activation.to_dict()
        )

        valid = compile_fixed_public_alternative_cost(
            material_line=(
                "You may pay {1}{U} rather than pay this spell's mana cost."
            ),
            oracle_line="fixture",
            line_index=0,
        )
        self.assertIsNotNone(valid)
        descriptor = valid.to_dict()
        for mutation in (
            {**descriptor, "unknown": True},
            {**descriptor, "mana_requirements": {**descriptor["mana_requirements"], "U": 2}},
            {**descriptor, "condition": {**descriptor["condition"], "kind": "prowl"}},
            {**descriptor, "additional_costs": [{}, {}]},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(FixedPublicAlternativeCostError):
                    FixedPublicAlternativeCostSpec.from_dict(mutation)

    def test_fixed_public_alternative_cost_dependency_and_compiler_mutations_fail_closed(self):
        text = "Spectacle {1}{B}"
        result = self.compile(
            text,
            type_line="Creature — Human Rogue",
            keywords=("Spectacle",),
        )
        self.assertEqual("exact", result.status)
        with patch(
            "quorune.compiler.keyword_nodes."
            "fixed_public_alternative_cost_keyword_node",
            return_value=None,
        ):
            mutated = self.compile(
                text,
                type_line="Creature — Human Rogue",
                keywords=("Spectacle",),
            )
        self.assertNotEqual("exact", mutated.status)

        registry = trusted_registry()
        registry._capabilities[
            "casting.alternative_cost.fixed_public"
        ]["status"] = "blocked"
        blocked = compile_oracle_card(
            card_record(
                text,
                type_line="Creature — Human Rogue",
                keywords=("Spectacle",),
            ),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked.status)


class FixedPublicAlternativeCostConditionTests(unittest.TestCase):
    @staticmethod
    def host(events=(), *, active_player="A", card_type="Creature — Faerie Rogue"):
        card = SimpleNamespace(
            object_id="spell",
            ref="spell@1",
            controller="A",
            phased_out=False,
        )
        land = SimpleNamespace(
            object_id="land",
            ref="land@1",
            controller="A",
            phased_out=False,
        )
        state = SimpleNamespace(
            active_player=active_player,
            players={
                "A": PlayerState("A", "A"),
                "B": PlayerState("B", "B"),
                "C": PlayerState("C", "C"),
                "D": PlayerState("D", "D"),
            },
            cards={"spell": card, "land": land},
        )
        state.players["A"].zones["battlefield"] = ["land"]
        by_kind = {}
        for event in events:
            by_kind.setdefault(event.kind, []).append(event)
        return SimpleNamespace(
            state=state,
            active_seats=("A", "B", "C", "D"),
            _current_turn_history=lambda kind: tuple(by_kind.get(kind, ())),
            _effective_card_data=lambda current: {
                "type_line": (
                    "Land — Island" if current is land else card_type
                )
            },
            _type_parts=lambda value: (
                ({"land"}, {"island"}, set())
                if value.startswith("Land")
                else ({"creature"}, {"faerie", "rogue"}, set())
            ),
        ), card

    def assert_condition(self, kind, events=(), *, expected=True):
        host, card = self.host(events)
        condition = FixedPublicAlternativeCondition(kind=kind)
        self.assertEqual(
            expected,
            fixed_public_alternative_condition_met(
                host, "A", card, condition.to_dict()
            ),
        )

    def test_spectacle_surge_prowl_and_freerunning_use_authoritative_history(self):
        self.assert_condition(
            FixedPublicAlternativeConditionKind.OPPONENT_LOST_LIFE,
            (TurnHistoryEvent("player_lost_life", target="B", amount=1),),
        )
        self.assert_condition(
            FixedPublicAlternativeConditionKind.CONTROLLER_CAST_ANOTHER_SPELL,
            (TurnHistoryEvent("spell_cast", actor="A"),),
        )
        self.assert_condition(
            FixedPublicAlternativeConditionKind.PROWL,
            (
                TurnHistoryEvent(
                    "player_damaged",
                    actor="A",
                    target="B",
                    amount=1,
                    types=(COMBAT_DAMAGE_HISTORY_MARKER, "rogue", "creature"),
                ),
            ),
        )
        self.assert_condition(
            FixedPublicAlternativeConditionKind.FREERUNNING,
            (
                TurnHistoryEvent(
                    "player_damaged",
                    actor="A",
                    target="B",
                    amount=1,
                    types=(
                        COMBAT_DAMAGE_HISTORY_MARKER,
                        COMMANDER_DAMAGE_HISTORY_MARKER,
                        "creature",
                    ),
                ),
            ),
        )
        self.assert_condition(
            FixedPublicAlternativeConditionKind.PROWL,
            (TurnHistoryEvent("player_damaged", actor="A", target="B", amount=1, types=("rogue", "creature")),),
            expected=False,
        )


class FixedPublicAlternativeCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "public-alternative-cost.sqlite3"
        build_fixture_database(
            (
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT / "tests" / "fixtures" / "fixed-public-alternative-costs.json",
            ),
            database,
        )
        cls.db = CardDatabase(database)
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
        cls.capabilities = trusted_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "A",
        zone: str = "hand",
        is_commander: bool = False,
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name, fuzzy=False)
        card = CardInstance(
            object_id=f"public-alternative:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            is_commander=is_commander,
            commander_designation_id=(
                f"commander:{seat}:{ref}" if is_commander else None
            ),
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=[seat] if zone == "hand" else list(engine.seats),
            revealed_to=[] if zone == "hand" else list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def prepare_main(session, seat: str = "A") -> None:
        engine = session.engine
        engine.state.active_player = seat
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
        engine.pump()

    @staticmethod
    def cast_action(engine, card: CardInstance, seat: str = "A"):
        return next(
            action
            for action in engine._priority_action_hints(seat)["actions"]
            if action.get("action") == "cast" and action.get("card") == card.ref
        )

    @staticmethod
    def options(action):
        return {option["id"]: option for option in action["cost_options"]}

    @staticmethod
    def remove_all_abilities(engine, card: CardInstance) -> None:
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id=f"test:remove:{card.ref}",
                source_id="test:public-alternative-removal",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=card.object_id,
                        logical_object_id=card.logical_object_id,
                    ),
                ),
            ),
        )

    def test_fixed_alternative_costs_offer_commit_tax_and_total_costs(self):
        session = self.session(701_001)
        engine = session.engine
        commander = self.add_card(
            session,
            name="Alternative Avatar",
            ref="ALT-COMMANDER",
            zone="command",
            is_commander=True,
        )
        engine.state.players["A"].commander_casts[commander.oracle_id] = 1
        engine.state.players["A"].mana_pool.update(
            {"C": 2, "W": 1, "U": 1, "B": 1, "R": 1, "G": 1}
        )
        self.prepare_main(session)
        action = self.cast_action(engine, commander)
        option = self.options(action)["plain-alternative-ab1"]
        self.assertEqual(2, option["requirements"]["GENERIC"])
        self.assertEqual(
            {"W": 1, "U": 1, "B": 1, "R": 1, "G": 1},
            {key: option["requirements"][key] for key in "WUBRG"},
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "cost_option": "plain-alternative-ab1",
                "pay": "manual",
                "payment": {
                    "C": 2,
                    "W": 1,
                    "U": 1,
                    "B": 1,
                    "R": 1,
                    "G": 1,
                },
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", commander.zone)
        self.assertEqual(
            2,
            engine.state.players["A"].commander_casts[commander.oracle_id],
        )

    def test_nonmana_alternative_costs_are_atomic_and_revalidated(self):
        session = self.session(701_002)
        engine = session.engine
        spell = self.add_card(
            session, name="Alternative Exile Insight", ref="ALT-EXILE"
        )
        first = self.add_card(
            session, name="Alternative Exile Insight", ref="BLUE-ONE"
        )
        second = self.add_card(
            session, name="Alternative Exile Insight", ref="BLUE-TWO"
        )
        self.prepare_main(session)
        action = self.cast_action(engine, spell)
        option = self.options(action)["plain-alternative-ab1"]
        legal = option["choice_schema"]["exile_cards"]["legal_refs"]
        self.assertNotIn(spell.ref, legal)
        self.assertLessEqual({first.ref, second.ref}, set(legal))
        result = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "cost_option": "plain-alternative-ab1",
                "exile_cards": [first.ref, second.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", spell.zone)
        self.assertEqual("exile", first.zone)
        self.assertEqual("exile", second.zone)

        stale_session = self.session(701_003)
        stale_engine = stale_session.engine
        stale_spell = self.add_card(
            stale_session, name="Alternative Exile Insight", ref="STALE-SPELL"
        )
        stale_first = self.add_card(
            stale_session, name="Alternative Exile Insight", ref="STALE-ONE"
        )
        stale_second = self.add_card(
            stale_session, name="Alternative Exile Insight", ref="STALE-TWO"
        )
        self.prepare_main(stale_session)
        stale = deepcopy(self.cast_action(stale_engine, stale_spell))
        stale_engine.move_card(stale_second.object_id, "graveyard", log=False)
        before = authoritative_state_hash(stale_engine.state)
        rejected = stale_session.act(
            "pilot:A",
            {
                "action_id": stale["id"],
                "cost_option": "plain-alternative-ab1",
                "exile_cards": [stale_first.ref, stale_second.ref],
                "pay": "auto",
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale_engine.state))
        self.assertEqual("hand", stale_spell.zone)

    def test_history_conditions_follow_life_damage_and_cast_commits(self):
        session = self.session(701_006)
        engine = session.engine
        spectacle = self.add_card(
            session, name="Spectacle Rogue", ref="SPECTACLE"
        )
        surge = self.add_card(session, name="Surge Wizard", ref="SURGE")
        prowl = self.add_card(session, name="Prowl Rogue", ref="PROWL")
        freerunning = self.add_card(
            session, name="Freerunning Assassin", ref="FREERUNNING"
        )
        for color in "UB":
            engine.state.players["A"].mana_pool[color] = 4
        self.prepare_main(session)
        self.assertEqual(
            {"normal"}, set(self.options(self.cast_action(engine, spectacle)))
        )
        pay_life_cost(engine, "B", 1)
        self.assertIn(
            "spectacle-alternative-ab1",
            self.options(self.cast_action(engine, spectacle)),
        )

        self.assertEqual({"normal"}, set(self.options(self.cast_action(engine, surge))))
        engine._record_turn_history("spell_cast", actor="A", types=("instant",))
        self.assertIn(
            "surge-alternative-ab1",
            self.options(self.cast_action(engine, surge)),
        )

        rogue = self.add_card(
            session,
            name="Prowl Rogue",
            ref="ROGUE-SOURCE",
            zone="battlefield",
        )
        resolve_damage_batch(
            engine,
            (
                DamageProposal(
                    proposal_id="test:prowl-history",
                    source=source_snapshot(
                        engine, rogue.ref, controller="A"
                    ),
                    recipient=recipient_snapshot(engine, "B", actor="A"),
                    amount=1,
                    combat=True,
                    reason="fixed alternative-cost history test",
                ),
            ),
        )
        self.assertIn(
            "prowl-alternative-ab1",
            self.options(self.cast_action(engine, prowl)),
        )
        self.assertEqual(
            {"normal"},
            set(self.options(self.cast_action(engine, freerunning))),
        )
        assassin = self.add_card(
            session,
            name="Freerunning Assassin",
            ref="ASSASSIN-SOURCE",
            zone="battlefield",
        )
        resolve_damage_batch(
            engine,
            (
                DamageProposal(
                    proposal_id="test:freerunning-history",
                    source=source_snapshot(
                        engine, assassin.ref, controller="A"
                    ),
                    recipient=recipient_snapshot(engine, "B", actor="A"),
                    amount=1,
                    combat=True,
                    reason="fixed alternative-cost history test",
                ),
            ),
        )
        self.assertIn(
            "freerunning-alternative-ab1",
            self.options(self.cast_action(engine, freerunning)),
        )

    def test_partial_removed_and_stale_alternative_costs_fail_closed(self):
        session = self.session(701_004)
        engine = session.engine
        partial = self.add_card(
            session,
            name="Partial Alternative Creature",
            ref="PARTIAL-ALT",
        )
        engine.state.players["A"].mana_pool["C"] = 20
        self.prepare_main(session)
        self.assertFalse(
            any(
                action.get("action") == "cast"
                and action.get("card") == partial.ref
                for action in engine._priority_action_hints("A")["actions"]
            )
        )
        card = self.add_card(
            session,
            name="Alternative Avatar",
            ref="REMOVED-ALT",
            zone="command",
            is_commander=True,
        )
        engine.state.players["A"].mana_pool["C"] = 9
        self.prepare_main(session)
        stale = deepcopy(self.cast_action(engine, card))
        self.remove_all_abilities(engine, card)
        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": stale["id"],
                "cost_option": "plain-alternative-ab1",
                "pay": "auto",
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.prepare_main(session)
        self.assertNotIn(
            "plain-alternative-ab1",
            self.options(self.cast_action(engine, card)),
        )

    def test_four_player_alternative_cost_projection_save_load_and_replay(self):
        session = self.session(701_005, players=4)
        engine = session.engine
        spell = self.add_card(
            session, name="Alternative Exile Insight", ref="REPLAY-SPELL"
        )
        first = self.add_card(
            session, name="Alternative Exile Insight", ref="REPLAY-ONE"
        )
        second = self.add_card(
            session, name="Alternative Exile Insight", ref="REPLAY-TWO"
        )
        self.prepare_main(session)
        action = self.cast_action(engine, spell)
        acting = json.dumps(session.packet("pilot:A", full=True), sort_keys=True)
        opponent = json.dumps(session.packet("pilot:B", full=True), sort_keys=True)
        self.assertIn(first.ref, acting)
        self.assertIn(second.ref, acting)
        self.assertNotIn(first.ref, opponent)
        self.assertNotIn(second.ref, opponent)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "cost_option": "plain-alternative-ab1",
                "exile_cards": [first.ref, second.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = json.dumps(
                session.packet(principal, full=True), sort_keys=True
            )
            self.assertNotIn(spell.object_id, packet)
            self.assertNotIn(first.object_id, packet)
            self.assertNotIn(second.object_id, packet)
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public-alternative-replay"
            session.save(path)
            replay = replay_record(path, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
