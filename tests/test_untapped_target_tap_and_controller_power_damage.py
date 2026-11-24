"""Exercise one untapped target that deals its own current-power damage."""

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
from quorune.target_controller_reference import target_controller_reference
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Backlash", "Traitor's Roar")


class UntappedTargetTapAndControllerPowerDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "untapped-target-controller-damage.sqlite3"
        fixture = ROOT / "tests/fixtures/untapped-target-tap-and-controller-power-damage-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Untapped target power damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def source_program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
        return card, program

    def test_both_originals_bind_one_untapped_target_and_preserve_printed_conspire(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, program = self.source_program(name)
                self.assertEqual(["creature"], program.target_schema["types_any"])
                self.assertIs(program.target_schema["state_predicate"]["tapped"], False)
                tap, damage = program.effects
                self.assertEqual({"op": "tap", "card": "$target.0"}, tap)
                self.assertEqual(("creature_power_damage", "$target.0", target_controller_reference()),
                                 (damage["op"], damage["source"], damage["target"]))
                ir = compile_oracle_card(card, capability_registry=self.registry)
                self.assertEqual(name == "Traitor's Roar", any(
                    node.text.startswith("Conspire") for face in ir.faces for node in face.nodes))
                for text in (
                    card.oracle_text.replace("target untapped creature", "target creature"),
                    card.oracle_text.replace("its controller.", "you."),
                    card.oracle_text.replace("equal to its power", "equal to its toughness"),
                    card.oracle_text.replace("Tap target", "Untap target"),
                ):
                    self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                        capability_registry=self.registry).status, text)

    def test_raw_program_rejects_target_widening_and_rebound_damage_roles(self):
        _, program = self.source_program("Backlash")
        tap, damage = program.effects
        for changed in (
            replace(program, effects=(damage, tap)),
            replace(program, effects=(tap, {**damage, "source": "$source"})),
            replace(program, effects=(tap, {**damage, "target": "$controller"})),
            replace(program, target_schema={key: value for key, value in program.target_schema.items()
                                            if key != "state_predicate"}),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                profile="commander_review")
            self.assertFalse(result["strict"], result)

    def test_actual_spell_uses_current_power_and_the_creatures_own_damage(self):
        for name, growth, lost in (("Grizzly Bears", True, 5), ("Child of Night", False, 0)):
            with self.subTest(name=name):
                game = self.session(61343401 + int(growth))
                target = self.add(game.engine, name, seat="B")
                spell = self.add(game.engine, "Backlash", zone="hand")
                boost = self.add(game.engine, "Giant Growth", seat="C", zone="hand") if growth else None
                for card in (target, spell, boost):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, mana={"C": 1, "B": 1, "R": 1})
                game.state.players["C"].mana_pool["G"] = 1
                self.checkpoint(game)
                life = {seat: game.state.players[seat].life for seat in "ABCD"}
                self.cast_for(game, "A", spell, [target.ref])
                if boost is not None:
                    self.cast_for(game, "C", boost, [target.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertTrue(game.state.cards[target.object_id].tapped)
                damage, = game.engine._current_turn_history("player_damaged")
                self.assertEqual(("B", target.logical_object_id, "B", 5 if growth else 2),
                                 (damage.actor, damage.object_incarnation, damage.target, damage.amount))
                self.assertNotIn("history:combat", damage.types)
                self.assertEqual({**life, "B": life["B"] - lost},
                                 {seat: game.state.players[seat].life for seat in "ABCD"})
                self.replay(game, load=True)

    def test_actual_tap_in_response_invalidates_the_entire_original_spell(self):
        game = self.session(61343403)
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        spell = self.add(game.engine, "Backlash", zone="hand")
        response = self.add(game.engine, "Twiddle", seat="C", zone="hand")
        for card in (spell, response):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 1, "R": 1})
        game.state.players["C"].mana_pool["U"] = 1
        self.checkpoint(game)
        life = game.state.players["B"].life
        self.cast_for(game, "A", spell, [target.ref])
        self.cast_for(game, "C", response, [target.ref])
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        selected = game.act("pilot:C", {"action_id": "choose", "choice": "tap"})
        self.assertTrue(selected.ok, selected.summary)
        self.assertTrue(game.state.cards[target.object_id].tapped)
        self.resolve_top(game)
        self.assertEqual(life, game.state.players["B"].life)
        self.assertFalse(game.engine._current_turn_history("player_damaged"))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_conspire_keeps_ordinary_copy_and_untapped_target_revalidation(self):
        for retarget in (False, True):
            with self.subTest(retarget=retarget):
                game = self.session(61343404 + int(retarget))
                engine = game.engine
                spell = self.add(engine, "Traitor's Roar", zone="hand")
                helpers = [self.add(engine, name) for name in ("Walking Corpse", "Goblin Piker")]
                original = self.add(engine, "Grizzly Bears", seat="B")
                alternate = self.add(engine, "Savannah Lions", seat="C")
                self.assert_closed(game, spell)
                self.prepare(game, mana={"B": 5})
                self.checkpoint(game)
                life = {seat: game.state.players[seat].life for seat in "BC"}
                offer = self.action(game, "A", game.state.cards[spell.object_id])
                option, = [row["id"] for row in offer["cost_options"] if "+conspire:" in row["id"]]
                command = {"action_id": offer["id"], "targets": [original.ref], "cost_option": option, "pay": "auto"}
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertTrue(all(game.state.cards[card.object_id].tapped for card in helpers))
                pending = self.resolve(game)
                self.assertEqual("semantic.storm", pending.kind)
                chosen = alternate if retarget else original
                reply = {"action_id": "choose", "copy_targets": [[chosen.ref]]}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:D", reply).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                result = game.act("pilot:A", reply)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(1, sum(item.kind == "spell_copy" for item in game.state.stack))
                self.resolve_top(game)
                self.assertTrue(game.state.cards[chosen.object_id].tapped)
                self.resolve_top(game)
                self.assertEqual(life["B"] - 2, game.state.players["B"].life)
                self.assertEqual(life["C"] - (2 if retarget else 0), game.state.players["C"].life)
                self.assertEqual(retarget, game.state.cards[alternate.object_id].tapped)
                self.replay(game, load=True)
