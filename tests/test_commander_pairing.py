from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.ability_fragments import (
    PartnerWithSpec,
    ability_fragment_from_dict,
)
from quorune.carddb import CardDatabase
from quorune.commander import initial_commander_state
from quorune.commander_pairing import (
    COMMANDER_PAIRING_TEMPLATE_ID,
    PARTNER_WITH_SEARCH_CAPABILITY_ID,
    PARTNER_WITH_SEARCH_MECHANIC_ID,
    PARTNER_WITH_SEARCH_TEMPLATE_ID,
    CommanderPairingError,
    CommanderPairingKind,
    commander_pairing_declaration,
    validate_commander_pair,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import CommanderEngine
from quorune.model import GameConfig, GameState
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.library_search_capability_shapes import (
    partner_with_search_node_capabilities,
)
from quorune.rules.spell_cast_events import SpellCastEvent
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


PAIRING_FIXTURE = ROOT / "tests" / "fixtures" / "commander-pairing-cards.json"


def pairing_deck(first: str, second: str) -> DeckDefinition:
    return DeckDefinition(
        name=f"{first} and {second}",
        entries=[
            DeckEntry(first, board="commander"),
            DeckEntry(second, board="commander"),
        ],
        commanders=[first, second],
    )


class CommanderPairingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "commander-pairing.sqlite3"
        build_fixture_database(PAIRING_FIXTURE, path)
        cls.db = CardDatabase(path)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def registry_for(self, *names: str) -> SemanticRegistry:
        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            [self.db.lookup(name) for name in names],
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_capability_declarations=True,
        )
        return registry

    @staticmethod
    def _single_commander_deck(main_name: str) -> DeckDefinition:
        return DeckDefinition(
            name=f"Partner with search {main_name}",
            entries=[
                DeckEntry("Thrasios, Triton Hero", board="commander"),
                DeckEntry(main_name, quantity=8),
            ],
            commanders=["Thrasios, Triton Hero"],
        )

    def partner_search_session(self, seed: int) -> CommanderSession:
        session = CommanderSession.create(
            self.db,
            {
                "A": self._single_commander_deck("Named Partner Alpha"),
                "B": self._single_commander_deck("Named Partner Beta"),
                "C": self._single_commander_deck("Named Partner Alpha"),
                "D": self._single_commander_deck("Named Partner Beta"),
            },
            first_player="A",
            seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        while (
            session.state.pending_decision is not None
            and session.state.pending_decision.kind == "mulligan.declare"
        ):
            for principal in tuple(session.pending_principals()):
                result = session.act(principal, {"a": "keep"})
                self.assertTrue(result.ok, result.summary)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        session.engine.state.priority_passes = []
        return session

    def partner_draw_session(self, seed: int) -> CommanderSession:
        deck = DeckDefinition(
            name="Partner with draw interaction",
            entries=[
                DeckEntry("Named Partner Alpha", board="commander"),
                DeckEntry("Named Partner Beta", board="commander"),
                DeckEntry("Thrasios, Triton Hero", quantity=10),
            ],
            commanders=["Named Partner Alpha", "Named Partner Beta"],
        )
        session = CommanderSession.create(
            self.db,
            {seat: deck for seat in "ABCD"},
            first_player="A",
            seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        while (
            session.state.pending_decision is not None
            and session.state.pending_decision.kind == "mulligan.declare"
        ):
            for principal in tuple(session.pending_principals()):
                result = session.act(principal, {"a": "keep"})
                self.assertTrue(result.ok, result.summary)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        source = self._owned_card(session, "A", "Named Partner Alpha")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        engine.state.players["A"].mana_pool["C"] = 3
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        return session

    @staticmethod
    def _owned_card(
        session: CommanderSession,
        seat: str,
        name: str,
        *,
        zone: str | None = None,
    ):
        return next(
            card
            for card in session.state.cards.values()
            if card.owner == seat
            and card.printed_name == name
            and (zone is None or card.zone == zone)
        )

    def _begin_partner_search(
        self,
        session: CommanderSession,
        *,
        target_seat: str = "B",
    ):
        engine = session.engine
        source = self._owned_card(session, "A", "Named Partner Alpha")
        partner = self._owned_card(
            session,
            target_seat,
            "Named Partner Beta",
            zone="library",
        )
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(("A", "B", "C", "D"), batch.apnap_order)
        self.assertEqual(
            [PARTNER_WITH_SEARCH_TEMPLATE_ID],
            [
                engine.semantics.get(item["semantic_key"]).provenance[
                    "template_id"
                ]
                for item in batch.items
            ],
        )
        engine._stabilize()
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target_seat]},
        )
        self.assertTrue(selected.ok, selected.summary)
        for seat in "ABCD":
            passed = session.act(
                f"pilot:{seat}",
                {"action_id": "pass"},
            )
            self.assertTrue(passed.ok, passed.summary)
        self.assertEqual("semantic.search", engine.state.pending_decision.kind)
        return source, partner

    def test_pairing_keywords_compile_as_exact_typed_setup_declarations(self):
        expected = {
            "Thrasios, Triton Hero": CommanderPairingKind.PARTNER,
            "Wilson, Refined Grizzly": (
                CommanderPairingKind.CHOOSE_A_BACKGROUND
            ),
            "Rose Tyler": CommanderPairingKind.DOCTORS_COMPANION,
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == COMMANDER_PAIRING_TEMPLATE_ID
                ]
                self.assertEqual(1, len(nodes))
                self.assertTrue(nodes[0].exact)
                self.assertEqual("game.setup", nodes[0].event)
                self.assertEqual((kind.value,), nodes[0].mechanics)

        for name in ("Bjorna, Nightfall Alchemist",):
            with self.subTest(excluded=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )

    def test_partner_with_compiles_exact_pairing_and_entry_search(self):
        cases = (
            ("Named Partner Alpha", "Named Partner Beta"),
            ("Named Partner Beta", "Named Partner Alpha"),
            ("Toothy, Imaginary Friend", "Pir, Imaginative Rascal"),
        )
        for name, partner_name in cases:
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                partner_nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id
                    in {
                        COMMANDER_PAIRING_TEMPLATE_ID,
                        PARTNER_WITH_SEARCH_TEMPLATE_ID,
                    }
                ]
                self.assertEqual(2, len(partner_nodes), ir.material_residuals)
                pairing = next(
                    node
                    for node in partner_nodes
                    if node.template_id == COMMANDER_PAIRING_TEMPLATE_ID
                )
                search = next(
                    node
                    for node in partner_nodes
                    if node.template_id == PARTNER_WITH_SEARCH_TEMPLATE_ID
                )
                self.assertTrue(pairing.exact)
                self.assertEqual("game.setup", pairing.event)
                self.assertEqual(("partner with",), pairing.mechanics)
                self.assertEqual(1, len(pairing.handlers))
                fragment = ability_fragment_from_dict(
                    pairing.handlers[0]["fragment"]
                )
                self.assertEqual(PartnerWithSpec(partner_name), fragment)
                self.assertTrue(search.exact)
                self.assertEqual("permanent.enter.self", search.event)
                self.assertEqual("battlefield", search.active_zone)
                self.assertIn(
                    PARTNER_WITH_SEARCH_MECHANIC_ID,
                    search.mechanics,
                )
                self.assertIn(
                    PARTNER_WITH_SEARCH_CAPABILITY_ID,
                    search.capability_dependencies,
                )
                self.assertEqual(
                    {
                        "zones": ["player"],
                        "categories": ["player"],
                        "player_relation": "any",
                        "count": 1,
                    },
                    search.target_schema,
                )
                self.assertEqual(
                    ({
                        "op": "search",
                        "searching_player": "$target.0",
                        "zone": "library",
                        "selector": {"names": [partner_name]},
                        "count": {"minimum": 0, "maximum": 1},
                        "destination": "hand",
                        "optional": True,
                        "shuffle_after": True,
                    },),
                    search.effects,
                )
                self.assertEqual(pairing.span, search.span)
                self.assertGreater(search.span.end, search.span.start)

    def test_partner_with_shape_dependencies_and_variants_fail_closed(self):
        record = self.db.lookup("Named Partner Alpha")
        target_schema = {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": "any",
            "count": 1,
        }
        effect = {
            "op": "search",
            "searching_player": "$target.0",
            "zone": "library",
            "selector": {"names": ["Named Partner Beta"]},
            "count": {"minimum": 0, "maximum": 1},
            "destination": "hand",
            "optional": True,
            "shuffle_after": True,
        }
        self.assertEqual(
            (
                PARTNER_WITH_SEARCH_CAPABILITY_ID,
                "target.revalidate_resolution",
            ),
            partner_with_search_node_capabilities(
                effects=(effect,),
                target_schema=target_schema,
                mechanic_ids=(PARTNER_WITH_SEARCH_MECHANIC_ID,),
            ),
        )
        self.assertIn(
            PARTNER_WITH_SEARCH_CAPABILITY_ID,
            capability_dependencies_for_node(
                effects=(effect,),
                target_schema=target_schema,
                mechanic_ids=(PARTNER_WITH_SEARCH_MECHANIC_ID,),
            ),
        )
        mutations = (
            {**effect, "searching_player": "$controller"},
            {**effect, "zone": "graveyard"},
            {**effect, "selector": {"names": ["Alpha", "Beta"]}},
            {**effect, "count": {"minimum": 1, "maximum": 1}},
            {**effect, "destination": "battlefield"},
            {**effect, "optional": False},
            {**effect, "shuffle_after": False},
            {**effect, "unknown": True},
        )
        for mutated_effect in mutations:
            with self.subTest(mutated_effect=mutated_effect):
                self.assertEqual(
                    (),
                    partner_with_search_node_capabilities(
                        effects=(mutated_effect,),
                        target_schema=target_schema,
                        mechanic_ids=(PARTNER_WITH_SEARCH_MECHANIC_ID,),
                    ),
                )
                self.assertNotIn(
                    PARTNER_WITH_SEARCH_CAPABILITY_ID,
                    capability_dependencies_for_node(
                        effects=(mutated_effect,),
                        target_schema=target_schema,
                        mechanic_ids=(PARTNER_WITH_SEARCH_MECHANIC_ID,),
                    ),
                )
        registry_value = json.loads(
            DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf-8")
        )
        dependencies = (
            "library.search.partner_with_named_to_hand",
            "target.revalidate_resolution",
            "trigger.event.normalized_zone_change",
            "trigger.placement.apnap",
            "variant.commander.zone_return",
            "zone.change.destination_replacement",
        )
        for dependency in dependencies:
            with self.subTest(blocked=dependency):
                value = json.loads(json.dumps(registry_value))
                row = next(
                    item
                    for item in value["capabilities"]
                    if item["id"] == dependency
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused Partner with dependency mutation"]
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(value),
                    capability_profile="commander_review",
                )
                search = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id == PARTNER_WITH_SEARCH_TEMPLATE_ID
                )
                self.assertFalse(search.exact)
                self.assertTrue(search.residual_ids)

        variants = (
            "Partner—Friends forever",
            "Partner with",
            "Partner with Named Partner Beta; draw a card.",
            'Partner with "Named Partner Beta"',
            "Partner with itself",
            "Partner with Knight",
        )
        for oracle_text in variants:
            with self.subTest(oracle_text=oracle_text):
                mutated = replace(record, oracle_text=oracle_text)
                ir = compile_oracle_card(
                    mutated,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertFalse(
                    any(
                        node.template_id
                        in {
                            COMMANDER_PAIRING_TEMPLATE_ID,
                            PARTNER_WITH_SEARCH_TEMPLATE_ID,
                        }
                        for node in ir.faces[0].nodes
                    )
                )
                self.assertFalse(
                    any(
                        node.template_id == COMMANDER_PAIRING_TEMPLATE_ID
                        for face in ir.faces
                        for node in face.nodes
                    )
                )

    def test_partner_pair_is_designated_in_four_player_public_state_and_round_trips(self):
        cases = (
            ("Thrasios, Triton Hero", "Tymna the Weaver"),
            ("Wilson, Refined Grizzly", "Flaming Fist"),
            ("Rose Tyler", "The Tenth Doctor"),
        )
        for index, names in enumerate(cases, start=1):
            with self.subTest(pair=names):
                registry = self.registry_for(*names)
                deck = pairing_deck(*names)
                state = initial_commander_state(
                    self.db,
                    {seat: deck for seat in "ABCD"},
                    first_player="A",
                    config=GameConfig(seed=702_124_000 + index),
                    semantics=registry,
                )

                for seat in state.turn_order:
                    command_cards = [
                        state.cards[object_id]
                        for object_id in state.players[seat].zones["command"]
                    ]
                    self.assertEqual(2, len(command_cards))
                    self.assertEqual(
                        2,
                        len(
                            {
                                card.commander_designation_id
                                for card in command_cards
                            }
                        ),
                    )
                    self.assertTrue(
                        all(card.is_commander for card in command_cards)
                    )
                self.assertEqual(
                    state.to_dict(),
                    GameState.from_dict(state.to_dict()).to_dict(),
                )

    def test_choose_a_background_and_doctors_companion_accept_exact_type_pairs(self):
        cases = (
            (
                "Wilson, Refined Grizzly",
                "Flaming Fist",
                CommanderPairingKind.CHOOSE_A_BACKGROUND,
            ),
            (
                "Rose Tyler",
                "The Tenth Doctor",
                CommanderPairingKind.DOCTORS_COMPANION,
            ),
        )
        for first_name, second_name, expected_kind in cases:
            with self.subTest(kind=expected_kind.value):
                registry = self.registry_for(first_name, second_name)
                first = self.db.lookup(first_name)
                second = self.db.lookup(second_name)
                forward = validate_commander_pair(
                    self.db,
                    registry,
                    (first, second),
                )
                reverse = validate_commander_pair(
                    self.db,
                    registry,
                    (second, first),
                )
                self.assertIn(
                    expected_kind,
                    {value.kind for value in forward if value is not None},
                )
                self.assertEqual(tuple(reversed(forward)), reverse)

    def test_partner_with_pair_is_designated_and_requires_reciprocal_names(self):
        names = ("Named Partner Alpha", "Named Partner Beta")
        registry = self.registry_for(*names)
        records = tuple(self.db.lookup(name) for name in names)
        forward = validate_commander_pair(self.db, registry, records)
        reverse = validate_commander_pair(
            self.db,
            registry,
            tuple(reversed(records)),
        )
        self.assertEqual(
            ["Named Partner Beta", "Named Partner Alpha"],
            [declaration.partner_name for declaration in forward],
        )
        self.assertTrue(
            all(
                declaration.kind is CommanderPairingKind.PARTNER_WITH
                for declaration in forward
            )
        )
        self.assertEqual(tuple(reversed(forward)), reverse)

        deck = pairing_deck(*names)
        state = initial_commander_state(
            self.db,
            {seat: deck for seat in "ABCD"},
            first_player="A",
            config=GameConfig(seed=702_124_101),
            semantics=registry,
        )
        self.assertTrue(
            all(
                len(state.players[seat].zones["command"]) == 2
                for seat in state.turn_order
            )
        )
        self.assertEqual(
            state.to_dict(),
            GameState.from_dict(state.to_dict()).to_dict(),
        )

        mismatches = (
            (records[0], self.db.lookup("Toothy, Imaginary Friend")),
            (records[0], self.db.lookup("Thrasios, Triton Hero")),
        )
        mismatch_registry = self.registry_for(
            *names,
            "Toothy, Imaginary Friend",
            "Thrasios, Triton Hero",
        )
        before = mismatch_registry.card_program_fingerprints()
        for first, second in mismatches:
            with self.subTest(first=first.name, second=second.name):
                with self.assertRaises(CommanderPairingError):
                    validate_commander_pair(
                        self.db,
                        mismatch_registry,
                        (first, second),
                    )
        self.assertEqual(
            before,
            mismatch_registry.card_program_fingerprints(),
        )

    def test_partner_with_target_search_is_private_revalidates_and_replays(self):
        session = self.partner_search_session(702_124_102)
        source, partner = self._begin_partner_search(session)
        packet = session.packet("pilot:B", full=True)
        candidates = packet["decision"]["ctx"]["search_cards"]
        self.assertEqual([partner.ref], [row["id"] for row in candidates])
        for seat in "ACD":
            projected = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertNotIn(partner.ref, projected)

        before_shuffle = session.state.players["B"].stats.get(
            "shuffle_count", 0
        )
        chosen = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [partner.ref],
            },
        )
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("hand", partner.zone)
        self.assertEqual(
            before_shuffle + 1,
            session.state.players["B"].stats["shuffle_count"],
        )
        self.assertEqual("battlefield", source.zone)

        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "partner-with-private-search"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        stale = self.partner_search_session(702_124_103)
        _stale_source, stale_partner = self._begin_partner_search(stale)
        stale.engine.move_card(stale_partner.object_id, "graveyard", log=False)
        before = authoritative_state_hash(stale.state)
        rejected = stale.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [stale_partner.ref],
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale.state))
        self.assertEqual("graveyard", stale_partner.zone)

    def test_partner_with_entry_triggers_follow_current_ability_and_apnap(self):
        absent = self.partner_search_session(702_124_104)
        engine = absent.engine
        source = self._owned_card(absent, "A", "Named Partner Alpha")
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-partner-with",
                source_id="fixture:remove-partner-with-owner",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=source.object_id,
                        logical_object_id=(
                            f"{source.object_id}@"
                            f"{source.zone_change_counter + 1}"
                        ),
                    ),
                ),
            ),
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.pending_trigger_batches)
        self.assertFalse(engine.state.stack)
        self.assertNotEqual(
            "semantic.target",
            getattr(engine.state.pending_decision, "kind", None),
        )

        persistent = self.partner_search_session(702_124_105)
        old_source, partner = self._begin_partner_search(persistent)
        old_logical_id = old_source.logical_object_id
        persistent.engine.move_card(old_source.object_id, "graveyard", log=False)
        persistent.engine.move_card(
            old_source.object_id,
            "battlefield",
            controller="C",
            log=False,
        )
        self.assertNotEqual(old_logical_id, old_source.logical_object_id)
        selected = persistent.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [partner.ref],
            },
        )
        self.assertTrue(selected.ok, selected.summary)
        self.assertEqual("hand", partner.zone)
        self.assertEqual("C", old_source.controller)

    def test_partner_with_pairing_composes_with_target_draw_and_replay(self):
        session = self.partner_draw_session(702_124_106)
        engine = session.engine
        source = self._owned_card(session, "A", "Named Partner Alpha")
        partner = self._owned_card(session, "A", "Named Partner Beta")
        self.assertTrue(source.is_commander)
        self.assertTrue(partner.is_commander)
        self.assertIsNotNone(source.commander_designation_id)
        self.assertIsNotNone(partner.commander_designation_id)
        self.assertNotEqual(
            source.commander_designation_id,
            partner.commander_designation_id,
        )
        packet = session.packet("pilot:A", full=True)
        actions = [
            action
            for action in packet["decision"]["ctx"]["legal"]["actions"]
            if action.get("action") == "activate"
            and action.get("source") == source.ref
        ]
        self.assertEqual(1, len(actions))
        self.assertEqual(
            {"A", "B", "C", "D"},
            set(actions[0]["target_schema"]["legal_refs"]),
        )
        drawn_ids = set(engine.state.players["B"].zones["library"][-2:])
        hand_before = len(engine.state.players["B"].zones["hand"])
        designation_id = source.commander_designation_id
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        activated = session.act(
            "pilot:A",
            {"action_id": actions[0]["id"], "targets": ["B"]},
        )
        self.assertTrue(activated.ok, activated.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            principals = session.pending_principals()
            self.assertTrue(principals)
            passed = session.act(
                principals[0],
                {"action_id": "pass"},
            )
            self.assertTrue(passed.ok, passed.summary)
        else:
            self.fail("Partner with draw interaction did not resolve")

        self.assertEqual(
            hand_before + 2,
            len(engine.state.players["B"].zones["hand"]),
        )
        self.assertTrue(
            drawn_ids <= set(engine.state.players["B"].zones["hand"])
        )
        self.assertTrue(source.is_commander)
        self.assertEqual(designation_id, source.commander_designation_id)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual("command", partner.zone)
        self.assertNotIn(
            "hand",
            session.packet("pilot:A", full=True)["state"]["players"]["B"],
        )

        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "partner-with-target-draw"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_engine_create_shares_pairing_registry_with_setup(self):
        names = ("Thrasios, Triton Hero", "Tymna the Weaver")
        registry = self.registry_for(*names)
        deck = pairing_deck(*names)

        engine = CommanderEngine.create(
            self.db,
            {"A": deck, "B": deck},
            first_player="A",
            config=GameConfig(seed=702_124_001),
            semantics=registry,
        )

        self.assertIs(registry, engine.semantics)
        self.assertTrue(
            all(
                len(engine.state.players[seat].zones["command"]) == 2
                for seat in engine.state.turn_order
            )
        )

    def test_pairing_and_activated_mill_preserve_commander_identity(self):
        names = ("Zellix, Sanity Flayer", "Flaming Fist")
        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            [self.db.lookup(name) for name in names],
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        deck = pairing_deck(*names)
        deck.entries.extend(
            DeckEntry(name)
            for name in (
                "Thrasios, Triton Hero",
                "Tymna the Weaver",
                "Wilson, Refined Grizzly",
                "Rose Tyler",
            )
        )
        state = initial_commander_state(
            self.db,
            {seat: deck for seat in "ABCD"},
            first_player="A",
            config=GameConfig(seed=702_124_006),
            semantics=registry,
        )
        engine = CommanderEngine(self.db, state, registry)
        source = next(
            card
            for card in state.cards.values()
            if card.owner == "A" and card.printed_name == names[0]
        )
        designation_id = source.commander_designation_id
        source = engine.move_card(source.object_id, "battlefield", log=False)
        top_cards = tuple(
            state.cards[object_id]
            for object_id in reversed(
                state.players["B"].zones["library"][-3:]
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        engine.state.players["A"].mana_pool["C"] = 1
        ability = engine._activated_abilities(source)[0]

        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": ability.ability_id,
                "targets": ["B"],
            },
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(
            ["graveyard", "graveyard", "graveyard"],
            [card.zone for card in top_cards],
        )
        self.assertTrue(source.tapped)
        self.assertTrue(source.is_commander)
        self.assertEqual(designation_id, source.commander_designation_id)
        self.assertIn(source.oracle_id, state.commander_oracle_ids["A"])
        self.assertEqual(2, len(state.players["B"].zones["command"]))
        self.assertEqual(
            state.to_dict(),
            GameState.from_dict(state.to_dict()).to_dict(),
        )

    def test_partner_commander_second_opponent_cast_draws_without_losing_identity(
        self,
    ):
        names = ("Kraum, Ludevic's Opus", "Thrasios, Triton Hero")
        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            [self.db.lookup(name) for name in names],
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        deck = pairing_deck(*names)
        deck.entries.extend(
            DeckEntry(name)
            for name in (
                "Tymna the Weaver",
                "Wilson, Refined Grizzly",
                "Rose Tyler",
                "The Tenth Doctor",
            )
        )
        state = initial_commander_state(
            self.db,
            {seat: deck for seat in "ABCD"},
            first_player="B",
            config=GameConfig(seed=702_124_007),
            semantics=registry,
        )
        engine = CommanderEngine(self.db, state, registry)
        kraum = next(
            card
            for card in state.cards.values()
            if card.owner == "A" and card.printed_name == names[0]
        )
        designation_id = kraum.commander_designation_id
        kraum = engine.move_card(kraum.object_id, "battlefield", log=False)
        hand_before = len(state.players["A"].zones["hand"])
        library_top = state.players["A"].zones["library"][-1]

        engine.state.active_player = "B"
        engine._dispatch_semantic_event(
            "spell.cast",
            SpellCastEvent(
                schema_version=3,
                card_ref="fixture:B:second-spell",
                object_id="fixture:B:second-spell",
                logical_object_id="fixture:B:second-spell@1",
                controller="B",
                owner="B",
                active_player="B",
                origin="hand",
                stack_ref="fixture:stack:B:second-spell",
                types=("instant",),
                mana_value=1,
                caster_spell_number=2,
                kicked=False,
                has_x_cost=False,
                has_adventure=False,
                keywords=(),
            ).to_context(),
        )
        engine._stabilize()

        self.assertEqual(1, len(state.stack))
        self.assertEqual("A", state.stack[-1].controller)
        self.assertEqual(kraum.object_id, state.stack[-1].source_object_id)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(hand_before + 1, len(state.players["A"].zones["hand"]))
        self.assertIn(library_top, state.players["A"].zones["hand"])
        self.assertEqual("battlefield", kraum.zone)
        self.assertTrue(kraum.is_commander)
        self.assertEqual(designation_id, kraum.commander_designation_id)
        self.assertIn(kraum.oracle_id, state.commander_oracle_ids["A"])
        self.assertEqual(
            state.to_dict(),
            GameState.from_dict(state.to_dict()).to_dict(),
        )

    def test_unsupported_or_mismatched_pairings_fail_closed_without_mutation(self):
        all_names = (
            "Thrasios, Triton Hero",
            "Tymna the Weaver",
            "Wilson, Refined Grizzly",
            "Flaming Fist",
            "Rose Tyler",
            "The Tenth Doctor",
            "Toothy, Imaginary Friend",
            "Bjorna, Nightfall Alchemist",
        )
        registry = self.registry_for(*all_names)
        thrasios = self.db.lookup("Thrasios, Triton Hero")
        tymna = self.db.lookup("Tymna the Weaver")
        wilson = self.db.lookup("Wilson, Refined Grizzly")
        rose = self.db.lookup("Rose Tyler")
        doctor = self.db.lookup("The Tenth Doctor")
        cases = (
            (thrasios, wilson),
            (self.db.lookup("Toothy, Imaginary Friend"), thrasios),
            (self.db.lookup("Bjorna, Nightfall Alchemist"), thrasios),
            (
                replace(
                    thrasios,
                    type_line="Legendary Enchantment",
                ),
                tymna,
            ),
            (
                wilson,
                replace(
                    self.db.lookup("Flaming Fist"),
                    type_line="Enchantment — Background",
                ),
            ),
            (
                rose,
                replace(
                    doctor,
                    type_line=(
                        "Legendary Creature — Time Lord Doctor Shapeshifter"
                    ),
                ),
            ),
            (thrasios, thrasios),
        )
        before = registry.card_program_fingerprints()
        for first, second in cases:
            with self.subTest(first=first.name, second=second.name):
                with self.assertRaises(CommanderPairingError):
                    validate_commander_pair(
                        self.db,
                        registry,
                        (first, second),
                    )
        self.assertEqual(before, registry.card_program_fingerprints())

        arbitrary = pairing_deck(
            "Thrasios, Triton Hero",
            "Wilson, Refined Grizzly",
        )
        with self.assertRaises(CommanderPairingError):
            initial_commander_state(
                self.db,
                {"A": arbitrary, "B": arbitrary},
                first_player="A",
                config=GameConfig(seed=702_124_002),
                semantics=registry,
            )

        too_many = DeckDefinition(
            name="Three commanders",
            entries=[
                DeckEntry(name, board="commander")
                for name in (
                    "Thrasios, Triton Hero",
                    "Tymna the Weaver",
                    "Wilson, Refined Grizzly",
                )
            ],
            commanders=[
                "Thrasios, Triton Hero",
                "Tymna the Weaver",
                "Wilson, Refined Grizzly",
            ],
        )
        with self.assertRaisesRegex(ValueError, "at most two"):
            initial_commander_state(
                self.db,
                {"A": too_many, "B": too_many},
                first_player="A",
                config=GameConfig(seed=702_124_003),
                semantics=registry,
            )

        inconsistent_board = pairing_deck(
            "Thrasios, Triton Hero",
            "Tymna the Weaver",
        )
        inconsistent_board.entries.append(
            DeckEntry("Wilson, Refined Grizzly", board="commander")
        )
        with self.assertRaisesRegex(ValueError, "must match"):
            initial_commander_state(
                self.db,
                {"A": inconsistent_board, "B": inconsistent_board},
                first_player="A",
                config=GameConfig(seed=702_124_004),
                semantics=registry,
            )

        missing_designation = pairing_deck(
            "Thrasios, Triton Hero",
            "Tymna the Weaver",
        )
        missing_designation.entries.pop()
        with self.assertRaisesRegex(ValueError, "must exist"):
            initial_commander_state(
                self.db,
                {"A": missing_designation, "B": missing_designation},
                first_player="A",
                config=GameConfig(seed=702_124_005),
                semantics=registry,
            )

    def test_pairing_program_and_compiler_mutations_fail_closed(self):
        names = ("Thrasios, Triton Hero", "Tymna the Weaver")
        registry = self.registry_for(*names)
        records = tuple(self.db.lookup(name) for name in names)
        declaration = commander_pairing_declaration(
            self.db,
            registry,
            records[0],
        )
        self.assertIsNotNone(declaration)
        registry.remove(declaration.program_key)
        with self.assertRaises(CommanderPairingError):
            validate_commander_pair(self.db, registry, records)

        with patch(
            "quorune.compiler.keyword_nodes.commander_pairing_keyword_node",
            return_value=None,
        ):
            ir = compile_oracle_card(
                records[0],
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id == COMMANDER_PAIRING_TEMPLATE_ID
                and node.exact
                for face in ir.faces
                for node in face.nodes
            )
        )

    def test_partner_with_pairing_and_compiler_mutations_fail_closed(self):
        names = ("Named Partner Alpha", "Named Partner Beta")
        registry = self.registry_for(*names)
        records = tuple(self.db.lookup(name) for name in names)
        declaration = commander_pairing_declaration(
            self.db,
            registry,
            records[0],
        )
        self.assertIsNotNone(declaration)
        registry.remove(declaration.program_key)
        with self.assertRaises(CommanderPairingError):
            validate_commander_pair(self.db, registry, records)

        with patch(
            "quorune.compiler.multi_keyword_nodes.partner_with_keyword_nodes",
            return_value=None,
        ):
            ir = compile_oracle_card(
                records[0],
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id
                in {
                    COMMANDER_PAIRING_TEMPLATE_ID,
                    PARTNER_WITH_SEARCH_TEMPLATE_ID,
                }
                and node.exact
                for face in ir.faces
                for node in face.nodes
            )
        )


if __name__ == "__main__":
    unittest.main()
