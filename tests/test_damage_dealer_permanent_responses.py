"""Exercise non-targeted responses to one original permanent damage dealer."""

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
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("No Mercy", "Dissipation Field", "Teysa, Envoy of Ghosts")
DEALER = "$event.permanent_object"


class DamageDealerPermanentResponsesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "damage-dealer-permanent-responses.sqlite3"
        fixture = ROOT / "tests/fixtures/damage-dealer-permanent-responses-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original damage dealer", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "damage.dealt"]
        return card, program

    def test_three_complete_originals_admit_exact_dealers_without_announcing_targets(self):
        for name, operation in zip(ORIGINALS, ("destroy", "bounce", "destroy"), strict=True):
            with self.subTest(name=name):
                card, program = self.program(name)
                self.assertEqual("battlefield", program.active_zone)
                self.assertIsNone(program.target_schema)
                first, *siblings = program.effects
                self.assertEqual({"op": operation, "card": DEALER}, first)
                if name == "Teysa, Envoy of Ghosts":
                    self.assertEqual(["create_token"], [effect["op"] for effect in siblings])
                else:
                    self.assertFalse(siblings)
                for changed in (
                    card.oracle_text.replace("damage to you", "damage to an opponent"),
                    card.oracle_text.replace("Whenever a creature", "Whenever a spell").replace("Whenever a permanent", "Whenever a spell"),
                    card.oracle_text.replace("destroy it", "destroy target creature").replace("destroy that creature", "destroy target creature")
                        .replace("return it", "return target permanent"),
                ):
                    ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
                    self.assertNotEqual("exact", ir.status, changed)

    def test_raw_dealer_cannot_change_recipient_or_lose_the_original_token_result(self):
        _, program = self.program("Teysa, Envoy of Ghosts")
        first, token = program.effects
        for changed in (
            replace(program, event_condition=None),
            replace(program, effects=(first,)),
            replace(program, effects=(token, first)),
            replace(program, effects=({**first, "card": "$target.0"}, token)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def activate_damage(self, game, source, actor):
        self.priority(game, actor)
        rows = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + source.ref + ":")]
        result = game.act("pilot:" + actor, {"action_id": action, "targets": ["A"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)

    def test_actual_own_creature_noncombat_damage_triggers_no_mercy_and_blink_does_not_rebind_it(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                game = self.session(61343701 + int(blink))
                engine = game.engine
                mercy = self.add(engine, "No Mercy")
                bear = self.add(engine, "Grizzly Bears")
                backlash = self.add(engine, "Backlash", zone="hand")
                response = self.add(engine, "Cloudshift", zone="hand") if blink else None
                for card in (mercy, backlash, response):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, mana={"C": 1, "B": 1, "R": 1, "W": 2})
                self.checkpoint(game)
                life = game.state.players["A"].life
                original = bear.logical_object_id
                self.cast_for(game, "A", backlash, [bear.ref])
                self.resolve_top(game)
                self.assertEqual(life - 2, game.state.players["A"].life)
                self.assertEqual(1, self.trigger_count(game, mercy))
                queued = next(item for item in game.state.stack if item.source_object_id == mercy.object_id)
                self.assertFalse(queued.targets)
                game = self.replay(game, load=True)
                if response is not None:
                    self.cast_for(game, "A", response, [bear.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(original, game.state.cards[bear.object_id].logical_object_id)
                self.resolve_top(game)
                self.assertEqual("battlefield" if blink else "graveyard", game.state.cards[bear.object_id].zone)
                self.replay(game, load=True)

    def test_actual_noncreature_permanent_returns_to_its_owner_and_an_instant_does_not_trigger(self):
        game = self.session(61343703)
        engine = game.engine
        field = self.add(engine, "Dissipation Field")
        rod = self.add(engine, "Rod of Ruin", seat="C")
        engine.change_control(rod.object_id, "B", reason="Prepare borrowed damage source")
        shock = self.add(engine, "Shock", seat="B", zone="hand")
        for card in (field, rod, shock):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"R": 1, "C": 3})
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.cast_for(game, "B", shock, ["A"])
        self.resolve_top(game)
        self.assertEqual(life - 2, game.state.players["A"].life)
        self.assertEqual(0, self.trigger_count(game, field))
        self.activate_damage(game, rod, "B")
        self.assertEqual(life - 3, game.state.players["A"].life)
        self.assertEqual(1, self.trigger_count(game, field))
        self.resolve_top(game)
        self.assertIn(rod.object_id, game.state.players["C"].zones["hand"])
        self.assertNotIn(rod.object_id, game.state.players["B"].zones["hand"])
        self.replay(game, load=True)

    def test_actual_teysa_ignores_noncombat_and_creates_its_token_after_failed_destroy(self):
        game = self.session(61343704)
        engine = game.engine
        teysa = self.add(engine, "Teysa, Envoy of Ghosts")
        pinger = self.add(engine, "Prodigal Sorcerer", seat="B")
        sentinel = self.add(engine, "Darksteel Sentinel", seat="B")
        for creature in (pinger, sentinel):
            creature.acquired_control_turn_count = -1
        for card in (teysa, pinger, sentinel):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.activate_damage(game, pinger, "B")
        self.assertEqual(0, self.trigger_count(game, teysa))
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attacked = game.act("pilot:B", {"action_id": "attack", "attackers": {sentinel.ref: "A"}})
        self.assertTrue(attacked.ok, attacked.summary)
        self.until(game, lambda current: self.trigger_count(current, teysa) == 1)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[sentinel.object_id].zone)
        token, = [card for card in game.state.cards.values()
                  if card.is_token and card.zone == "battlefield" and card.controller == "A"]
        values = game.engine._effective_card_data(token)
        self.assertEqual({"W", "B"}, set(values["colors"]))
        self.assertIn("Spirit", values["type_line"])
        self.assertIn("flying", game.engine._combat_keywords(token))
        self.assertEqual((1, 1), tuple(game.engine._numeric_stat(token.object_id, stat)
                                      for stat in ("power", "toughness")))
        self.replay(game, load=True)
