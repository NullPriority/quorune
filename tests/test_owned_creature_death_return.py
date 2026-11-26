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
from quorune.rules.graveyard_self_return import GRAVEYARD_SELF_ENTRY_CAPABILITY, owned_other_creature_death_condition
from scripts.build_test_database import build_fixture_database


SOURCE = "Nether Traitor"
CAP = GRAVEYARD_SELF_ENTRY_CAPABILITY


class OwnedCreatureDeathReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "owned-death-return.sqlite3"
        fixtures = ("bound-effect-program-cards", "triggered-graveyard-battlefield-cards", "activation-public-state-cards")
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid graveyard return", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_owner_not_controller_may_pay_once_or_decline_and_replays(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                game = self.session(603928 + pay)
                source = self.add(game.engine, SOURCE, zone="graveyard")
                owned = self.add(game.engine, "Grizzly Bears", ref="owned-borrowed-creature")
                game.engine.change_control(owned.object_id, "B", reason="Borrowed creature")
                foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-owned-creature")
                removal = self.add(game.engine, "Murder", zone="hand", ref="owner-removal")
                foreign_removal = self.add(game.engine, "Murder", zone="hand", ref="foreign-removal")
                self.assert_closed(game, source)
                self.prepare(game, mana={"C":2, "B":7})
                self.checkpoint(game)
                self.cast(game, "A", foreign_removal, [foreign.ref])
                self.resolve_top(game)
                self.assertFalse(game.state.stack)
                self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
                original = source.logical_object_id
                self.cast(game, "A", removal, [owned.ref])
                self.resolve_top(game)
                (pending,) = game.state.stack
                self.assertEqual("A", pending.controller)
                self.assertEqual(original, pending.context["source_logical_object_id"])
                self.assertEqual(("A", "B"), (pending.context["owner"], pending.context["previous_controller"]))
                decision = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
                game = self.replay(game, load=True)
                command = {"action_id":"choose", "pay":pay}
                self.rejected(game, "B", command)
                amount = game.state.players["A"].mana_pool["B"]
                self.assertGreaterEqual(amount, 1)
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[source.object_id]
                self.assertEqual("battlefield" if pay else "graveyard", current.zone)
                self.assertEqual(amount-int(pay), game.state.players["A"].mana_pool["B"])
                if pay:
                    self.assertNotEqual(original, current.logical_object_id)
                    self.assertFalse(current.tapped)
                self.rejected(game, "A", command)
                self.replay(game, load=True)

    def test_actual_simultaneous_death_does_not_discover_a_new_graveyard_source(self):
        game = self.session(603940)
        source = self.add(game.engine, SOURCE)
        owned = self.add(game.engine, "Grizzly Bears")
        sweeper = self.add(game.engine, "Wrath of God", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, sweeper)
        self.prepare(game, mana={"C":2, "W":2, "B":1})
        self.checkpoint(game)
        self.cast(game, "A", sweeper)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"graveyard"}, {game.state.cards[card.object_id].zone for card in (source, owned)})
        self.assertFalse(game.state.stack)
        self.assertEqual(1, game.state.players["A"].mana_pool["B"])
        self.replay(game, load=True)

    def test_original_requires_the_exact_owner_death_and_paid_return(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        (node,) = (node for face in ir.faces for node in face.nodes if CAP in node.capability_dependencies)
        self.assertEqual(("creature.dies", "graveyard"), (node.event, node.active_zone))
        self.assertEqual(owned_other_creature_death_condition(), node.event_condition)
        for before, after in (("your graveyard from the battlefield", "an opponent's graveyard from the battlefield"),
                              ("pay {B}", "pay {R}")):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)
        game = self.session(603949)
        source = self.add(game.engine, SOURCE, zone="graveyard")
        (program,) = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                      if CAP in row.capability_dependencies)
        forged = replace(program, event_condition={"field":"owner","op":"eq","value":"$source.owner"})
        self.assertFalse(bind_semantic_program_runtime(forged, capability_registry=self.registry,
                                                       profile="commander_review")["strict"])

    def test_omitting_the_original_source_exclusion_is_killed(self):
        owner_only = {"field":"owner","op":"eq","value":"$source.owner"}
        with patch("quorune.rules.graveyard_self_return.owned_other_creature_death_condition", return_value=owner_only):
            with self.assertRaises((AssertionError, ValueError)):
                self.test_original_requires_the_exact_owner_death_and_paid_return()
