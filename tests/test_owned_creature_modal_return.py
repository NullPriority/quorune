"""Real modal entry, owner-relative targets and linked return."""

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture

from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.linked_exile_return_shapes import OWNED_CREATURE_END_STEP_RETURN_CAPABILITY
from scripts.build_test_database import build_fixture_database


SOURCE = "Charming Prince"
RETURN_LABEL = "Return linked exiled objects"


class OwnedCreatureModalReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        blink = json.loads((fixtures / "activated-self-transform-cards.json").read_bytes())
        blink["cards"] = [row for row in blink["cards"] if row["name"] == "Ephemerate"]
        assert len(blink["cards"]) == 1
        oracle = blink["cards"][0]["oracle_id"]
        blink["rulings"] = [row for row in blink.get("rulings", []) if row["oracle_id"] == oracle]
        support = folder / "owned-return-blink.json"
        support.write_text(json.dumps(blink), encoding="utf-8")
        database = folder / "owned-modal-return.sqlite3"
        build_fixture_database([support, fixtures / "owned-creature-modal-return-cards.json",
                                fixtures / "next-personal-turn-protection-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Owned-creature modal return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)
        ], ["Isamaru, Hound of Konda"])

    def start(self, game, source, *support):
        game.state.config.semantic_policy = "trusted_only"
        for card in (source, *support):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))

    def choose(self, game, mode, targets=()):
        result = game.act("pilot:A", {"action_id": "choose", "modes": [mode], "targets": list(targets)})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([mode], list(game.state.stack[-1].modes))

    def reject(self, game, seat, mode, targets):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "modes": [mode], "targets": list(targets)})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def returned(self, game):
        return [row for row in game.state.delayed_triggers if row.active and row.label == RETURN_LABEL]

    def owned_elsewhere(self, game):
        owned = self.add(game.engine, "Grizzly Bears", zone="hand")
        game.engine.move_card(owned.object_id, "battlefield", controller="B", log=False)
        return game.state.cards[owned.object_id]

    def test_complete_modal_source_retains_exact_ownership_and_delayed_recipient(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(("permanent.enter.self", "battlefield"), (node.event, node.active_zone))
        modes = node.target_schema["modes"]
        self.assertEqual(["mode_1", "mode_2", "mode_3"], list(modes))
        self.assertFalse(modes["mode_1"]["groups"])
        self.assertFalse(modes["mode_2"]["groups"])
        target, = modes["mode_3"]["groups"]
        self.assertEqual(("you", True, ["creature"]),
                         (target["owner_relation"], target["source_exclusion"], target["types_any"]))
        self.assertIn(OWNED_CREATURE_END_STEP_RETURN_CAPABILITY, node.capability_dependencies)
        for old, new in (("next end step", "next upkeep"), ("another target creature you own", "any number of target creatures you own")):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        game = self.session(70020003)
        source = self.add(game.engine, SOURCE, zone="hand")
        self.assert_closed(game, source)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(dep for dep in program.capability_dependencies if dep != OWNED_CREATURE_END_STEP_RETURN_CAPABILITY)
        closure = self.registry.closure(deps, profile="commander_review")
        changed = replace(program, capability_dependencies=deps, capability_closure=closure.to_dict())
        report = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + OWNED_CREATURE_END_STEP_RETURN_CAPABILITY, report["blockers"])

    def test_untargeted_modes_resolve_independently_when_blink_is_unavailable(self):
        for mode in ("mode_1", "mode_2"):
            with self.subTest(mode=mode):
                game = self.session(70020004)
                source = self.add(game.engine, SOURCE, zone="hand")
                self.start(game, source)
                context = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
                self.assertEqual(["mode_1", "mode_2"], context["legal_modes"])
                self.reject(game, "A", "mode_3", [source.ref])
                self.choose(game, mode)
                pending = self.resolve(game)
                if mode == "mode_1":
                    self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                    schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
                    self.assertEqual("ordered_partition", schema["shape"])
                    top, bottom = schema["legal_refs"]
                    before = authoritative_state_hash(game.state)
                    self.assertTrue(all(ref in json.dumps(game.packet("pilot:A", full=True)) for ref in (top, bottom)))
                    for seat in ("B", "C", "D"):
                        projection = json.dumps(game.packet(f"pilot:{seat}", full=True))
                        self.assertTrue(all(ref not in projection for ref in (top, bottom)))
                    choice = {"action_id": "choose", "cards": {"top": [top], "bottom": [bottom]}}
                    self.assertFalse(game.act("pilot:B", choice).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    game = self.replay(game, load=True)
                    picked = game.act("pilot:A", choice)
                    self.assertTrue(picked.ok, picked.summary)
                    library = game.state.players["A"].zones["library"]
                    self.assertEqual((bottom, top), (game.state.cards[library[0]].ref, game.state.cards[library[-1]].ref))
                    self.assertEqual(40, game.state.players["A"].life)
                else:
                    self.assertIsNone(pending)
                    self.assertEqual(43, game.state.players["A"].life)
                self.assertFalse(self.returned(game))
                self.replay(game, load=True)

    def test_actual_owned_foreign_control_target_returns_under_trigger_controller(self):
        game = self.session(40070071)
        source = self.add(game.engine, SOURCE, zone="hand")
        owned = self.owned_elsewhere(game)
        owned.counters["+1/+1"] = 1
        foreign = self.add(game.engine, "Grizzly Bears", zone="hand", seat="C", ref="foreign-owned")
        game.engine.move_card(foreign.object_id, "battlefield", controller="A", log=False)
        self.start(game, source)
        self.assertEqual(("A", "B"), (owned.owner, owned.controller))
        for target in (source.ref, foreign.ref):
            self.reject(game, "A", "mode_3", [target])
        self.reject(game, "B", "mode_3", [owned.ref])
        game = self.replay(game, load=True)
        self.choose(game, "mode_3", [owned.ref])
        self.resolve_top(game)
        exiled = game.state.cards[owned.object_id]
        self.assertEqual("exile", exiled.zone)
        delayed, = self.returned(game)
        self.assertEqual(("A", {"step": "end_step"}), (delayed.controller, delayed.condition))
        effect, = delayed.stack_template["context"]["dynamic_effects"]
        self.assertEqual("actor", effect["spec"]["controller"])
        identity, = effect["objects"]
        self.assertEqual(exiled.logical_object_id, identity["logical_object_id"])
        game = self.replay(game, load=True)
        self.until(game, lambda s: any(item.label == RETURN_LABEL for item in s.state.stack))
        self.assertEqual("end_step", game.state.step)
        self.resolve_top(game)
        returned = game.state.cards[owned.object_id]
        self.assertEqual(("battlefield", "A", "A", {}), (returned.zone, returned.owner, returned.controller, returned.counters))
        self.assertNotEqual(identity["logical_object_id"], returned.logical_object_id)
        self.replay(game, load=True)

    def test_real_reentry_before_resolution_makes_original_target_illegal(self):
        game = self.session(60820071)
        source = self.add(game.engine, SOURCE, zone="hand")
        owned = self.owned_elsewhere(game)
        response = self.add(game.engine, "Ephemerate", zone="hand", seat="B")
        game.state.players["B"].mana_pool["W"] = 1
        original = owned.logical_object_id
        self.start(game, source, response)
        self.choose(game, "mode_3", [owned.ref])
        self.cast_for(game, "B", response, [owned.ref])
        self.resolve_top(game)
        updated = game.state.cards[owned.object_id].logical_object_id
        self.assertNotEqual(original, updated)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(updated, game.state.cards[owned.object_id].logical_object_id)
        self.assertEqual("battlefield", game.state.cards[owned.object_id].zone)
        self.assertFalse(self.returned(game))
        self.replay(game, load=True)

    def test_real_token_exiles_and_schedules_no_return(self):
        game = self.session(11180071)
        source = self.add(game.engine, SOURCE, zone="hand")
        model = self.add(game.engine, "Grizzly Bears", seat="B")
        token_ref, = game.engine.create_token("A", name="", copy_of=model.ref)
        self.start(game, source)
        self.choose(game, "mode_3", [token_ref])
        self.resolve_top(game)
        self.assertFalse(any(card.ref == token_ref and card.zone == "battlefield" for card in game.state.cards.values()))
        self.assertFalse(self.returned(game))
        self.replay(game, load=True)
