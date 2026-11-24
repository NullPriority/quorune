"""Native announced-X reanimation with an exact own-graveyard target."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.announced_mana_value_target import OWN_GRAVEYARD_MANA_VALUE_CAPABILITY
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


PROFILE = "commander_review"


class OwnGraveyardManaValueTests(unittest.TestCase):
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
    spell = "Ember Recall"
    eligible = "Rill Courier"
    zero = "Rill Lurker"
    expensive = "Rill Keeper"
    artifact = "Borrowed Hook"

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "graveyard-x.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json",
            "fixed-stack-spell-copy-cards.json",
            "own-graveyard-mana-value-target-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Own graveyard X", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def offer(self, session, spell):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"] == "cast:" + spell.ref)

    def test_announced_reanimation_quotes_only_owned_creatures_and_replays(self):
        session = self.session(601_401)
        spell = self.add(session.engine, self.spell, zone="hand")
        eligible = self.add(session.engine, self.eligible, zone="graveyard", ref="grave-x-eligible")
        free = self.add(session.engine, self.zero, zone="graveyard", ref="grave-x-zero")
        expensive = self.add(session.engine, self.expensive, zone="graveyard", ref="grave-x-pricey")
        artifact = self.add(session.engine, self.artifact, zone="graveyard", ref="grave-x-artifact")
        foreign = self.add(session.engine, self.eligible, seat="B", zone="graveyard", ref="grave-x-foreign")
        hidden = self.add(session.engine, self.eligible, seat="C", zone="hand", ref="grave-x-hidden")
        self.prepare(session, mana={"B": 1, "C": 2})
        self.checkpoint(session)
        binding = self.binding(session, spell)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        action = self.offer(session, spell)
        bounds = action["target_schema"]["x_bounds_by_ref"]
        self.assertEqual({eligible.ref: {"minimum": 2, "maximum": 2},
                          free.ref: {"minimum": 0, "maximum": 2}}, bounds)
        self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:A", full=True)))
        before = authoritative_state_hash(session.state)
        for target in (eligible, expensive, artifact, foreign, hidden):
            x = 1 if target is eligible else 2
            denied = session.act("pilot:A", {"action_id": action["id"], "x": x,
                                            "targets": [target.ref], "pay": "auto"})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        paid = session.act("pilot:A", {"action_id": action["id"], "x": 2,
            "targets": [eligible.ref], "pay": "manual", "payment": {"B": 1, "C": 2}})
        self.assertTrue(paid.ok, paid.summary)
        item = session.state.stack[-1]
        self.assertEqual(2, item.x_value)
        self.assertEqual(2, item.context["target_schema_override"]["mana_value_max"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[eligible.object_id]
        self.assertEqual(("battlefield", "A", False), (current.zone, current.controller, current.tapped))
        self.assertTrue(all(session.state.cards[c.object_id].zone == "graveyard"
                            for c in (free, expensive, artifact, foreign)))
        self.replay(session, load=True)

    def test_zero_x_returns_a_zero_mana_value_creature(self):
        session = self.session(601_403)
        spell = self.add(session.engine, self.spell, zone="hand")
        free = self.add(session.engine, self.zero, zone="graveyard", ref="return-zero")
        costly = self.add(session.engine, self.eligible, zone="graveyard", ref="return-nonzero")
        self.prepare(session, mana={"B": 1})
        self.checkpoint(session)
        offered = self.offer(session, spell)
        self.assertEqual({free.ref: {"minimum": 0, "maximum": 0}},
                         offered["target_schema"]["x_bounds_by_ref"])
        paid = session.act("pilot:A", {"action_id": offered["id"], "x": 0,
                                        "targets": [free.ref], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(0, session.state.stack[-1].context["target_schema_override"]["mana_value_max"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[free.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[costly.object_id].zone)
        self.replay(session, load=True)

    def test_reanimation_copy_keeps_announced_x_and_owned_graveyard_filter(self):
        session = self.session(601_402)
        spell = self.add(session.engine, self.spell, zone="hand")
        echo = self.add(session.engine, "Reverberate", zone="hand")
        first = self.add(session.engine, self.eligible, zone="graveyard", ref="copy-grave-first")
        second = self.add(session.engine, self.eligible, zone="graveyard", ref="copy-grave-second")
        expensive = self.add(session.engine, self.expensive, zone="graveyard", ref="copy-grave-too-much")
        foreign = self.add(session.engine, self.eligible, seat="B", zone="graveyard", ref="copy-grave-foreign")
        self.prepare(session, mana={"B": 1, "C": 2, "R": 2})
        self.checkpoint(session)
        sent = session.act("pilot:A", {"action_id": self.offer(session, spell)["id"], "x": 2,
                "targets": [first.ref], "pay": "manual", "payment": {"B": 1, "C": 2}})
        self.assertTrue(sent.ok, sent.summary)
        original = session.state.stack[-1]
        self.cast(session, "A", echo, [original.ref], {"R": 2})
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        before = authoritative_state_hash(session.state)
        for invalid in (expensive, foreign):
            denied = session.act("pilot:A", {"action_id": "choose", "targets": [invalid.ref]})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        chosen = session.act("pilot:A", {"action_id": "choose", "targets": [second.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        copied, = (item for item in session.state.stack if item.kind == "spell_copy")
        self.assertEqual(2, copied.x_value)
        self.assertEqual("you", copied.context["target_schema_override"]["owner_relation"])
        self.assertEqual(2, copied.context["target_schema_override"]["mana_value_max"])
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone == "battlefield" for c in (first, second)))
        self.assertTrue(all(session.state.cards[c.object_id].zone == "graveyard" for c in (expensive, foreign)))
        self.replay(session, load=True)

    def test_owned_graveyard_mana_value_authority_withdraws_only_its_scope(self):
        card = self.db.lookup(self.spell)
        exact = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        node, = (n for face in exact.faces for n in face.nodes if n.event == "resolve")
        self.assertIn(OWN_GRAVEYARD_MANA_VALUE_CAPABILITY, node.capability_dependencies)
        self.assertIn("zone.reanimate.fixed_target", node.capability_dependencies)

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(r for r in raw["capabilities"] if r["id"] == OWN_GRAVEYARD_MANA_VALUE_CAPABILITY)
        row.update(status="blocked", blockers=["Owned graveyard bound withdrawn."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                                                         capability_profile=PROFILE).status)
        from quorune.compiler.announced_mana_value_target import announced_mana_value_target_template
        for clause in (card.oracle_text.replace("your graveyard", "an opponent's graveyard"),
                       card.oracle_text.replace("X or less", "less than X"),
                       card.oracle_text.replace("creature card", "artifact card")):
            self.assertIsNone(announced_mana_value_target_template(clause, has_cast_x=True))
        with patch("quorune.compiler.announced_mana_value_target.announced_mana_value_target_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry,
                                                             capability_profile=PROFILE).status)
