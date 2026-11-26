"""CR 508.3b: one direct declaration, two ordered token instructions."""

from collections import Counter
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attack_batch_model import ATTACHED_PLAYER_ATTACK_VARIANT, DIRECT_PLAYER_ATTACK_EVENT, direct_player_attack_condition
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_public_action_event_bindings import direct_player_attack_binding_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.attack_batches import (
    ATTACKING_OPPONENT_TOKEN_CAPABILITY, ATTACKING_OPPONENT_TOKEN_FIELD,
    attacking_opponent_token_effects, qualified_attack_batch_runtime_dependencies,
)
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AttachedPlayerAttackTokenShapeTests(unittest.TestCase):
    def test_two_token_instructions_require_the_exact_attached_player_declaration(self):
        text = "Whenever enchanted player is attacked, create a Gold token. Each opponent attacking that player does the same."
        binding = direct_player_attack_binding_spec(text)
        self.assertEqual(ATTACHED_PLAYER_ATTACK_VARIANT, binding.variant)
        self.assertIn(ATTACKING_OPPONENT_TOKEN_CAPABILITY, binding.capabilities)
        for other in (text.replace("Gold", "Treasure"), text.replace("opponent", "player"),
                      text.replace("that player", "you"), text + " Draw a card."):
            self.assertIsNone(direct_player_attack_binding_spec(other))

        first, second = attacking_opponent_token_effects()
        exact = {
            "event": DIRECT_PLAYER_ATTACK_EVENT, "active_zone": "battlefield", "effects": (first, second),
            "event_condition": direct_player_attack_condition(ATTACHED_PLAYER_ATTACK_VARIANT),
            "target_schema": None, "cost_schema": None, "handlers": (), "coverage": (),
            "capability_dependencies": (),
        }
        self.assertIn(ATTACKING_OPPONENT_TOKEN_CAPABILITY,
                      qualified_attack_batch_runtime_dependencies(SimpleNamespace(**exact)))
        for changes in ({"event": "creature.attacks"}, {"active_zone": "hand"},
                        {"event_condition": direct_player_attack_condition("opponent_attacks_controller")},
                        {"target_schema": {"count": 1}}, {"effects": (first,)}, {"effects": (second, first)},
                        {"effects": (first, {**second, ATTACKING_OPPONENT_TOKEN_FIELD: True})},
                        {"effects": (first, {**second, "quantity": 2})}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                qualified_attack_batch_runtime_dependencies(SimpleNamespace(**{**exact, **changes}))


class AttachedPlayerAttackTokenNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "attached-player-attack-tokens.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/per-opponent-attacking-token-cards.json",
            ROOT / "tests/fixtures/additional-draw-step-cards.json",
            ROOT / "tests/fixtures/curse-of-opulence-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attached player attacks", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def place_curse(self, session, curse, defender):
        self.assert_closed(session, curse)
        self.prepare(session, mana={"R": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", curse, [defender])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(f"player:{defender}", session.state.cards[curse.object_id].attached_to)
        self.assertIn(curse.object_id, session.state.players[defender].attachments)

    def declare(self, session, seat, assignments):
        self.until(session, lambda game: game.state.pending_decision.kind == "combat.attackers"
                   and game.state.pending_decision.actors == [seat])
        command = {"action_id": "attack", "attackers": {card.ref: target for card, target in assignments}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def ready_attackers(self, session, seat, count):
        attackers = [self.add(session.engine, "Grizzly Bears", seat=seat, ref=f"bear-{seat}-{i}")
                     for i in range(count)]
        for card in attackers:
            card.acquired_control_turn_count = -1
        return attackers

    def activation(self, session, seat, card):
        self.priority(session, seat)
        rows = session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row["id"] for row in rows if row["id"].startswith(f"activate:{card.ref}:"))

    def gold(self, session):
        tokens = [card for card in session.state.cards.values()
                  if card.is_token and card.zone == "battlefield" and card.printed_name == "Gold Token"]
        for card in tokens:
            values = session.engine._effective_card_data(card)
            self.assertEqual("Artifact — Gold", values["type_line"])
            self.assertFalse(values.get("colors"))
            self.assertEqual(card.owner, card.controller)
            ability, = values["activated_abilities"]
            self.assertTrue(ability["sacrifice_source"])
            self.assertFalse(ability.get("tap_source"))
        return tokens

    def test_withdrawing_attacking_opponent_authority_blocks_whole_card(self):
        source = ROOT / "quorune/rules/capability-registry.json"
        raw = json.loads(source.read_text(encoding="utf-8"))
        row = next(row for row in raw["capabilities"] if row["id"] == ATTACKING_OPPONENT_TOKEN_CAPABILITY)
        row.update(status="blocked", blockers=["Attacking-opponent creation authority withheld"])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        result = compile_oracle_card(self.db.lookup("Curse of Opulence"), capability_registry=registry,
                                     capability_profile="commander_review")
        self.assertNotEqual("exact", result.status)
        self.assertIn(ATTACKING_OPPONENT_TOKEN_CAPABILITY, str(result.material_residuals))

    def test_actual_separate_players_get_one_gold_each_after_curse_departure_and_replay(self):
        session = self.session(50830117)
        curse = self.add(session.engine, "Curse of Opulence", zone="hand")
        direct, second, other = self.ready_attackers(session, "B", 3)
        destroy = self.add(session.engine, "Disenchant", seat="D", zone="hand")
        for index in range(2):
            self.add(session.engine, "Plains", seat="D", ref=f"destroy-land-{index}")
        self.place_curse(session, curse, "C")
        self.declare(session, "B", [(direct, "C"), (second, "C"), (other, "D")])
        self.assertEqual(1, self.trigger_count(session, curse))
        self.assertFalse(self.gold(session))
        self.cast(session, "D", destroy, [curse.ref])
        self.resolve_top(session)
        self.assertEqual("graveyard", session.state.cards[curse.object_id].zone)
        self.assertEqual(1, self.trigger_count(session, curse))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"A": 1, "B": 1}, Counter(card.controller for card in self.gold(session)))
        self.replay(session, load=True)

    def test_actual_controller_attack_creates_once_and_tapped_gold_pays_native_mana(self):
        session = self.session(50830118)
        curse = self.add(session.engine, "Curse of Opulence", zone="hand")
        direct, second = self.ready_attackers(session, "A", 2)
        manipulator = self.add(session.engine, "Icy Manipulator", seat="B")
        self.add(session.engine, "Plains", seat="B", ref="gold-tap-land")
        self.place_curse(session, curse, "C")
        self.declare(session, "A", [(direct, "C"), (second, "C")])
        self.assertEqual(1, self.trigger_count(session, curse))
        self.assertIsNone(self.resolve(session))
        gold, = self.gold(session)
        self.assertEqual("A", gold.controller)
        tap = self.activation(session, "B", manipulator)
        result = session.act("pilot:B", {"action_id": tap, "targets": [gold.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[gold.object_id].tapped)
        command = {"action_id": self.activation(session, "A", gold), "mana_output": {"U": 1}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players["A"].mana_pool["U"])
        self.assertFalse(session.state.stack)
        self.assertEqual("outside", session.state.cards[gold.object_id].zone)
        self.replay(session, load=True)

    def test_actual_departure_rechecks_the_original_attacked_player_at_resolution(self):
        session = self.session(50830119)
        curse = self.add(session.engine, "Curse of Opulence", zone="hand")
        direct, other = self.ready_attackers(session, "B", 2)
        bounce = self.add(session.engine, "Unsummon", seat="C", zone="hand")
        self.add(session.engine, "Island", seat="C", ref="attack-response-island")
        self.place_curse(session, curse, "C")
        self.declare(session, "B", [(direct, "C"), (other, "D")])
        self.assertEqual(1, self.trigger_count(session, curse))
        self.cast(session, "C", bounce, [direct.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[direct.object_id].zone)
        self.assertEqual({other.object_id: "D"}, session.state.combat.attackers)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(["A"], [card.controller for card in self.gold(session)])
        self.replay(session, load=True)

    def test_actual_permanent_defenders_and_undeclared_attacking_entry_do_not_trigger(self):
        session = self.session(50830120)
        curse = self.add(session.engine, "Curse of Opulence", zone="hand")
        adeline = self.add(session.engine, "Adeline, Resplendent Cathar")
        walker = self.add(session.engine, "Jace Beleren", seat="C")
        walker.counters["loyalty"] = 3
        battle, = session.engine.create_token(
            "D", name="Generic Defended Siege", battle_protector="C",
            characteristics={"type_line": "Token Battle — Siege", "defense": "3"},
        )
        attackers = self.ready_attackers(session, "A", 2)
        self.place_curse(session, curse, "C")
        self.declare(session, "A", [(attackers[0], walker.ref), (attackers[1], battle)])
        self.assertEqual(1, self.trigger_count(session, adeline))
        self.assertEqual(0, self.trigger_count(session, curse))
        while self.resolve(session) is not None:
            decision = session.packet("pilot:A", full=True)["decision"]
            schema = decision["legal_actions"][0]["choice_schema"]
            target = "C" if "C" in schema["legal_values"] else schema["legal_values"][0]
            result = session.act("pilot:A", {"action_id": "choose", "attacking": {key: target for key in schema["legal_refs"]}})
            self.assertTrue(result.ok, result.summary)
        self.assertIn("C", session.state.combat.attackers.values())
        self.assertEqual(0, self.trigger_count(session, curse))
        self.assertFalse(self.gold(session))
        self.replay(session, load=True)

    def test_qualifying_creature_that_enters_attacking_can_keep_an_existing_result(self):
        from quorune.rules.casting import public_conditions

        session = self.session(50830121)
        curse = self.add(session.engine, "Curse of Opulence", zone="hand")
        direct, = self.ready_attackers(session, "B", 1)
        bounce = self.add(session.engine, "Unsummon", seat="C", zone="hand")
        self.add(session.engine, "Island", seat="C", ref="entry-response-island")
        self.place_curse(session, curse, "C")
        self.declare(session, "B", [(direct, "C")])
        self.cast(session, "C", bounce, [direct.ref])
        self.resolve_top(session)
        self.assertFalse(session.state.combat.attackers)
        # Canonical attacking-entry owner witness. The unrecorded setup mutation is checkpointed.
        session.engine.create_token("B", name="Late attacker", attacking="C", characteristics={
            "type_line": "Token Creature — Bear", "power": "2", "toughness": "2",
        })
        self.assertEqual(1, self.trigger_count(session, curse))
        self.checkpoint(session)
        with patch.object(public_conditions, "current_creature_attacking_controller", return_value=False):
            mutant = self.replay(session, load=True)
            self.assertIsNone(self.resolve(mutant))
            self.assertEqual(["A"], [card.controller for card in self.gold(mutant)])
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"A": 1, "B": 1}, Counter(card.controller for card in self.gold(session)))
        self.replay(session, load=True)
