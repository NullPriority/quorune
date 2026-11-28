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
from quorune.rules.subtype_damage_controller import CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

BROOD = "Brood Sliver"
ESSENCE = "Essence Sliver"
PROFILE = "commander_review"


class SliverDamageResultsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "sliver-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/sliver-damage-results-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed damage and changing source control", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def add_providers(self, game):
        providers = {name: self.add(game.engine, name, seat="A") for name in (BROOD, ESSENCE)}
        for provider in providers.values():
            self.assert_closed(game, provider)
        return providers

    def accept(self, game, seat, command):
        result = game.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def activate(self, game, seat, card, ref):
        self.priority(game, seat)
        matrix = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in matrix if row["id"].startswith(f"activate:{card.ref}:")]
        self.accept(game, seat, {"action_id": offered["id"], "targets": [ref], "pay": "auto"})
        self.resolve_top(game)

    def combat(self, seed, *, leaves=False, prevents=False, non_sliver=False):
        game = self.session(seed)
        host = game.engine
        abilities = self.add_providers(game)
        original = self.add(host, "Grizzly Bears" if non_sliver else "Homing Sliver", seat="B", ref="damage-dealer")
        original.acquired_control_turn_count = -1
        self.add(host, "Grizzly Bears", seat="C", ref="optional-blocker")
        shield = self.add(host, "Samite Healer", seat="C", ref="partial-prevention")
        shield.acquired_control_turn_count = -1
        control = self.add(host, "Word of Seizing", seat="C", zone="hand", ref="dealer-control")
        bounce = self.add(host, "Boomerang", seat="D", zone="hand", ref="dealer-departure") if leaves else None
        fog = self.add(host, "Fog", seat="C", zone="hand", ref="all-damage-prevented") if prevents else None
        for index in range(5):
            self.add(host, "Mountain", seat="C", ref=f"control-mountain-{index}")
        self.add(host, "Forest", seat="C")
        for index in range(2):
            self.add(host, "Island", seat="D", ref=f"bounce-island-{index}")
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.accept(game, "B", {"action_id": "attack", "attackers": {original.ref: "C"}})
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        self.accept(game, "C", {"action_id": "block", "blocks": {}})
        if fog:
            self.cast_for(game, "C", fog)
            self.resolve_top(game)
        else:
            self.activate(game, "C", shield, "C")
        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.pending_decision.kind in {"priority", "trigger.order"})
        return game, abilities, original, control, bounce

    def test_brood_choice_and_essence_gain_follow_current_and_departure_controller(self):
        for index, leaves in enumerate((False, True)):
            with self.subTest(leaves=leaves):
                game, providers, dealer, control, bounce = self.combat(7315700 + index, leaves=leaves)
                self.assertEqual(39, game.state.players["C"].life)
                self.assertEqual(("trigger.order", ["A"]), (game.state.pending_decision.kind, game.state.pending_decision.actors))
                occurrences = [row for batch in game.state.pending_trigger_batches for row in batch.items]
                self.assertEqual(2, len(occurrences))
                self.assertTrue(all(row.event_facts["amount"] == 1 for row in occurrences))
                ordered = sorted(occurrences, key=lambda row: row.source_object_id == providers[BROOD].object_id)
                self.accept(game, "A", {"action_id": "order", "order": [row.ref for row in ordered]})
                self.assertEqual(providers[BROOD].object_id, game.state.stack[-1].source_object_id)
                self.cast_for(game, "C", control, [dealer.ref])
                self.resolve_top(game)
                self.assertEqual("C", game.state.cards[dealer.object_id].controller)
                if bounce:
                    self.cast_for(game, "D", bounce, [dealer.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[dealer.object_id].zone)
                game = self.replay(game, load=True)
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
                before = authoritative_state_hash(game.state)
                command = {"action_id": "choose", "choice": "create" if leaves else "decline"}
                for seat in ("A", "B", "D"):
                    self.assertFalse(game.act(f"pilot:{seat}", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.assertEqual(39, game.state.players["C"].life)
                game = self.replay(game, load=True)
                self.accept(game, "C", command)
                tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
                self.assertEqual(int(leaves), len(tokens))
                if leaves:
                    token, = tokens
                    self.assertEqual(("C", "C", "Sliver Token"), (token.controller, token.owner, token.printed_name))
                    self.assertEqual((1, 1), tuple(game.engine._numeric_stat(token.object_id, field)
                                                 for field in ("power", "toughness")))
                    self.assertEqual([], game.engine._effective_card_data(token)["colors"])
                self.resolve_top(game)
                self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)

    def test_noncombat_sliver_damage_to_a_permanent_triggers_only_essence(self):
        game = self.session(7315702)
        host = game.engine
        providers = self.add_providers(game)
        dealer = self.add(host, "Homing Sliver", seat="B", ref="equipped-sliver")
        dealer.acquired_control_turn_count = -1
        bow = self.add(host, "Viridian Longbow", seat="B")
        victim = self.add(host, "Grizzly Bears", seat="C", ref="permanent-damage-recipient")
        self.prepare(game, actor="B", active="B", mana={"C": 3})
        self.checkpoint(game)
        self.activate(game, "B", bow, dealer.ref)
        self.activate(game, "B", dealer, victim.ref)
        trigger, = game.state.stack
        self.assertEqual(("A", providers[ESSENCE].object_id, 1), (trigger.controller, trigger.source_object_id, trigger.context["amount"]))
        self.assertEqual(1, game.state.cards[victim.object_id].marked_damage)
        self.assertEqual(40, game.state.players["B"].life)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([40, 41, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_prevented_or_wrong_subtype_damage_produces_neither_trigger(self):
        for index, (fog, non_sliver) in enumerate(((True, False), (False, True))):
            with self.subTest(fog=fog, non_sliver=non_sliver):
                game, _, _, _, _ = self.combat(7315704 + index, prevents=fog, non_sliver=non_sliver)
                self.assertFalse(game.state.stack)
                self.assertEqual(40 if fog else 39, game.state.players["C"].life)
                self.replay(game, load=True)

    def test_exact_effects_reject_wrong_event_recipient_and_missing_authority(self):
        for name in (BROOD, ESSENCE):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                options = {"capability_registry": self.registry, "capability_profile": PROFILE}
                self.assertEqual("exact", compile_oracle_card(card, **options).status)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report)
                program, = semantics.programs_for_oracle(card.oracle_id)
                effect, = program.effects
                deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
                missing = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                result = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])
                for invalid in (replace(program, event_condition={"field": "combat", "op": "truthy", "value": True}),
                                replace(program, effects=({**effect, "player": "$controller"},))):
                    self.assertFalse(bind_semantic_program_runtime(invalid, capability_registry=self.registry,
                                                                  profile=PROFILE)["strict"])
                with patch("quorune.compiler.subtype_damage_controller.subtype_damage_binding_parts", return_value=None):
                    self.assertNotEqual("exact", compile_oracle_card(card, **options).status)
