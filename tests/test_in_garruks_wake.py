from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from quorune.carddb import CardDatabase
from quorune.compiler.destruction_templates import mass_destruction_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

SOURCE = "In Garruk's Wake"


class InGarruksWakeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "in-garruks-wake.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/in-garruks-wake-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Opponent creature and planeswalker union", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_actual_paid_wake_moves_one_opposing_union_with_replacement_and_replays(self):
        for index, redirected in enumerate((False, True)):
            with self.subTest(redirected=redirected):
                game = self.session(7314900 + index)
                host = game.engine
                spell = self.add(host, SOURCE, zone="hand", ref="wake-actual-cast")
                self.assert_closed(game, spell)
                opposing_walker = self.add(host, "Garruk Wildspeaker", seat="B", ref="wake-opposing-walker")
                own_walker = self.add(host, "Garruk Wildspeaker", ref="wake-own-walker")
                for walker in (opposing_walker, own_walker):
                    walker.counters["loyalty"] = 3
                opposing_creature = self.add(host, "Grizzly Bears", ref="wake-owned-opposing-creature")
                host.change_control(opposing_creature.object_id, "C", reason="fixture separate controller")
                borrowed = self.add(host, "Grizzly Bears", seat="D", ref="wake-borrowed-own-creature")
                host.change_control(borrowed.object_id, "A", reason="fixture separate owner")
                indestructible = self.add(host, "Darksteel Myr", seat="D", ref="wake-indestructible")
                other_type = self.add(host, "Sol Ring", seat="D", ref="wake-unaffected-artifact")
                if redirected:
                    self.assert_closed(game, self.add(host, "Rest in Peace", seat="D", ref="wake-replacement"))
                self.prepare(game, mana={"C": 7, "B": 2})
                self.checkpoint(game)
                self.cast_for(game, "A", spell)
                game = self.replay(game, load=True)
                with patch.object(game.engine, "_move_cards_simultaneously",
                                  wraps=game.engine._move_cards_simultaneously) as batch:
                    self.resolve_top(game)
                batch.assert_called_once()
                self.assertCountEqual(
                    [(opposing_creature.object_id, "graveyard"), (opposing_walker.object_id, "graveyard")],
                    batch.call_args.args[0],
                )
                destination = "exile" if redirected else "graveyard"
                self.assertEqual((destination, "A"), (game.state.cards[opposing_creature.object_id].zone,
                    game.state.cards[opposing_creature.object_id].owner))
                self.assertEqual((destination, "B"), (game.state.cards[opposing_walker.object_id].zone,
                    game.state.cards[opposing_walker.object_id].owner))
                self.assertEqual(destination, game.state.cards[spell.object_id].zone)
                for card in (own_walker, borrowed, indestructible, other_type):
                    self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
                self.replay(game, load=True)

    def test_printed_compound_uses_the_existing_closed_untargeted_union(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIsNone(node.target_schema)
        self.assertIn("permanent.destroy.fixed_set", node.capability_dependencies)
        effect, = node.effects
        self.assertEqual("destroy_all", effect["op"])
        shape = AffectedPermanentSetSpec.from_dict(effect["set"])
        self.assertEqual(PermanentControllerRelation.OPPONENTS, shape.controller_relation)
        self.assertEqual(("creature", "planeswalker"), shape.query.types_any)
        self.assertEqual((), shape.query.types_all)
        old_form = mass_destruction_effect_template("Destroy all creatures and planeswalkers you don't control.")
        self.assertEqual(old_form.effects, tuple(node.effects))
        self.assertIsNone(mass_destruction_effect_template(
            "Destroy all creatures you don't control and all planeswalkers you control.",
        ))
