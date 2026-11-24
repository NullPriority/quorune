"""Native announced-X return with a typed sealed set and terminal exile."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
import test_qualified_hexproof as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import ANNOUNCED_GRAVEYARD_RETURN_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class AnnouncedGraveyardReturnTests(unittest.TestCase):
    witness = native.QualifiedHexproofTests
    register_fixture_cards_on_add = True
    session = witness.session
    add = witness.add
    prepare = witness.prepare
    checkpoint = witness.checkpoint
    binding = witness.binding
    resolve = witness.resolve
    replay = witness.replay
    priority = witness.priority
    cast = witness.cast
    spell = "Warproot Return"
    zero = "Ash Seed"
    low = "Ash Runner"
    planeswalker = "Ash Keeper"
    high = "Ash Tower"
    above = "Ash Citadel"
    artifact = "Ash Loop"
    high_value = 21
    above_value = 24

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "announced-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json",
            "fixed-stack-spell-copy-cards.json",
            "announced-owned-graveyard-return-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Mass reanimation X", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def offer(self, session, spell):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"] == "cast:" + spell.ref)

    def test_declared_return_set_keeps_zero_large_x_ownership_and_terminal_exile(self):
        for x in (0, 3, 22):
            with self.subTest(x=x):
                session = self.session(601_410 + x)
                spell = self.add(session.engine, self.spell, zone="hand")
                zero = self.add(session.engine, self.zero, zone="graveyard", ref="mass-return-zero")
                low = self.add(session.engine, self.low, zone="graveyard", ref="mass-return-low")
                walker = self.add(session.engine, self.planeswalker, zone="graveyard", ref="mass-return-walker")
                high = self.add(session.engine, self.high, zone="graveyard", ref="mass-return-high")
                above = self.add(session.engine, self.above, zone="graveyard", ref="mass-return-above")
                inert = self.add(session.engine, self.artifact, zone="graveyard", ref="mass-return-inert")
                foreign = self.add(session.engine, self.low, seat="B", zone="graveyard", ref="mass-return-foreign")
                hidden = self.add(session.engine, self.low, seat="D", zone="hand", ref="mass-return-hidden")
                payment = {"W": 3, **({"C": x} if x else {})}
                self.prepare(session, mana=payment)
                self.checkpoint(session)
                bound = self.binding(session, spell)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
                action = self.offer(session, spell)
                self.assertFalse(action.get("target_schema"))
                self.assertEqual(x, action["cost_options"][0]["choice_schema"]["x"]["maximum"])
                self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:A", full=True)))
                before = authoritative_state_hash(session.state)
                denied = session.act("pilot:A", {"action_id": action["id"], "x": x + 1, "pay": "auto"})
                self.assertFalse(denied.ok, denied.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act("pilot:A", {"action_id": action["id"], "x": x,
                                                    "pay": "manual", "payment": payment})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual(x, session.state.stack[-1].x_value)
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                for card, value in ((zero, 0), (low, 2), (walker, 3), (high, self.high_value), (above, self.above_value)):
                    current = session.state.cards[card.object_id]
                    self.assertEqual("battlefield" if value <= x else "graveyard", current.zone)
                    if value <= x:
                        self.assertEqual("A", current.controller)
                        self.assertFalse(current.tapped)
                self.assertEqual("exile", session.state.cards[spell.object_id].zone)
                self.assertTrue(all(session.state.cards[c.object_id].zone == "graveyard" for c in (inert, foreign)))
                self.replay(session, load=True)

    def test_copied_return_uses_its_controller_and_the_copied_x(self):
        session = self.session(601_435)
        spell = self.add(session.engine, self.spell, zone="hand")
        echo = self.add(session.engine, "Reverberate", seat="B", zone="hand")
        a = self.add(session.engine, self.low, zone="graveyard", ref="mass-copy-a")
        b = self.add(session.engine, self.low, seat="B", zone="graveyard", ref="mass-copy-b")
        high = self.add(session.engine, self.high, seat="B", zone="graveyard", ref="mass-copy-high")
        session.state.players["B"].mana_pool["R"] = 2
        self.prepare(session, mana={"W": 3, "C": 2})
        self.checkpoint(session)
        sent = session.act("pilot:A", {"action_id": self.offer(session, spell)["id"], "x": 2,
            "pay": "manual", "payment": {"W": 3, "C": 2}})
        self.assertTrue(sent.ok, sent.summary)
        original = session.state.stack[-1]
        self.cast(session, "B", echo, [original.ref], {"R": 2})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(("battlefield", "A"), (session.state.cards[a.object_id].zone, session.state.cards[a.object_id].controller))
        self.assertEqual(("battlefield", "B"), (session.state.cards[b.object_id].zone, session.state.cards[b.object_id].controller))
        self.assertEqual("graveyard", session.state.cards[high.object_id].zone)
        self.assertEqual("exile", session.state.cards[spell.object_id].zone)
        self.replay(session, load=True)

    def test_declared_return_set_withdraws_with_its_paired_authority(self):
        source = self.db.lookup(self.spell)
        exact = compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", exact.status, exact.material_residuals)
        node, = (n for face in exact.faces for n in face.nodes if n.event == "resolve")
        self.assertIn(ANNOUNCED_GRAVEYARD_RETURN_CAPABILITY, node.capability_dependencies)
        self.assertIn("zone.exile.resolving_spell", node.capability_dependencies)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(r for r in raw["capabilities"] if r["id"] == ANNOUNCED_GRAVEYARD_RETURN_CAPABILITY)
        row.update(status="blocked", blockers=["Announced graveyard return withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual("exact", compile_oracle_card(source, capability_registry=withdrawn,
                                                         capability_profile="commander_review").status)
        with patch("quorune.compiler.declared_effect_amounts._announced_graveyard_set_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(source, capability_registry=self.registry,
                                                             capability_profile="commander_review").status)
