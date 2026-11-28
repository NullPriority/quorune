from __future__ import annotations

"""Pinned Gamble detection and real private-search and cold-replay witnesses."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from common import ROOT, keep_all
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_order_model import SHUFFLE_OPERATION
from quorune.library_search_model import (
    PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY,
    PRIVATE_SEARCH_RANDOM_DISCARD_MECHANIC,
)
from quorune.model import GameConfig
from quorune.oracle_ir import compile_oracle_card
from quorune.random_discard import RANDOM_DISCARD_OPERATION
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.search_random_discard import private_search_random_discard_runtime_dependencies
from quorune.session import CommanderSession
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database

ORACLE = "a54f0869-94c8-42af-9080-166efb9486a4"
TEMPLATE = "private-library-search-random-discard-v1"
OPERATIONS = ("search", RANDOM_DISCARD_OPERATION, SHUFFLE_OPERATION)
FIXTURE = ROOT / "tests/fixtures/private-search-random-discard-cards.json"
BASE_CARDS = ROOT / "tests/fixtures/bound-effect-program-cards.json"
OBSERVERS = ("pilot:B", "pilot:C", "pilot:D", "spectator")


class PrivateSearchRandomDiscardTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "gamble-fixture.sqlite3"
        build_fixture_database([BASE_CARDS, FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Gamble command witness",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    def scene(self, seed, *, library=True, old_hand=True):
        game = CommanderSession.create(
            self.db,
            {seat: deepcopy(self.deck) for seat in "ABCD"},
            first_player="A",
            seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        keep_all(game)
        game.state.config.semantic_policy = "trusted_only"
        engine = game.engine
        for zone in ("hand", "library"):
            for oid in tuple(game.state.players["A"].zones[zone]):
                engine.move_card(oid, "outside", log=False, semantic_events=False)
        candidates = []
        if library:
            for ref, name in (
                ("gamble-earlier", "Generic Bound Mentor"),
                ("gamble-z-later", "Generic Bound Draw Loss"),
                ("gamble-unselected-neighbor", "Generic Bound Counter Adder"),
            ):
                card = self.add(engine, name, zone="library", ref=ref)
                card.known_to = []
                card.revealed_to = []
                candidates.append(card)
        existing = (
            self.add(engine, "Generic Bound Body", zone="hand", ref="gamble-middle")
            if old_hand else None
        )
        spell = self.add(engine, "Gamble", zone="hand", ref="gamble-normal-cast")
        self.prepare(game, actor="A", active="A", mana={"R": 1})
        self.checkpoint(game)
        return game, spell, candidates, existing

    def assert_compiled_program(self, game, spell):
        self.assert_closed(game, spell)
        program, = [
            row for row in game.engine.semantics.programs_for_oracle(spell.oracle_id)
            if row.key == f"{spell.oracle_id}:spell:front"
        ]
        self.assertEqual(TEMPLATE, program.provenance.get("template_id"))
        self.assertEqual(OPERATIONS, tuple(effect["op"] for effect in program.effects))
        self.assertIn(PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY, program.capability_dependencies)
        self.assertTrue(game.engine.semantic_program_is_current_trusted(program))
        return program

    def cast_to_search(self, game, spell, candidates):
        self.assert_compiled_program(game, spell)
        action = self.action(game, "A", spell)
        self.assertIsNone(action.get("target_schema"))
        cast = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("priority", game.state.pending_decision.kind)
        stack_ref, = [item.ref for item in game.state.stack if item.card_object_id == spell.object_id]
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ("A",)), (pending.kind, tuple(pending.actors)))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        schema = ctx["legal_actions"][0]["choice_schema"]
        self.assertEqual({row.ref for row in candidates}, set(schema["legal_refs"]))
        self.assertEqual(int(bool(candidates)), schema["minimum"])
        self.assertFalse(schema["rules_may_fail_to_find"])
        self.assertEqual("hand", ctx["search_spec"]["destination"])
        self.assertFalse(ctx["search_spec"]["reveal"])
        self.assertFalse(ctx["search_spec"]["shuffle_after"])
        for actor in OBSERVERS:
            packet = game.packet(actor, full=True)
            self.assertIsNone(packet["decision"])
            self.assert_hidden(packet, candidates)
        return stack_ref

    def assert_hidden(self, packet, cards):
        rendered = json.dumps(packet)
        for card in cards:
            self.assertNotIn(card.ref, rendered)
            self.assertNotIn(card.printed_name, rendered)

    def assert_rejected(self, game, principal, response):
        before = authoritative_state_hash(game.state)
        commands = len(game.commands)
        result = game.act(principal, response)
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(commands, len(game.commands))

    @staticmethod
    def shuffles(game):
        return {seat: int(player.stats.get("shuffle_count", 0)) for seat, player in game.state.players.items()}

    def expected_discard(self, game, stack_ref, cards):
        population = sorted(card.object_id for card in cards)
        if not population:
            return None
        return Random(f"{game.state.config.seed}|{stack_ref}|1|random-discard|A").sample(population, 1)[0]

    def resolve_and_check_order(self, game, *, selected, discard, cards, library, start):
        before = self.shuffles(game)
        expected_library = [card.object_id for card in library if card is not selected]
        Random(f"{game.state.config.seed}|A|shuffle|{before['A'] + 1}").shuffle(expected_library)
        shuffle_inputs = []
        original_shuffle = ZoneTransitionOwner.shuffle_library

        def observe_shuffle(owner, player, **kwargs):
            shuffle_inputs.append((
                player,
                {card.object_id: owner.state.cards[card.object_id].zone for card in cards},
            ))
            return original_shuffle(owner, player, **kwargs)

        with patch.object(ZoneTransitionOwner, "shuffle_library", autospec=True, side_effect=observe_shuffle):
            result = game.act("pilot:A", {
                "action_id": "choose", "search_cards": [selected.ref] if selected else [],
            })
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        destinations = {card.object_id: "graveyard" if card.object_id == discard else "hand" for card in cards}
        self.assertEqual([("A", destinations)], shuffle_inputs)
        self.assertEqual(destinations, {oid: game.state.cards[oid].zone for oid in destinations})
        self.assertEqual({**before, "A": before["A"] + 1}, self.shuffles(game))
        self.assertEqual(expected_library, game.state.players["A"].zones["library"])

        events = [event for event in game.state.events if event.event_id > start]
        search, = [event for event in events if event.code == "library.search"]
        shuffle, = [event for event in events if event.code == "library.shuffle"]
        self.assertEqual(("A", "A"), (search.actor, shuffle.actor))
        self.assertLess(search.event_id, shuffle.event_id)
        self.assertEqual(int(selected is not None), search.details["count"])
        self.assertFalse(search.details["revealed"])
        self.assertNotIn("objects", search.details)
        for principal in OBSERVERS:
            observer = game.packet(principal, full=True)
            self.assert_hidden(observer, [card for card in (*cards, *library) if card.object_id != discard])
            if discard is not None:
                self.assertIn(game.state.cards[discard].ref, json.dumps(observer))
        self.replay(game, load=True)

    def check_populated_search(self, seed, *, discard_found):
        game, spell, library, existing = self.scene(seed)
        stack_ref = self.cast_to_search(game, spell, library)
        self.assertLess(library[0].object_id, existing.object_id)
        self.assertLess(existing.object_id, library[1].object_id)
        chosen, = [
            card for card in library[:2]
            if (self.expected_discard(game, stack_ref, (existing, card)) == card.object_id) == discard_found
        ]
        loser = chosen.object_id if discard_found else existing.object_id
        self.assertEqual(loser, self.expected_discard(game, stack_ref, (existing, chosen)))
        game = self.replay(game, load=True)
        self.assertEqual("semantic.search", game.state.pending_decision.kind)
        self.assert_rejected(game, "pilot:B", {"action_id": "choose", "search_cards": [chosen.ref]})
        for invalid in ([], [existing.ref], [chosen.ref, chosen.ref]):
            self.assert_rejected(game, "pilot:A", {"action_id": "choose", "search_cards": invalid})
        start = game.state.event_sequence
        self.resolve_and_check_order(
            game, selected=chosen, discard=loser, cards=(existing, chosen), library=library, start=start,
        )

    def test_original_search_rejects_zero_and_can_discard_the_found_card(self):
        self.check_populated_search(701230901, discard_found=True)

    def test_original_search_keeps_the_found_card_private_when_old_hand_card_is_discarded(self):
        self.check_populated_search(701230909, discard_found=False)

    def test_original_empty_library_still_discards_then_shuffles(self):
        game, spell, library, existing = self.scene(701230903, library=False)
        stack_ref = self.cast_to_search(game, spell, library)
        self.assertEqual(existing.object_id, self.expected_discard(game, stack_ref, (existing,)))
        game = self.replay(game, load=True)
        self.resolve_and_check_order(
            game, selected=None, discard=existing.object_id, cards=(existing,), library=(),
            start=game.state.event_sequence,
        )

    def test_original_empty_library_and_hand_still_shuffle(self):
        game, spell, library, _ = self.scene(701230904, library=False, old_hand=False)
        self.cast_to_search(game, spell, library)
        self.resolve_and_check_order(
            game, selected=None, discard=None, cards=(), library=(), start=game.state.event_sequence,
        )

    def test_compiler_and_runtime_refuse_reordered_or_widened_recipe(self):
        record = self.db.by_oracle_id(ORACLE)
        ir = compile_oracle_card(record, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(TEMPLATE, node.template_id)
        self.assertEqual(OPERATIONS, tuple(effect["op"] for effect in node.effects))
        self.assertIsNone(node.target_schema)
        for alternative in (
            record.oracle_text.replace("discard a card at random, then shuffle", "then shuffle and discard a card at random"),
            record.oracle_text.replace("for a card", "for up to one card"),
            record.oracle_text.replace("discard a card at random", "discard a card"),
            record.oracle_text + " Draw a card.",
        ):
            mutated = compile_oracle_card(replace(record, oracle_text=alternative), capability_registry=self.registry)
            self.assertNotEqual("exact", mutated.status)
        with patch(
            "quorune.compiler.library_search_templates.private_search_random_discard_effect_template",
            return_value=None,
        ) as omitted:
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)
            omitted.assert_called()
        game, spell, _, _ = self.scene(701230905)
        program = self.assert_compiled_program(game, spell)
        search, discard, shuffle = program.effects
        self.assertIn(PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY, private_search_random_discard_runtime_dependencies(program))
        for effects in (
            (search, shuffle, discard), (search, discard),
            ({**search, "reveal": True}, discard, shuffle),
            ({**search, "shuffle_after": True}, discard, shuffle),
            (search, {**discard, "count": True}, shuffle),
        ):
            with self.assertRaises(ValueError):
                private_search_random_discard_runtime_dependencies(replace(program, effects=effects))
        with self.assertRaises(ValueError):
            private_search_random_discard_runtime_dependencies(replace(
                program, coverage=tuple(marker for marker in program.coverage if marker != PRIVATE_SEARCH_RANDOM_DISCARD_MECHANIC),
            ))


    def test_withheld_random_discard_authority_blocks_original_admission(self):
        from quorune.random_discard import RANDOM_DISCARD_CAPABILITY
        from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import DECLARATIONS_PATH, build_index, discover_tests

        value = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(item for item in value["capabilities"] if item["id"] == RANDOM_DISCARD_CAPABILITY)
        row.update(status="blocked", blockers=["Withhold the existing seeded random-discard owner"])
        unavailable = CapabilityRegistry(value)
        index = build_index(
            registry_value=value,
            declaration_source=read_evidence_document(DECLARATIONS_PATH),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        unavailable.mark_evidence_verified(validate_capability_evidence_index(index, registry=unavailable))
        record = self.db.by_oracle_id(ORACLE)
        changed = compile_oracle_card(record, capability_registry=unavailable)
        self.assertNotEqual("exact", changed.status)
        blockers = [blocker for residual in changed.material_residuals for blocker in residual.blockers]
        self.assertTrue(any(f"status:{RANDOM_DISCARD_CAPABILITY}:blocked" in blocker for blocker in blockers), blockers)
        self.assertFalse(any("evidence_index:unverified" in blocker for blocker in blockers), blockers)
        self.assertEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)
