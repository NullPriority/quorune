from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CombatState, GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.attacking_entry_copy import CAPABILITY, ENTRY_FACT
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AttackingEntryCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "attacking-entry-copy.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/attacking-entry-copy-cards.json",
        ], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attacking entry copy", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def setup_combat(self, seed):
        game = self.session(seed)
        shadow = self.add(game.engine, "Thousand-Faced Shadow", zone="hand")
        returned = self.add(game.engine, "Generic Bound Body", ref="returned-attacker")
        model = self.add(game.engine, "Grizzly Bears", ref="copy-attacker")
        nonattacker = self.add(game.engine, "Grizzly Bears", ref="not-an-attacker")
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.assert_closed(game, shadow)
        for card in (returned, model):
            card.attacking = "B"
        model.counters["+1/+1"] = 1
        game.state.players["A"].mana_pool.update({"C": 2, "U": 2})
        game.state.players["B"].mana_pool["U"] = 1
        game.state.active_player = "A"
        game.state.started = True
        game.state.phase = "combat"
        game.state.step = "declare_blockers"
        game.state.combat = CombatState(
            attackers_declared=True, blockers_declared=True, had_attacking_creature=True,
            attackers={card.object_id: "B" for card in (returned, model)},
            attack_target_context={card.object_id: {"target":"B", "kind":"player", "defending_player":"B"}
                                   for card in (returned, model)},
            defending_players=["B"],
        )
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)
        return game, shadow, returned, model, nonattacker, response

    def trigger_copy(self, game, shadow, returned, model, nonattacker):
        packet = game.packet("pilot:A", full=True)
        action, = (row for row in packet["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith(f"activate:{shadow.ref}:"))
        self.assertIn(returned.ref, action["cost_summary"]["choose_cost"][0]["legal_refs"])
        self.assertNotIn(shadow.ref, json.dumps(game.packet("pilot:B", full=True)))
        accepted = game.act("pilot:A", {"action_id": action["id"], "cost_objects": [returned.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("hand", game.state.cards[returned.object_id].zone)
        self.resolve_top(game)
        source = game.state.cards[shadow.object_id]
        self.assertEqual(("battlefield", "B", True), (source.zone, source.attacking, source.tapped))
        choice = game.state.pending_decision
        self.assertEqual("semantic.target", choice.kind)
        before = authoritative_state_hash(game.state)
        for wrong in (source, nonattacker):
            result = game.act("pilot:A", {"action_id": "choose", "targets": [wrong.ref]})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "targets": [model.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIs(game.state.stack[-1].context[ENTRY_FACT], True)

    def test_actual_ninjutsu_offer_and_other_attacker_copy_enters_tapped_against_chosen_seat_and_replays(self):
        game, shadow, returned, model, nonattacker, _ = self.setup_combat(508425)
        self.trigger_copy(game, shadow, returned, model, nonattacker)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "attacking": {"attacking:0": "C"}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        token, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertEqual(("C", True, "Grizzly Bears"), (token.attacking, token.tapped, game.engine.display_name(token.object_id)))
        self.assertEqual({}, token.counters)
        self.assertEqual(2, game.engine._numeric_stat(token.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_intervening_condition_and_target_are_rechecked_after_a_response(self):
        for remove_source in (True, False):
            with self.subTest(remove_source=remove_source):
                game, shadow, returned, model, nonattacker, bounce = self.setup_combat(508426 + remove_source)
                self.trigger_copy(game, shadow, returned, model, nonattacker)
                target = shadow if remove_source else model
                self.cast(game, "B", bounce, [target.ref])
                self.resolve_top(game)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual("hand", game.state.cards[target.object_id].zone)
                self.assertFalse(any(card.is_token for card in game.state.cards.values()))
                self.replay(game, load=True)

    def test_printed_contract_and_legacy_publication_shape(self):
        card = self.db.lookup("Thousand-Faced Shadow", fuzzy=False)
        result = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertTrue(any(CAPABILITY in node.capability_dependencies for face in result.faces for node in face.nodes))
        for material in ("from your graveyard", "from your hand"):
            if material == "from your hand":
                changed = replace(card, oracle_text=card.oracle_text.replace("another target attacking creature", "target attacking creature"))
            else:
                changed = replace(card, oracle_text=card.oracle_text.replace("enters from your hand", "enters from your graveyard"))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)
        game = self.session(508427)
        archived = game.state.to_dict()
        archived.pop("attacking_entry_publication_version")
        self.assertEqual(archived, GameState.from_dict(archived).to_dict())

    def test_omitting_the_attacking_copy_binding_is_killed(self):
        with patch("quorune.compiler.attacking_token_results.attacking_entry_copy_binding", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_printed_contract_and_legacy_publication_shape()
