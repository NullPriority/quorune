from dataclasses import replace
from pathlib import Path
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_sacrifice_model import (
    SOURCE_SACRIFICE_CAPABILITY,
    UNATTACHMENT_EVENT,
    UNATTACHMENT_SNAPSHOT,
    UNATTACHMENT_TRIGGER_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


WARGEAR = "Grafted Wargear"
EXOSKELETON = "Grafted Exoskeleton"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = FIXTURES / "source-unattachment-sacrifice-cards.json"


class SourceUnattachmentSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Disenchant"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda", "Grizzly Bears", "Plains"},
            "defending-player-target-cards.json": {"Cloudshift"},
            "targeted-graveyard-exile-cards.json": {"Shock"},
            "nonowner-creature-modifier-cards.json": {"Ensoul Artifact"},
            "attachment-source-return-cards.json": {"Planar Cleansing"},
            "fixed-source-sacrifice-cards.json": {"Straw Golem"},
        }
        fixtures = [ORIGINALS]
        for name, names in selected.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            fixture = folder / name
            fixture.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(fixture)
        path = folder / "unattachment-sacrifice.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Unattachment sacrifice", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_originals_keep_equip_characteristics_and_the_untargeted_trigger(self):
        for name in (WARGEAR, EXOSKELETON):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                profile = "commander_review"
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                entries = ir.faces[0].nodes
                self.assertTrue(any("attachment.equip.fixed_mana" in node.capability_dependencies for node in entries))
                trigger, = [node for node in entries if node.kind == "triggered_ability"]
                self.assertIsNone(trigger.target_schema)
                self.assertIn("trigger.placement.apnap", trigger.capability_dependencies)
                expected_keyword = name == EXOSKELETON
                self.assertEqual(expected_keyword, any("damage.result.infect" in node.capability_dependencies
                    for node in entries))
                ready, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
                program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                    if p.event == UNATTACHMENT_EVENT]
                effect, = program.effects
                self.assertEqual("$context." + UNATTACHMENT_SNAPSHOT, effect[UNATTACHMENT_SNAPSHOT])
                for changed in (
                    replace(program, event="permanent.leave"),
                    replace(program, event_condition=None),
                    replace(program, effects=[{**effect, UNATTACHMENT_SNAPSHOT: {"schema_version": 1}}]),
                    replace(program, effects=[{**effect, UNATTACHMENT_SNAPSHOT: "$source"}]),
                ):
                    bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=profile)
                    self.assertFalse(bound["strict"], bound)
                for candidate in (
                    card.oracle_text.replace("unattached", "attached"),
                    card.oracle_text.replace("that permanent", "this Equipment"),
                    card.oracle_text.replace("sacrifice that permanent", "you may sacrifice that permanent"),
                ):
                    altered = compile_oracle_card(replace(card, oracle_text=candidate),
                        capability_registry=self.registry, capability_profile=profile)
                    self.assertNotEqual("exact", altered.status)

    def equip_once(self, game, equipment, target):
        self.priority(game, "A")
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in legal if row["id"].startswith("activate:" + equipment.ref + ":")]
        admitted = game.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(admitted.ok, admitted.summary)
        self.resolve_top(game)

    def only_unattachment_trigger(self, game, source):
        self.assertEqual(1, len(game.state.stack), "One broken attachment must produce one trigger")
        trigger, = game.state.stack
        self.assertEqual("triggered_ability", trigger.kind)
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.assertEqual("A", trigger.controller)
        self.assertEqual([], trigger.targets)
        return trigger

    def test_actual_free_reequip_sacrifices_the_former_permanent_once(self):
        game = self.session(70130310)
        source = self.add(game.engine, WARGEAR, zone="hand")
        former = self.add(game.engine, "Grizzly Bears", ref="unattachment-former")
        current = self.add(game.engine, "Grizzly Bears", ref="unattachment-current")
        hidden = self.add(game.engine, "Shock", seat="D", zone="hand", ref="unattachment-private")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.equip_once(game, source, former)
        self.assertFalse(game.state.stack)
        self.equip_once(game, source, former)
        self.assertFalse(game.state.stack)
        self.equip_once(game, source, current)
        self.only_unattachment_trigger(game, source)
        self.assertEqual(current.object_id, game.state.cards[source.object_id].attached_to)
        self.assertEqual("battlefield", game.state.cards[former.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[former.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[current.object_id].zone)
        self.assertEqual((5, 4), tuple(game.engine._numeric_stat(current.object_id, field)
            for field in ("power", "toughness")))
        for seat in "ABC":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_destroyed_equipment_uses_its_old_ability_and_controller(self):
        game = self.session(70130311)
        source = self.add(game.engine, EXOSKELETON, zone="hand")
        former = self.add(game.engine, "Grizzly Bears")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B", ref="unattachment-payer-white")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="unattachment-payer-generic")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 6})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.equip_once(game, source, former)
        self.assertIn("infect", game.engine._combat_keywords(game.state.cards[former.object_id]))
        old_source = game.state.cards[source.object_id].logical_object_id
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotEqual(old_source, game.state.cards[source.object_id].logical_object_id)
        self.only_unattachment_trigger(game, source)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[former.object_id].zone)
        self.replay(game, load=True)

    def test_actual_blink_reentry_does_not_replace_the_former_incarnation(self):
        game = self.session(70130312)
        source = self.add(game.engine, WARGEAR, zone="hand")
        former = self.add(game.engine, "Grizzly Bears")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Plains")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.equip_once(game, source, former)
        old_identity = game.state.cards[former.object_id].logical_object_id
        self.cast(game, "A", blink, [former.ref])
        self.resolve_top(game)
        self.only_unattachment_trigger(game, source)
        self.assertNotEqual(old_identity, game.state.cards[former.object_id].logical_object_id)
        self.assertIsNone(game.state.cards[source.object_id].attached_to)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[former.object_id].zone)
        self.replay(game, load=True)

    def seed_relation(self, game, equipment, target):
        attach_objects(game.state.cards, equipment, target, source_timestamp=game.engine._next_zone_timestamp(),
            players=game.state.players)
        self.assert_closed(game, equipment)

    def test_actual_ensoul_breaks_the_attachment_during_state_based_checks(self):
        game = self.session(70130313)
        source = self.add(game.engine, WARGEAR)
        former = self.add(game.engine, "Grizzly Bears")
        animation = self.add(game.engine, "Ensoul Artifact", zone="hand")
        self.seed_relation(game, source, former)
        self.assert_closed(game, animation)
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", animation, [source.ref])
        self.resolve_top(game)
        self.assertIsNone(game.state.cards[source.object_id].attached_to)
        self.assertEqual("battlefield", game.state.cards[former.object_id].zone)
        self.only_unattachment_trigger(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[former.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_simultaneous_departure_records_the_link_once(self):
        game = self.session(70130314)
        source = self.add(game.engine, EXOSKELETON)
        former = self.add(game.engine, "Grizzly Bears")
        wipe = self.add(game.engine, "Planar Cleansing", zone="hand")
        self.seed_relation(game, source, former)
        self.assert_closed(game, wipe)
        self.prepare(game, mana={"C": 3, "W": 3})
        self.checkpoint(game)
        self.cast(game, "A", wipe)
        self.resolve_top(game)
        for card in (source, former):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.only_unattachment_trigger(game, source)
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_foreign_or_departing_former_permanent_cannot_be_sacrificed(self):
        for leave in (False, True):
            with self.subTest(leave=leave):
                game = self.session(70130315 + leave)
                source = self.add(game.engine, WARGEAR)
                foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="unattachment-foreign")
                new = self.add(game.engine, "Grizzly Bears", ref="unattachment-legal-receiver")
                self.seed_relation(game, source, foreign)
                self.prepare(game)
                self.checkpoint(game)
                if leave:
                    self.priority(game, "B")
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertFalse(game.state.players["B"].in_game)
                    self.assertIsNone(game.state.cards[source.object_id].attached_to)
                else:
                    self.equip_once(game, source, new)
                self.only_unattachment_trigger(game, source)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("outside" if leave else "battlefield", game.state.cards[foreign.object_id].zone)
                self.replay(game, load=True)

    def test_omitting_shared_dispatch_breaks_the_real_reequip_witness(self):
        with patch("quorune.unattachment_triggers.dispatch_unattachment", return_value=None):
            with self.assertRaisesRegex(AssertionError, "One broken attachment must produce one trigger"):
                self.test_actual_free_reequip_sacrifices_the_former_permanent_once()

    def test_verified_withdrawals_distinguish_unattachment_from_ordinary_source_sacrifice(self):
        from copy import deepcopy

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        old = "Straw Golem"
        originals = {WARGEAR, EXOSKELETON}
        names = (WARGEAR, EXOSKELETON, old, "Grizzly Bears")
        baseline = verified(deepcopy(source))
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        for capability, blocked in (
            (UNATTACHMENT_TRIGGER_CAPABILITY, originals),
            (SOURCE_SACRIFICE_CAPABILITY, originals | {old}),
        ):
            with self.subTest(capability=capability):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Unattachment or fixed sacrifice withdrawn"])
                registry = verified(rows)
                closure = registry.closure((capability,), profile="commander_review")
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready["strict_capability_ready"],
                        (name, ready["blockers"]))
