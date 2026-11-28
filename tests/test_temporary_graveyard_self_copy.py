from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_self_copy import TEMPORARY_GRAVEYARD_COPY_CAPABILITY, temporary_graveyard_copy_effect
from scripts.build_test_database import build_fixture_database


class TemporaryGraveyardSelfCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "temporary-graveyard-copy.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "activation-public-state-cards", "fixed-public-zone-move-cards",
            "modal-event-composition-cards", "target-land-controller-choice-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Graveyard copy", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def copy_offer(self, game, woodland):
        self.priority(game, "A")
        view = game.packet("pilot:A", full=True)
        return next((row for row in view["decision"]["ctx"]["legal"]["actions"]
                     if row["id"] == f"activate:{woodland.ref}:ab3"), None)

    def pay_copy(self, game, woodland, target, *, reject=()):
        offer = self.copy_offer(game, woodland)
        self.assertIsNotNone(offer)
        command = {"action_id": offer["id"], "targets": [target.ref], "pay": "manual", "payment": {"C": 2, "G": 2}}
        before = authoritative_state_hash(game.state)
        for candidate in reject:
            failure = game.act("pilot:A", {**command, "targets": [candidate.ref]})
            self.assertFalse(failure.ok, failure.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("activated_ability", game.state.stack[-1].kind)

    def make_game(self, seed):
        game = self.session(seed)
        woodland = self.add(game.engine, "Shifting Woodland")
        target = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        forest = self.add(game.engine, "Forest", zone="graveyard")
        growth = self.add(game.engine, "Giant Growth", zone="graveyard")
        self.prepare(game, mana={"C": 2, "G": 2, "U": 1})
        self.assert_closed(game, woodland)
        programs = game.engine.semantics.programs_for_oracle(woodland.oracle_id)
        program, = (row for row in programs if row.ability_id == "ability:ab3")
        self.assertEqual([temporary_graveyard_copy_effect()], program.effects)
        self.assertIn(TEMPORARY_GRAVEYARD_COPY_CAPABILITY, program.capability_dependencies)
        return game, woodland, target, forest, growth

    def test_actual_delirium_only_at_declaration_and_copy_expires_with_saved_replay(self):
        game, woodland, target, forest, growth = self.make_game(81132201)
        self.add(game.engine, "Forest", zone="graveyard", ref="duplicate-land-type")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="foreign-permanent-model")
        private = self.add(game.engine, "Grizzly Bears", seat="C", zone="hand", ref="private-permanent-model")
        response = self.add(game.engine, "Public Grave Exile", seat="B", zone="hand")
        game.state.players["B"].mana_pool["B"] = 1
        self.prepare(game, mana={"C": 2, "G": 2, "U": 1})
        self.assertIsNone(self.copy_offer(game, woodland))
        self.add(game.engine, "Idol of Oblivion", zone="graveyard")
        self.prepare(game, mana={"C": 2, "G": 2, "U": 1})
        offer = self.copy_offer(game, woodland)
        self.assertIsNotNone(offer)
        self.assertNotIn(private.ref, str(offer))
        woodland.counters["+1/+1"] = 1
        self.checkpoint(game)
        self.pay_copy(game, woodland, target, reject=(foreign, private, growth))
        self.cast(game, "B", response, [growth.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[growth.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        live = game.state.cards[woodland.object_id]
        self.assertEqual("Grizzly Bears", game.engine.display_name(live.object_id))
        self.assertEqual("A", live.controller)
        self.assertEqual(3, game.engine._numeric_stat(live.object_id, "power"))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[forest.object_id].zone)
        game = self.replay(game, load=True)
        until = game.state.turn_sequence
        self.until(game, lambda s: s.state.turn_sequence > until)
        self.assertEqual("Shifting Woodland", game.engine.display_name(woodland.object_id))
        self.assertNotIn("copy_overrides", game.state.cards[woodland.object_id].annotations)
        self.assertEqual(1, game.state.cards[woodland.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_moved_target_is_illegal_for_the_pending_copy_and_replays(self):
        game, woodland, target, _, _ = self.make_game(81132202)
        self.add(game.engine, "Idol of Oblivion", zone="graveyard")
        response = self.add(game.engine, "Public Grave Exile", seat="B", zone="hand")
        game.state.players["B"].mana_pool["B"] = 1
        self.prepare(game, mana={"C": 2, "G": 2})
        self.checkpoint(game)
        self.pay_copy(game, woodland, target)
        self.cast(game, "B", response, [target.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.assertEqual("Shifting Woodland", game.engine.display_name(woodland.object_id))
        self.assertNotIn("copy_overrides", game.state.cards[woodland.object_id].annotations)
        self.replay(game, load=True)

    def test_actual_departed_and_returned_source_cannot_receive_old_copy_and_replays(self):
        game, woodland, target, _, _ = self.make_game(81132203)
        self.add(game.engine, "Idol of Oblivion", zone="graveyard")
        response = self.add(game.engine, "Generic Land Removal", seat="B", zone="hand")
        tap = self.add(game.engine, "Twiddle", zone="hand")
        returner = self.add(game.engine, "Scaretiller")
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.prepare(game, mana={"C": 2, "G": 2, "U": 1})
        self.checkpoint(game)
        original = woodland.logical_object_id
        self.pay_copy(game, woodland, target)
        self.cast(game, "B", response, [woodland.ref])
        self.resolve_top(game)
        departed = game.state.cards[woodland.object_id]
        self.assertEqual("graveyard", departed.zone)
        self.cast(game, "A", tap, [returner.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "tap"})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "semantic.target")
        result = game.act("pilot:A", {"action_id": "choose", "modes": ["mode_2"], "targets": [departed.ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        returned = game.state.cards[woodland.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(original, returned.logical_object_id)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("Shifting Woodland", game.engine.display_name(returned.object_id))
        self.assertNotIn("copy_overrides", game.state.cards[returned.object_id].annotations)
        self.replay(game, load=True)

    def test_scope_price_and_declaration_condition_fail_closed(self):
        record = self.db.lookup("Shifting Woodland", fuzzy=False)
        current = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", current.status, current.material_residuals)
        node, = (node for face in current.faces for node in face.nodes if list(node.effects) == [temporary_graveyard_copy_effect()])
        self.assertIn(TEMPORARY_GRAVEYARD_COPY_CAPABILITY, node.capability_dependencies)
        game, woodland, _, _, _ = self.make_game(81132204)
        program, = (row for row in game.engine.semantics.programs_for_oracle(woodland.oracle_id)
                    if row.ability_id == "ability:ab3")
        handlers = deepcopy(program.handlers)
        handlers[0]["ability"]["activation_conditions"] = []
        changed = replace(program, handlers=handlers)
        binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"], binding)
        for before, after in (("your graveyard", "a graveyard"), ("four or more", "three or more"),
                              ("{2}{G}{G}", "{1}{G}{G}"), ("until end of turn", "this turn")):
            with self.subTest(changed=before):
                changed = replace(record, oracle_text=record.oracle_text.replace(before, after))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
                self.assertNotEqual("exact", result.status, result.material_residuals)

    def test_mutant_omitting_the_exact_graveyard_copy_makes_original_partial(self):
        with patch("quorune.compiler.temporary_self_copy_templates.temporary_graveyard_self_copy_template", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_scope_price_and_declaration_condition_fail_closed()
