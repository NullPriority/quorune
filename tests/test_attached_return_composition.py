"""Pinned attachment returns and land animation through shared rules owners."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_granted_skulk import record
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.compiler.static_land_animation import static_land_animation_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.reanimation_capability_shapes import attached_event_battlefield_return_runtime_dependencies
from scripts.build_test_database import build_fixture_database


class AttachedReturnCompilerTests(unittest.TestCase):
    def test_printed_attachment_returns_bind_the_departure_and_controller(self):
        registry = load_default_capability_registry()

        def compile_return(subject, body):
            source = replace(record(f"Enchant {subject}\n{body}", "Enchantment — Aura"), keywords=("Enchant",))
            ir = compile_oracle_card(source, capability_registry=registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            return next(node for node in ir.faces[0].nodes if node.kind == "triggered_ability")

        node = compile_return(
            "creature",
            "When enchanted creature dies, return that card to the battlefield under your control with a +1/+1 counter on it.",
        )
        effect, = node.effects
        self.assertEqual("$controller", effect["controller"])
        self.assertEqual("$context.card_zone_change_counter", effect["expected_zone_change_counter"])
        self.assertEqual("+1/+1", effect["entry_counter"])
        self.assertIsNone(node.target_schema)
        program = dict(effects=node.effects, active_zone=node.active_zone, event=node.event,
                       event_condition=node.event_condition, target_schema=node.target_schema)
        expected = {"zone.return.fixed_event_battlefield", "attachment.reference.current_or_lki",
                    "counter.producer.effect_entry", "zone.change.destination_replacement"}
        self.assertTrue(expected <= set(node.capability_dependencies))
        self.assertEqual(expected, set(attached_event_battlefield_return_runtime_dependencies(SimpleNamespace(**program))))
        for changed in ({"event": "creature.dies.self"}, {"event_condition": None},
                        {"effects": ({"op": "offer_optional_effect", "effects": [effect]},)}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                attached_event_battlefield_return_runtime_dependencies(SimpleNamespace(**{**program, **changed}))

        owner = compile_return(
            "creature", "When enchanted creature dies, return it to the battlefield tapped under its owner's control.",
        )
        self.assertEqual("$context.owner", owner.effects[0]["controller"])
        self.assertTrue(owner.effects[0]["tapped"])

        for subject, event in (("land", "dies"), ("artifact", "is put into a graveyard")):
            with self.subTest(subject=subject):
                node = compile_return(subject, f"When enchanted {subject} {event}, return that card to its owner's hand.")
                self.assertEqual("permanent.graveyard", node.event)
                self.assertIn({"field": "types", "op": "contains_any", "value": [subject]}, node.event_condition["all"])
                self.assertIn({"field": "attachments", "op": "contains_any", "value": ["$source.ref"]}, node.event_condition["all"])
                self.assertEqual("$context.card_zone_change_counter", node.effects[0]["expected_zone_change_counter"])

    def test_retaining_land_animation_uses_closed_layers_and_attached_subject(self):
        text = "Enchanted land is a 2/2 blue Elemental creature with flying. It's still a land."
        _, descriptor, capabilities = static_land_animation_handler(text)
        self.assertIn("continuous.characteristics.fixed_public_setting", capabilities)
        self.assertEqual("attached", descriptor["target"]["kind"])
        self.assertEqual(["land"], descriptor["target"]["predicate"]["types_all"])
        modifier = descriptor["modifier"]
        self.assertEqual((2, 2), (modifier["base_power"], modifier["base_toughness"]))
        self.assertEqual([{"op": "set_colors", "values": ["U"]}], modifier["color_operations"])
        self.assertEqual(["Flying"], modifier["add_abilities"])
        self.assertEqual([
            {"op": "add_types", "field": "card_types", "values": ["Creature"]},
            {"op": "add_types", "field": "subtypes", "values": ["Elemental"]},
        ], modifier["type_operations"])
        self.assertIsNone(static_land_animation_handler(text.replace("Elemental", "UnrepresentedSubtype")))
        self.assertIsNone(static_land_animation_handler(text.replace("flying", "rewrite history")))


class AttachedReturnNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attached-return-composition.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/attached-return-composition-cards.json",
            ROOT / "tests/fixtures/typed-token-ability-program-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Attached returns",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def test_original_unholy_indenture_uses_apnap_and_the_winning_trigger_controller(self):
        session = self.session(400712206)
        body = self.add(session.engine, "Grizzly Bears", seat="B")
        body.counters["+1/+1"] = 2
        first = self.add(session.engine, "Unholy Indenture", ref="active-player-aura")
        last = self.add(session.engine, "Unholy Indenture", seat="C", ref="later-player-aura")
        removal = self.add(session.engine, "Murder", zone="hand")
        private = self.add(session.engine, "Generic Bound Growth", seat="D", zone="hand", ref="hidden-return-witness")
        for aura in (first, last):
            self.assert_closed(session, aura)
            attach_objects(session.state.cards, aura, body, players=session.state.players,
                           source_timestamp=session.engine._next_zone_timestamp())
        self.assert_closed(session, removal)
        self.prepare(session, mana={"C": 1, "B": 2})
        self.checkpoint(session)
        counter_before = body.zone_change_counter
        self.cast(session, "A", removal, [body.ref])
        self.resolve_top(session)
        for card in (body, first, last):
            self.assertEqual("graveyard", session.state.cards[card.object_id].zone)
        triggers = [item for item in session.state.stack if item.kind == "triggered_ability"]
        self.assertEqual(["A", "C"], [item.controller for item in triggers])
        for item in triggers:
            self.assertEqual(body.ref, item.context["card"])
            self.assertEqual(counter_before + 1, item.context["card_zone_change_counter"])
            self.assertFalse(item.targets)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        returned = session.state.cards[body.object_id]
        self.assertEqual(("battlefield", "B", "C"), (returned.zone, returned.owner, returned.controller))
        self.assertEqual(counter_before + 2, returned.zone_change_counter)
        self.assertEqual(1, returned.counters["+1/+1"])
        self.replay(session, load=True)

    def test_original_wind_zendikon_retains_land_and_returns_it_after_simultaneous_destruction(self):
        session = self.session(400712207)
        aura = self.add(session.engine, "Wind Zendikon", zone="hand")
        land = self.add(session.engine, "Island", seat="B", ref="enchanted-island")
        land.counters["+1/+1"] = 1
        other_land = self.add(session.engine, "Island", seat="B", ref="other-island")
        other_creature = self.add(session.engine, "Grizzly Bears", seat="B")
        sweep = self.add(session.engine, "Akroma's Vengeance", zone="hand")
        for spell in (aura, sweep):
            self.assert_closed(session, spell)
        self.prepare(session, mana={"C": 4, "U": 1, "W": 2})
        self.checkpoint(session)
        self.cast_for(session, "A", aura, [land.ref], reserve={"C": 4, "W": 2})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(land.object_id, session.state.cards[aura.object_id].attached_to)
        data = session.engine._effective_card_data(session.state.cards[land.object_id])
        types, subtypes, _ = session.engine._type_parts(data["type_line"])
        self.assertEqual({"land", "creature"}, types)
        self.assertEqual({"island", "elemental"}, subtypes)
        self.assertEqual({"U"}, set(data["colors"]))
        self.assertEqual((3, 3), tuple(session.engine._numeric_stat(land.object_id, stat) for stat in ("power", "toughness")))
        self.assertIn("flying", session.engine._combat_keywords(session.state.cards[land.object_id]))
        self.assertEqual({"land"}, session.engine._type_parts(session.engine._effective_card_data(other_land)["type_line"])[0])

        self.cast(session, "A", sweep)
        self.resolve_top(session)
        for card in (aura, land, other_creature):
            self.assertEqual("graveyard", session.state.cards[card.object_id].zone)
        trigger, = (item for item in session.state.stack if item.kind == "triggered_ability")
        self.assertEqual(land.ref, trigger.context["card"])
        self.assertEqual(aura.object_id, trigger.source_object_id)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertIn(land.object_id, session.state.players["B"].zones["hand"])
        self.assertEqual("graveyard", session.state.cards[other_creature.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[other_land.object_id].zone)
        self.replay(session, load=True)
