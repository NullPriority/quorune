"""Two original legal creature targets and one committed noncombat excess batch."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.creature_power_damage_model import TRAMPLE_BITE_CAPABILITY, split_trample_bite_damage
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.creature_power_damage_capability_shapes import trample_bite_target_schema
from scripts.build_test_database import build_fixture_database


CARD = "Ram Through"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TrampleBiteTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "trample-bite.sqlite3"
        build_fixture_database([FIXTURES / name for name in (
            "current-target-replacement-cards.json", "creature-power-departure-cards.json",
            "compiled-reviewed-spell-cards.json", "counter-prohibition-cards.json",
            "player-target-protection-cards.json", "activation-result-views-cards.json",
            "bound-effect-program-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Trample excess damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_two_target_binding_and_leaf_rejection(self):
        from quorune.compiler.target_replacements import current_target_replacement_template
        from quorune.oracle_ir import _reviewed_atomic_effect_template

        card = self.db.lookup(CARD, fuzzy=False)
        record = replace(query_record(card.oracle_text), type_line="Instant")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertIsNone(current_target_replacement_template(card.oracle_text,
            compile_component=lambda text: _reviewed_atomic_effect_template(text, card_name=record.name)))
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
        self.assertEqual(trample_bite_target_schema(), node.target_schema)
        self.assertIn(TRAMPLE_BITE_CAPABILITY, node.capability_dependencies)
        for old, new in (
            ("target creature you don't control", "target creature you control"),
            ("has trample", "has first strike"),
            ("that creature's controller", "any target"),
        ):
            with self.subTest(new=new):
                mutant = replace(record, oracle_text=record.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(mutant, capability_registry=self.registry,
                                                                capability_profile=PROFILE).status)
        bound, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        deps = [cap for cap in program.capability_dependencies if cap != TRAMPLE_BITE_CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])
        groups = [dict(group) for group in program.target_schema["groups"]]
        groups[1]["controller_relation"] = "any"
        changed = replace(program, target_schema={"groups": groups})
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])

    def test_excess_uses_marked_damage_deathtouch_and_all_permanent_domains(self):
        cases = (
            (dict(amount=6, toughness=4, marked=1, deathtouch=False), (3, 3)),
            (dict(amount=6, toughness=4, marked=1, deathtouch=True), (1, 5)),
            (dict(amount=6, toughness=4, marked=4, deathtouch=False), (0, 6)),
            (dict(amount=6, toughness=4, marked=1, deathtouch=False, loyalty=2, defense=1), (1, 5)),
            (dict(amount=2, toughness=4, marked=0, deathtouch=False), (2, 0)),
            (dict(amount=0, toughness=4, marked=0, deathtouch=True), (0, 0)),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(expected, split_trample_bite_damage(**arguments))
        with self.assertRaises(ValueError):
            split_trample_bite_damage(6, toughness=4, marked=False, deathtouch=False)

    def setup_spell(self, game, spell, dealer):
        for card in (spell, dealer):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 1})
        self.checkpoint(game)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def dealt(self, game):
        events = [event for event in game.state.events if event.code == "effect.creature_power_damage"]
        return [value for value in events[-1].details["dealt_amounts"] if value] if events else []

    def test_native_marked_indestructible_and_shield_do_not_change_overflow(self):
        game = self.session(12040101)
        spell = self.add(game.engine, CARD, zone="hand")
        dealer = self.add(game.engine, "Colossal Dreadmaw")
        target = self.add(game.engine, "Darksteel Myr", seat="B")
        aegis = self.add(game.engine, "Aegis of the Gods", seat="B")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="excess-private")
        target.counters.update({"+1/+1": 2, "shield": 1})
        target.marked_damage = 1
        self.assert_closed(game, aegis)
        self.setup_spell(game, spell, dealer)
        command = {"action_id": "cast:" + spell.ref, "targets": [target.ref, dealer.ref], "pay": "auto"}
        self.reject(game, "A", command)
        self.reject(game, "A", {**command, "targets": [dealer.ref, "B"]})
        earlier = {seat: game.state.players[seat].life for seat in "ABCD"}
        self.cast(game, "A", spell, [dealer.ref, target.ref])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({**earlier, "B": earlier["B"] - 4},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        current = game.state.cards[target.object_id]
        self.assertEqual(("battlefield", 1, {"+1/+1": 2}), (current.zone, current.marked_damage, current.counters))
        self.assertEqual([4], self.dealt(game))
        self.replay(game, load=True)

    def test_native_deathtouch_and_lifelink_follow_the_actual_noncombat_source(self):
        game = self.session(12040102)
        spell = self.add(game.engine, CARD, zone="hand")
        dealer = self.add(game.engine, "Colossal Dreadmaw", ref="deathtouch-dealer")
        target = self.add(game.engine, "Colossal Dreadmaw", seat="B", ref="deathtouch-receiver")
        collar = self.add(game.engine, "Basilisk Collar")
        attach_objects(game.state.cards, collar, dealer, source_timestamp=collar.zone_timestamp,
                       players=game.state.players)
        self.assertLessEqual({"Lifelink", "Deathtouch"}, set(game.engine._effective_card_data(dealer)["keywords"]))
        self.setup_spell(game, spell, dealer)
        life_a, life_b = (game.state.players[seat].life for seat in "AB")
        self.cast(game, "A", spell, [dealer.ref, target.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((life_a + 6, life_b - 5), tuple(game.state.players[seat].life for seat in "AB"))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertEqual([1, 5], self.dealt(game))
        self.assertEqual(0, game.state.cards[dealer.object_id].marked_damage)
        self.replay(game, load=True)

    def test_native_loss_of_either_target_cancels_both_and_no_trample_keeps_all_damage(self):
        for lost in ("dealer", "recipient", None):
            with self.subTest(lost=lost):
                game = self.session(12040103 + (0 if lost == "dealer" else 1 if lost == "recipient" else 2))
                spell = self.add(game.engine, CARD, zone="hand")
                dealer = self.add(game.engine, "Colossal Dreadmaw" if lost else "Grizzly Bears")
                target = self.add(game.engine, "Darksteel Myr", seat="B")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.add(game.engine, "Island", seat="B")
                self.assert_closed(game, bounce)
                self.setup_spell(game, spell, dealer)
                life = game.state.players["B"].life
                self.cast(game, "A", spell, [dealer.ref, target.ref])
                if lost:
                    self.cast(game, "B", bounce, [dealer.ref if lost == "dealer" else target.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(life, game.state.players["B"].life)
                self.assertEqual([] if lost else [2], self.dealt(game))
                self.assertEqual(0 if lost else 2, game.state.cards[target.object_id].marked_damage)
                self.replay(game, load=True)
