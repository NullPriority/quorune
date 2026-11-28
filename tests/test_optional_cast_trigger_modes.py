"""Native coverage for the zero-or-one modal cast trigger and both returns."""

from copy import deepcopy
from dataclasses import replace
import json
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
from quorune.rules.modal_selection import OPTIONAL_CAST_TRIGGER_MODAL_CAPABILITY
from quorune.rules.opponent_spell_or_permanent_return import OPPOSING_SPELL_CAPABILITY, OPPOSING_SPELL_FIELD
from scripts.build_test_database import build_fixture_database


SOURCE = "Hullbreaker Horror"
PROFILE = "commander_review"


class OptionalCastTriggerModeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixture_names = (
            "modal-cast-owner-return-cards", "opponent-spell-or-permanent-return-cards",
            "ability-source-counter-cards", "activation-target-view-cards",
        )
        db = Path(cls.temporary.name) / "modal-cast-owner-return.sqlite3"
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixture_names], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Optional cast modes", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def choose(self, game, *modes, targets=()):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (decision.kind, decision.actors))
        result = game.act("pilot:A", {"action_id": "choose", "modes": list(modes), "targets": list(targets)})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, modes, targets):
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "modes": modes, "targets": targets}
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def schema(self, game):
        payload = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertEqual((0, 0, 1), (payload["mode_count"], payload["min_modes"], payload["max_modes"]))
        return {mode: value["groups"][0]["legal_refs"] for mode, value in payload["mode_schemas"].items()}

    def test_exact_original_keeps_keywords_and_rejects_forged_modes(self):
        game = self.session(27701)
        source = self.add(game.engine, SOURCE)
        self.assert_closed(game, source)
        record = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(3, len(ir.faces[0].nodes))
        trigger, = (node for node in ir.faces[0].nodes if node.event == "spell.cast")
        self.assertEqual(record.oracle_text.split("\n", 2)[2], trigger.text)
        self.assertEqual({OPTIONAL_CAST_TRIGGER_MODAL_CAPABILITY, OPPOSING_SPELL_CAPABILITY},
                         set(trigger.capability_dependencies) & {OPTIONAL_CAST_TRIGGER_MODAL_CAPABILITY, OPPOSING_SPELL_CAPABILITY})
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if row.event == "spell.cast")
        for change in (
            {"event_condition": {"field": "controller", "op": "ne", "value": "$source.controller"}},
            {"active_zone": "stack"},
            {"target_schema": {**program.target_schema, "max_modes": 2}},
        ):
            with self.subTest(change=change):
                self.assertFalse(bind_semantic_program_runtime(replace(program, **change),
                    capability_registry=self.registry, profile=PROFILE)["strict"])
        for key, value in (("controller", "any"), ("categories", ["ability"]), ("zones", ["battlefield"])):
            with self.subTest(group=key):
                schema = deepcopy(program.target_schema)
                schema["modes"]["mode_1"]["groups"][0][key] = value
                self.assertFalse(bind_semantic_program_runtime(replace(program, target_schema=schema),
                    capability_registry=self.registry, profile=PROFILE)["strict"])
        schema = deepcopy(program.target_schema)
        schema["modes"]["mode_1"]["effects"][0][OPPOSING_SPELL_FIELD] = True
        self.assertFalse(bind_semantic_program_runtime(replace(program, target_schema=schema),
            capability_registry=self.registry, profile=PROFILE)["strict"])
        for text in (
            record.oracle_text.replace("Whenever you cast a spell", "Whenever an opponent casts a spell"),
            record.oracle_text.replace("choose up to one", "choose any number"),
            record.oracle_text.replace("you don't control", "you own"),
        ):
            with self.subTest(text=text):
                result = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                             capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

    def test_branch_capabilities_are_required_without_producer_annotations(self):
        game = self.session(27702)
        source = self.add(game.engine, SOURCE)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if row.event == "spell.cast")
        for capability in (OPTIONAL_CAST_TRIGGER_MODAL_CAPABILITY, OPPOSING_SPELL_CAPABILITY):
            with self.subTest(capability=capability):
                remaining = tuple(item for item in program.capability_dependencies if item != capability)
                closure = self.registry.closure(remaining, profile=PROFILE).to_dict()
                bad = replace(program, capability_dependencies=remaining, capability_closure=closure,
                              provenance={**program.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
                proof = bind_semantic_program_runtime(bad, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + capability, proof["blockers"])

    def test_actual_spell_and_copy_return_without_countering_and_replay(self):
        for copy in (False, True):
            with self.subTest(copy=copy):
                game = self.session(27710 + copy)
                source = self.add(game.engine, SOURCE)
                opponent_spell = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
                copier = self.add(game.engine, "Twincast", seat="C", zone="hand")
                trigger_spell = self.add(game.engine, "Unsummon", zone="hand")
                hidden = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="private-other-card")
                self.assert_closed(game, source)
                self.prepare(game, mana={"U": 1})
                game.state.players["B"].mana_pool.update({"C": 1, "R": 2})
                game.state.players["C"].mana_pool["U"] = 2
                self.checkpoint(game)
                self.cast(game, "B", opponent_spell)
                original = game.state.stack[-1]
                self.assertFalse(game.engine._stack_item_can_be_countered(original))
                self.assertEqual(0, self.trigger_count(game, source))
                target = original
                if copy:
                    self.cast(game, "C", copier, [original.ref])
                    self.resolve_top(game)
                    target, = (row for row in game.state.stack if row.kind == "spell_copy")
                    self.assertEqual(0, self.trigger_count(game, source))
                self.cast(game, "A", trigger_spell, [source.ref])
                trigger = game.state.stack[-1]
                own = game.state.stack[-2]
                candidates = self.schema(game)
                self.assertEqual({original.ref, target.ref}, set(candidates["mode_1"]))
                self.assertIn(source.ref, candidates["mode_2"])
                for mode, wrong in (("mode_1", own.ref), ("mode_1", trigger.ref), ("mode_2", target.ref)):
                    self.reject(game, "A", [mode], [wrong])
                self.reject(game, "B", ["mode_1"], [target.ref])
                self.reject(game, "A", ["mode_1", "mode_2"], [target.ref, source.ref])
                self.reject(game, "A", ["mode_1"], [])
                self.reject(game, "A", [], [target.ref])
                self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
                game = self.replay(game, load=True)
                self.choose(game, "mode_1", targets=[target.ref])
                self.assertEqual((trigger.ref, ["mode_1"], [target.ref]),
                                 (game.state.stack[-1].ref, game.state.stack[-1].modes, game.state.stack[-1].targets))
                self.resolve_top(game)
                self.assertNotIn(target.ref, {row.ref for row in game.state.stack})
                self.assertNotIn(target.ref, {event.details.get("stack") for event in game.state.events
                                             if event.code == "stack.counter"})
                if copy:
                    self.assertEqual("stack", game.state.cards[opponent_spell.object_id].zone)
                    copied_card = game.state.cards.get(target.card_object_id)
                    self.assertTrue(copied_card is None or copied_card.zone == "outside")
                else:
                    self.assertIn(opponent_spell.object_id, game.state.players["B"].zones["hand"])
                self.replay(game, load=True)

    def test_actual_permanent_mode_uses_owner_and_revalidates_departed_target(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                game = self.session(27720 + stale)
                source = self.add(game.engine, SOURCE)
                own = self.add(game.engine, "Grizzly Bears", ref="hullbreaker-own-body")
                borrowed = self.add(game.engine, "Grizzly Bears", seat="C", zone="hand", ref="hullbreaker-borrowed-body")
                land = self.add(game.engine, "Plains", seat="D", ref="hullbreaker-land")
                trigger_spell = self.add(game.engine, "Grizzly Bears", zone="hand", ref="hullbreaker-cast-body")
                response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                game.engine.move_card(borrowed.object_id, "battlefield", controller="A", log=False)
                self.prepare(game, mana={"C": 1, "G": 1})
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                self.cast(game, "A", trigger_spell)
                candidates = self.schema(game)
                self.assertEqual({source.ref, own.ref, borrowed.ref}, set(candidates["mode_2"]))
                self.assertNotIn("mode_1", candidates)
                self.reject(game, "A", ["mode_2"], [land.ref])
                self.choose(game, "mode_2", targets=[borrowed.ref])
                if stale:
                    self.cast(game, "B", response, [borrowed.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertIn(borrowed.object_id, game.state.players["C"].zones["hand"])
                self.assertNotIn(borrowed.object_id, game.state.players["A"].zones["hand"])
                self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
                self.assertEqual(stale, any(event.code == "target.illegal" for event in game.state.events))
                self.replay(game, load=True)

    def test_actual_flash_counter_prohibition_and_zero_modes_replay(self):
        game = self.session(27730)
        source = self.add(game.engine, SOURCE, zone="hand")
        trigger_spell = self.add(game.engine, "Grizzly Bears", zone="hand")
        opponent_spell = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        self.prepare(game, mana={"C": 6, "G": 1, "U": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "R": 2})
        game.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast(game, "B", opponent_spell)
        self.cast(game, "A", source)
        source_spell = game.state.stack[-1]
        self.assertEqual(0, self.trigger_count(game, source))
        self.cast(game, "C", counter, [source_spell.ref])
        self.resolve_top(game)
        self.assertIn(source_spell.ref, {item.ref for item in game.state.stack})
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.resolve_top(game)
        self.cast(game, "A", trigger_spell)
        self.assertEqual({"mode_2": [source.ref]}, self.schema(game))
        trigger = game.state.stack[-1]
        game = self.replay(game, load=True)
        self.choose(game)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertNotIn(trigger.ref, {item.ref for item in game.state.stack})
        self.assertIn({"stack": trigger.ref, "reason": "no_modes_chosen"},
                      [event.details for event in game.state.events if event.code == "stack.trigger.removed"])
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual("stack", game.state.cards[trigger_spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_trigger_with_no_legal_modes_is_removed_without_a_decision(self):
        game = self.session(27740)
        source = self.add(game.engine, SOURCE)
        safekeeper = self.add(game.engine, "Sylvan Safekeeper")
        lands = [self.add(game.engine, "Plains", ref=f"safekeeper-price-{index}") for index in range(2)]
        spell = self.add(game.engine, "Grizzly Bears", zone="hand")
        self.prepare(game, mana={"C": 1, "G": 1})
        self.checkpoint(game)
        for target, land in zip((source, safekeeper), lands, strict=True):
            self.priority(game, "A")
            actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            activation, = (row for row in actions if row["id"].startswith(f"activate:{safekeeper.ref}:"))
            result = game.act("pilot:A", {"action_id": activation["id"], "targets": [target.ref],
                                         "cost_cards": [land.ref], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
            self.resolve_top(game)
            self.assertIn("shroud", game.engine._combat_keywords(game.state.cards[target.object_id]))
        self.cast(game, "A", spell)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual([spell.object_id], [item.card_object_id for item in game.state.stack])
        self.assertTrue(any(event.code == "stack.trigger.removed" and event.details.get("reason") == "no_modes_chosen"
                            for event in game.state.events))
        self.replay(game, load=True)

    def test_omitting_optional_cast_context_or_spell_target_guard_is_detected(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        with patch("quorune.rules.modal_selection.optional_cast_trigger_context_matches", return_value=False):
            incomplete = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", incomplete.status)
        with patch("quorune.compiler.fixed_owner_zone_move_templates.spell_target_schema",
                   return_value={"zones": ["stack"], "categories": ["spell"], "count": 1}):
            incomplete = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", incomplete.status)
