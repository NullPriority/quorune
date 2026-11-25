"""Native fixed-color results with unrestricted modal target announcement."""

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
from quorune.conditional_target_preparation_model import RESOLUTION_TARGET_COLOR_CAPABILITY, RESOLUTION_TARGET_COLOR_FIELD
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class ConditionalModalTargetColorTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        database = Path(cls.temporary.name) / "conditional-modal-target-color.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "conditional-modal-target-color-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Conditional modal colors", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def _ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"C": 2, "U": 2, "R": 2, "W": 1})
        game.state.players["C"].mana_pool["U"] = 4
        self.prepare(game, mana={"U": 4, "R": 4})
        self.checkpoint(game)

    def _mode_command(self, game, seat, card, mode, target):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(candidate for candidate in actions if candidate["id"] == "cast:" + card.ref)
        refs = [ref for group in action["target_schema"]["mode_schemas"][mode]["groups"] for ref in group["legal_refs"]]
        self.assertIn(target, refs)
        return {"action_id": action["id"], "modes": [mode], "targets": [target], "pay": "auto"}

    def _cast_mode(self, game, seat, card, mode, target):
        command = self._mode_command(game, seat, card, mode, target)
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        stack_item = game.state.stack[-1]
        self.assertEqual([mode], stack_item.modes)
        return stack_item.ref

    def _target_spell(self, game, card):
        return next(item.ref for item in game.state.stack if item.card_object_id == card.object_id)

    def _resolved(self, game, ref):
        return any(event.code == "stack.resolve" and event.details.get("stack") == ref for event in game.state.events)

    def test_hydroblast_checks_the_existing_spell_color_at_resolution(self):
        game = self.session(608_700_01)
        blast = self.add(game.engine, "Hydroblast", zone="hand", ref="HYDRO-SPELL-RECOLOR")
        second = self.add(game.engine, "Hydroblast", zone="hand", ref="HYDRO-SPELL-MATCH")
        third = self.add(game.engine, "Hydroblast", zone="hand", ref="HYDRO-PERMANENT-MATCH")
        red = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="HYDRO-BOLT-ONE")
        later = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="HYDRO-BOLT-TWO")
        change = self.add(game.engine, "Moonlace", seat="C", zone="hand", ref="HYDRO-RECOLOR")
        permanent = self.add(game.engine, "Raging Goblin", seat="D", ref="HYDRO-RED-PERMANENT")
        self._ready(game, blast, red, change)

        self.cast(game, "B", red, ["D"])
        target = self._target_spell(game, red)
        blast_ref = self._cast_mode(game, "A", blast, "mode_1", target)
        self.cast(game, "C", change, [target])
        self.resolve_top(game)
        self.assertEqual([], game.engine._target_snapshot(target)["colors"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertTrue(self._resolved(game, blast_ref))
        self.assertEqual(target, self._target_spell(game, red))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["D"].life)

        self.cast(game, "B", later, ["D"])
        target = self._target_spell(game, later)
        self._cast_mode(game, "A", second, "mode_1", target)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["D"].life)
        self.assertFalse(self._resolved(game, target))

        self._cast_mode(game, "A", third, "mode_2", permanent.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[permanent.object_id].zone)
        self.replay(game, load=True)

    def test_pyroblast_keeps_colorless_targets_legal_and_revalidates_incarnations(self):
        game = self.session(608_700_02)
        neutral_blast = self.add(game.engine, "Pyroblast", zone="hand", ref="PYRO-NONMATCH")
        old_blast = self.add(game.engine, "Pyroblast", zone="hand", ref="PYRO-OLD-OBJECT")
        last = self.add(game.engine, "Pyroblast", zone="hand", ref="PYRO-MATCH")
        neutral = self.add(game.engine, "Sol Ring", seat="B", ref="PYRO-COLORLESS")
        blue = self.add(game.engine, "Merfolk of the Pearl Trident", seat="C", ref="PYRO-BLUE")
        blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand", ref="PYRO-BLINK")
        self._ready(game, neutral_blast, blink)

        command = self._mode_command(game, "A", neutral_blast, "mode_2", neutral.ref)
        initial = authoritative_state_hash(game.state)
        wrong = game.act("pilot:B", command)
        self.assertFalse(wrong.ok)
        invalid = game.act("pilot:A", {**command, "modes": ["mode_1"]})
        self.assertFalse(invalid.ok)
        self.assertEqual(initial, authoritative_state_hash(game.state))
        neutral_ref = self._cast_mode(game, "A", neutral_blast, "mode_2", neutral.ref)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self._resolved(game, neutral_ref))
        self.assertEqual("battlefield", game.state.cards[neutral.object_id].zone)

        prior_identity = game.state.cards[blue.object_id].logical_object_id
        stale_ref = self._cast_mode(game, "A", old_blast, "mode_2", blue.ref)
        self.cast(game, "B", blink, [blue.ref])
        self.resolve_top(game)
        self.assertNotEqual(prior_identity, game.state.cards[blue.object_id].logical_object_id)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self._resolved(game, stale_ref))
        self.assertEqual("battlefield", game.state.cards[blue.object_id].zone)

        self._cast_mode(game, "A", last, "mode_2", blue.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[blue.object_id].zone)
        self.replay(game, load=True)

    def test_pyroblast_copy_preserves_mode_across_new_target_choice_and_replay(self):
        game = self.session(608_700_03)
        blast = self.add(game.engine, "Pyroblast", zone="hand", ref="PYRO-COPY-SOURCE")
        red = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="PYRO-COPY-RED")
        blue = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="PYRO-COPY-BLUE")
        copy_spell = self.add(game.engine, "Twincast", seat="C", zone="hand", ref="PYRO-COPY-EFFECT")
        victim = self.add(game.engine, "Grizzly Bears", seat="D", ref="PYRO-COPY-VICTIM")
        self._ready(game, blast, red, blue, copy_spell)
        self.cast(game, "B", red, ["D"])
        first = self._target_spell(game, red)
        self.cast(game, "B", blue, [victim.ref])
        second = self._target_spell(game, blue)
        original = self._cast_mode(game, "A", blast, "mode_1", second)
        self.cast(game, "C", copy_spell, [original])
        choice = self.resolve(game)
        self.assertIsNotNone(choice)
        self.assertEqual(["C"], choice.actors)
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        game = self.replay(game, load=True)
        result = game.act("pilot:C", {"action_id": "choose", "targets": [first]})
        self.assertTrue(result.ok, result.summary)
        copy = next(item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual((["mode_1"], [first]), (copy.modes, copy.targets))
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self._resolved(game, copy.ref))
        self.assertTrue(self._resolved(game, original))
        self.assertFalse(self._resolved(game, second))
        self.assertEqual("battlefield", game.state.cards[victim.object_id].zone)
        self.assertEqual(37, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_pyroblast_historical_named_modes_remain_pinned_in_saved_records(self):
        game = self.session(608_700_04)
        blast = self.add(game.engine, "Pyroblast", zone="hand", ref="PYRO-HISTORICAL")
        blue = self.add(game.engine, "Merfolk of the Pearl Trident", seat="B", ref="PYRO-HISTORICAL-TARGET")
        original = SemanticRegistry().get(f"{blast.oracle_id}:spell:front")
        self.assertEqual({"counter", "destroy"}, set(original.target_schema["modes"]))
        game.engine.semantics.put(original)
        self._ready(game)
        self._cast_mode(game, "A", blast, "destroy", blue.ref)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[blue.object_id].zone)
        loaded = self.replay(game, load=True)
        self.assertEqual(original.target_schema, loaded.engine.semantics.get(original.key).target_schema)

    def test_modal_color_authority_rejects_unbound_effects_and_target_filters(self):
        game = self.session(608_700_05)
        for name, color in (("Hydroblast", "R"), ("Pyroblast", "U")):
            with self.subTest(card=name):
                card = self.add(game.engine, name)
                self.assert_closed(game, card)
                record = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(record, capability_registry=self.registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                program = next(p for p in game.engine.semantics.programs_for_oracle(record.oracle_id) if p.event == "resolve")
                self.assertEqual(("mode_1", "mode_2"), tuple(program.target_schema["modes"]))
                self.assertIn(RESOLUTION_TARGET_COLOR_CAPABILITY, program.capability_dependencies)
                for definition in program.target_schema["modes"].values():
                    self.assertNotIn("colors_any", definition)
                    self.assertEqual({"schema_version": 1, "color": color},
                                     definition["effects"][0][RESOLUTION_TARGET_COLOR_FIELD])
                malformed = []
                for field, value in (("colors_any", [color]), ("mechanics", [])):
                    schema = deepcopy(dict(program.target_schema))
                    schema["modes"]["mode_1"][field] = value
                    malformed.append(replace(program, target_schema=schema))
                schema = deepcopy(dict(program.target_schema))
                schema["modes"]["mode_1"]["effects"][0][RESOLUTION_TARGET_COLOR_FIELD]["schema_version"] = True
                malformed.append(replace(program, target_schema=schema))
                malformed.append(replace(program, event="activate"))
                for changed in malformed:
                    self.assertFalse(bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile="commander_review",
                    )["strict"])
                data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
                row = next(r for r in data["capabilities"] if r["id"] == RESOLUTION_TARGET_COLOR_CAPABILITY)
                row.update(status="blocked", blockers=["the current color authority was withdrawn"])
                self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=CapabilityRegistry(data)).status)
                with patch("quorune.compiler.conditional_target_preparation.fixed_target_color_result_template", return_value=None):
                    self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)
