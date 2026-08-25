from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.abilities import CostChoice, parse_activated_abilities
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.activated_ability_catalog import (
    compile_activated_ability_catalog,
)
from quorune.compiler import activated_mana_nodes
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement.immutable import FrozenMap
from quorune.rules.activation.commit import (
    ActivationProposalError,
    _commit_source_cost,
)
from quorune.rules.capabilities import CapabilityRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fixed-activated-zone-change-costs.json"
)
SELECTED_COST_CAPABILITY = "activation.selected_zone_change.fixed"
SOURCE_COST_CAPABILITY = "activation.source_zone_change.fixed"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(
    name: str,
    oracle_text: str,
    *,
    type_line: str = "Artifact",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"fixture:{name.casefold().replace(' ', '-')}",
        name=name,
        mana_cost="{0}",
        mana_value=0.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=("W", "U", "B", "R", "G"),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-activated-zone-change-costs.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-counter-keyword-activations.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class FixedActivatedZoneChangeCostCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )
        cls.capabilities = trusted_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_selected_zone_change_costs_compile_source_spanned(self):
        examples = (
            (
                self.db.lookup("Goblin Turncoat"),
                "sacrifice_one",
                "battlefield",
                "subtypes_all",
                ["goblin"],
            ),
            (
                fixture_card(
                    "Union Sacrifice Fixture",
                    "{2}, Sacrifice an artifact or creature: Draw a card.",
                ),
                "sacrifice_one",
                "battlefield",
                "types_any",
                ["artifact", "creature"],
            ),
            (
                fixture_card(
                    "Graveyard Exile Fixture",
                    "{1}{G}, Exile a creature card from your graveyard: "
                    "Create a 1/1 green Saproling creature token.",
                    type_line="Enchantment",
                ),
                "exile_one_from_graveyard",
                "graveyard",
                "types_all",
                ["creature"],
            ),
            (
                fixture_card(
                    "Battlefield Exile Fixture",
                    "Exile an artifact you control: Draw a card.",
                ),
                "exile_one_from_battlefield",
                "battlefield",
                "types_all",
                ["artifact"],
            ),
            (
                fixture_card(
                    "Hand Discard Fixture",
                    "Discard a red or green card: Draw a card.",
                ),
                "discard_one",
                "hand",
                "colors_any",
                ["G", "R"],
            ),
            (
                fixture_card(
                    "Battlefield Return Fixture",
                    "Return a land you control to its owner's hand: "
                    "Draw a card.",
                ),
                "return_one_to_owner_hand",
                "battlefield",
                "types_all",
                ["land"],
            ),
        )
        for record, operation, zone, predicate_field, predicate_value in examples:
            with self.subTest(record=record.name):
                ir = self.compile(record)
                node = next(
                    item
                    for item in ir.faces[0].nodes
                    if item.kind == "activated_ability"
                )
                self.assertTrue(node.exact, ir.to_dict())
                self.assertEqual(
                    node.text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                self.assertEqual([], node.cost["uncompiled_costs"])
                self.assertEqual(1, len(node.cost["choices"]))
                choice = node.cost["choices"][0]
                self.assertEqual(operation, choice["k"])
                self.assertEqual(zone, choice["z"])
                self.assertEqual(predicate_value, choice["q"][predicate_field])
                self.assertIn(
                    SELECTED_COST_CAPABILITY,
                    node.capability_dependencies,
                )
                catalog = compile_activated_ability_catalog(record)["front"]
                lowered = next(
                    ability
                    for ability in catalog
                    if ability.line_index == node.span.line - 1
                )
                self.assertIsNotNone(lowered.choices[0].predicate)
                self.assertFalse(lowered.uncompiled_costs)

    def test_fixed_source_sacrifice_cost_compiles_source_spanned(self):
        record = self.db.lookup("Font of Fortunes")
        ir = self.compile(record)
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status, ir.to_dict())
        self.assertTrue(node.cost["sacrifice_source"])
        self.assertIn(SOURCE_COST_CAPABILITY, node.capability_dependencies)

    def test_zone_change_cost_grammar_rejects_unbounded_variants(self):
        unsupported = (
            "Sacrifice any number of Goblins",
            "Sacrifice two Goblins",
            "Exile a card from each graveyard",
            "Sacrifice a Goblin and an artifact",
        )
        for index, cost in enumerate(unsupported):
            with self.subTest(cost=cost):
                record = fixture_card(
                    f"Unsupported Zone Cost {index}",
                    f"{cost}: Draw a card.",
                )
                ir = self.compile(record)
                node = ir.faces[0].nodes[0]
                self.assertFalse(node.exact)
                self.assertEqual([cost], node.cost["uncompiled_costs"])
                self.assertTrue(node.residual_ids)

        predicate = FrozenMap(
            {
                "zones": ["battlefield"],
                "owner": None,
                "controller": "$actor",
                "types_all": ["creature"],
                "types_any": [],
                "excluded_types": [],
                "subtypes_all": [],
                "supertypes_all": [],
                "colors_all": [],
                "colors_any": [],
                "keywords_all": [],
                "token": None,
                "tapped": None,
                "include_phased_out": False,
                "known_to_actor": True,
                "exclude_ref": None,
            }
        )
        with self.assertRaisesRegex(ValueError, "origin is inconsistent"):
            CostChoice(
                kind="sacrifice_one",
                zone="graveyard",
                predicate=predicate,
            )
        with self.assertRaisesRegex(ValueError, "one exact query"):
            CostChoice(
                kind="sacrifice_one",
                count=2,
                predicate=predicate,
            )
        legacy = CostChoice(kind="sacrifice", card_type="creature")
        with self.assertRaisesRegex(ValueError, "nonnull objects"):
            CostChoice.from_dict({**legacy.to_dict(), "predicate": None})

    def test_activation_zone_change_costs_exclude_mana_abilities(self):
        ability = parse_activated_abilities(
            card_name="Mana Cost Fixture",
            oracle_text="Sacrifice a Goblin: Add {R}.",
        )[0]

        self.assertTrue(ability.mana_ability)
        self.assertTrue(ability.uncompiled_costs)
        self.assertIs(
            ability,
            activated_mana_nodes.fixed_activated_zone_change_cost(ability),
        )
        source_ir = self.compile(
            fixture_card(
                "Source Mana Cost Fixture",
                "Sacrifice this artifact: Add {C}.",
            )
        )
        source_node = source_ir.faces[0].nodes[0]
        self.assertNotIn(
            SOURCE_COST_CAPABILITY,
            source_node.capability_dependencies,
        )

    def test_source_zone_cost_rejects_inconsistent_descriptor(self):
        ability = parse_activated_abilities(
            card_name="Font of Fortunes",
            oracle_text=(
                "{1}{U}, Sacrifice this enchantment: Draw two cards."
            ),
        )[0]
        source = SimpleNamespace(zone="hand", object_id="source", ref="S")
        with self.assertRaisesRegex(
            ActivationProposalError,
            "requires the source on the battlefield",
        ):
            _commit_source_cost(SimpleNamespace(), source, ability, {})

    def test_selected_zone_cost_dependencies_fail_closed(self):
        record = self.db.lookup("Goblin Turncoat")
        for blocked in (
            SELECTED_COST_CAPABILITY,
            "trigger.event.normalized_zone_change",
            "zone.change.destination_replacement",
        ):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                dependency = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == blocked
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["focused mutation witness"]
                registry = CapabilityRegistry(value)
                registry.mark_evidence_verified("0" * 64)
                ir = compile_oracle_card(
                    record,
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_selected_zone_cost_compiler_mutant_is_killed(self):
        record = self.db.lookup("Goblin Turncoat")

        def assert_exact() -> None:
            ir = self.compile(record)
            self.assertEqual("exact", ir.status)
            choice = ir.faces[0].nodes[0].cost["choices"][0]
            self.assertEqual("sacrifice_one", choice["k"])
            self.assertIn("q", choice)

        assert_exact()
        with mock.patch.object(
            activated_mana_nodes,
            "fixed_activated_zone_change_cost",
            side_effect=lambda ability: ability,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class FixedActivatedZoneChangeCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
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
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        session.engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        session,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
        controller: str | None = None,
        register: bool = True,
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        public = zone not in {"hand", "library"}
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        if register:
            register_generated_programs(
                self.db,
                engine.semantics,
                (record,),
                trust_level="provisional",
                capability_registry=self.capabilities,
                capability_profile=engine.state.config.review_profile,
                promote_exact_runtime_handlers=True,
                promote_exact_effect_programs=True,
            )
        return card

    @staticmethod
    def prepare_priority(session, *, seat: str = "A", mana: int = 8):
        engine = session.engine
        for symbol in ("B", "C", "G", "R", "U", "W"):
            engine.state.players[seat].mana_pool[symbol] = mana
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
    def pass_until(session, predicate, *, limit: int = 48):
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Resolution did not converge")

    @staticmethod
    def choose_replacements(session, *, limit: int = 8):
        for _ in range(limit):
            decision = session.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return
            seat = decision.actors[0]
            packet = StateProjector(
                session.engine.card_db,
                session.state,
            )._decision(f"pilot:{seat}")
            assert packet is not None
            result = session.act(
                f"pilot:{seat}",
                {
                    "a": "choose",
                    "replacement": packet["ctx"]["options"][0]["id"],
                },
            )
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Replacement sequence did not converge")

    @staticmethod
    def action(session, action_id: str, *, seat: str = "A"):
        return next(
            row
            for row in session.packet(f"pilot:{seat}", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if row["id"] == action_id
        )

    def ability(self, session, source: CardInstance):
        return next(
            ability
            for ability in session.engine._activated_abilities(source)
            if source.zone in ability.zones
        )

    def install_competing_graveyard_replacements(self, session) -> None:
        engine = session.engine
        voidwalker = self.add_card(
            session,
            seat="B",
            name="Dauthi Voidwalker",
            ref="B-voidwalker",
            zone="battlefield",
            controller="B",
        )
        engine.create_token(
            "B",
            name="",
            copy_of=voidwalker.ref,
            reason="activation zone-cost ordering witness",
        )

    def assert_private_replacement_choice(self, session) -> None:
        self.assertEqual(
            "replacement.order",
            session.state.pending_decision.kind,
        )
        projector = StateProjector(self.db, session.state)
        self.assertIsNotNone(projector._decision("pilot:A"))
        for seat in session.engine.seats[1:]:
            self.assertIsNone(projector._decision(f"pilot:{seat}"))

    def test_fixed_source_sacrifice_cost_compiles_and_resolves(self):
        session = self.session(7031301)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Font of Fortunes",
            ref="A-font",
            zone="battlefield",
        )
        self.prepare_priority(session)
        ability = self.ability(session, source)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        hand_before = len(engine.state.players["A"].zones["hand"])
        source_logical_object_id = source.logical_object_id
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(1, len(engine.state.stack))
        self.assertEqual(
            source_logical_object_id,
            engine.state.stack[-1].context["source_logical_object_id"],
        )
        self.assertNotEqual(source_logical_object_id, source.logical_object_id)
        self.pass_until(session, lambda: not engine.state.stack)
        self.assertEqual(
            hand_before + 2,
            len(engine.state.players["A"].zones["hand"]),
        )

    def test_fixed_selected_graveyard_exile_cost_resolves(self):
        session = self.session(7031302)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Fungal Plots",
            ref="A-plots",
            zone="battlefield",
        )
        creature = self.add_card(
            session,
            seat="A",
            name="Beastbreaker of Bala Ged",
            ref="A-grave-creature",
            zone="graveyard",
        )
        noncreature = self.add_card(
            session,
            seat="A",
            name="Font of Fortunes",
            ref="A-grave-enchantment",
            zone="graveyard",
            register=False,
        )
        self.prepare_priority(session)
        ability = next(
            item
            for item in engine._activated_abilities(source)
            if item.choices and item.choices[0].zone == "graveyard"
        )
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        action = self.action(session, action_id)
        self.assertEqual(
            [creature.ref],
            action["cost_summary"]["choose_cost"][0]["legal_refs"],
        )

        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", creature.zone)
        self.assertEqual("graveyard", noncreature.zone)
        self.assertTrue(engine.state.stack)
        self.pass_until(session, lambda: not engine.state.stack)
        self.assertTrue(
            any(
                card.printed_name == "Saproling"
                and card.controller == "A"
                and card.zone == "battlefield"
                for card in engine.state.cards.values()
            )
        )

    def test_selected_zone_cost_offer_and_command_share_candidates(self):
        session = self.session(7031303)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Goblin Turncoat",
            ref="A-turncoat",
            zone="battlefield",
        )
        fodder = self.add_card(
            session,
            seat="A",
            name="Goblin Turncoat",
            ref="A-goblin-fodder",
            zone="battlefield",
            register=False,
        )
        phased = self.add_card(
            session,
            seat="A",
            name="Goblin Turncoat",
            ref="A-phased-goblin",
            zone="battlefield",
            register=False,
        )
        phased.phased_out = True
        opposing = self.add_card(
            session,
            seat="B",
            name="Goblin Turncoat",
            ref="B-goblin",
            zone="battlefield",
            controller="B",
            register=False,
        )
        self.prepare_priority(session)
        ability = self.ability(session, source)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        action = self.action(session, action_id)
        self.assertEqual(
            {source.ref, fodder.ref},
            set(action["cost_summary"]["choose_cost"][0]["legal_refs"]),
        )
        source_logical_object_id = source.logical_object_id
        before = authoritative_state_hash(engine.state)

        rejected = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [opposing.ref]},
        )

        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        accepted = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [source.ref]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            "graveyard",
            engine.state.cards[source.object_id].zone,
        )
        self.assertEqual(
            source_logical_object_id,
            engine.state.stack[-1].context["source_logical_object_id"],
        )
        self.assertNotEqual(
            source_logical_object_id,
            engine.state.cards[source.object_id].logical_object_id,
        )
        self.assertTrue(engine.state.stack)

    def test_source_sacrifice_replacement_is_private_and_replays(self):
        session = self.session(7031304, players=4)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Font of Fortunes",
            ref="A-font-replacement",
            zone="battlefield",
        )
        self.install_competing_graveyard_replacements(session)
        self.prepare_priority(session)
        ability = self.ability(session, source)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        mana_before = dict(engine.state.players["A"].mana_pool)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertTrue(result.ok, result.summary)
        self.assert_private_replacement_choice(session)
        self.assertEqual("battlefield", source.zone)
        self.assertFalse(engine.state.stack)
        self.assertEqual(mana_before, engine.state.players["A"].mana_pool)
        self.choose_replacements(session)
        self.assertEqual(
            "exile",
            engine.state.cards[source.object_id].zone,
        )
        self.assertTrue(engine.state.stack)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "source-sacrifice-replacement"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_selected_zone_cost_replacement_is_private_and_replays(self):
        session = self.session(7031305, players=4)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Goblin Turncoat",
            ref="A-turncoat-replacement",
            zone="battlefield",
        )
        fodder = self.add_card(
            session,
            seat="A",
            name="Goblin Turncoat",
            ref="A-goblin-replacement",
            zone="battlefield",
            register=False,
        )
        self.install_competing_graveyard_replacements(session)
        self.prepare_priority(session)
        ability = self.ability(session, source)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [fodder.ref]},
        )

        self.assertTrue(result.ok, result.summary)
        self.assert_private_replacement_choice(session)
        self.assertEqual("battlefield", fodder.zone)
        self.assertFalse(engine.state.stack)
        self.choose_replacements(session)
        self.assertEqual(
            "exile",
            engine.state.cards[fodder.object_id].zone,
        )
        self.assertTrue(engine.state.stack)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "selected-sacrifice-replacement"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
