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
            "artifact-graveyard-departure-cards.json",
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

    def test_artifact_departure_binding_rejects_changed_grammar_and_missing_facts(self):
        from dataclasses import replace
        from types import SimpleNamespace

        from quorune.rules.artifact_graveyard_departure import (
            CAPABILITY, EVENT, MECHANIC, bind_departure_artifact_return_target,
            departure_artifact_return_condition, departure_artifact_return_result,
            departure_artifact_return_runtime_dependencies,
        )
        from test_fixed_extra_turns import turn_record

        clause = self.db.lookup("Scrap Trawler").oracle_text
        record = turn_record(clause, type_line="Artifact Creature — Construct")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(EVENT, node.event)
        self.assertIn(CAPABILITY, node.capability_dependencies)
        self.assertEqual(departure_artifact_return_condition(), node.event_condition)
        self.assertEqual(departure_artifact_return_result()[2], node.target_schema)
        for changed in (
            clause.replace("another artifact you control", "another artifact"),
            clause.replace("lesser mana value", "equal or lesser mana value"),
            clause.replace("target artifact card", "target permanent card"),
        ):
            with self.subTest(changed=changed):
                unsupported = compile_oracle_card(replace(record, oracle_text=changed),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertTrue(unsupported.material_residuals)

        program = SimpleNamespace(event=node.event, active_zone=node.active_zone,
            event_condition=node.event_condition, effects=node.effects, target_schema=node.target_schema,
            handlers=(), cost_schema=None, coverage=node.mechanics, capability_dependencies=node.capability_dependencies)
        self.assertIn(MECHANIC, program.coverage)
        facts = {"from": "battlefield", "to": "graveyard", "card_object_identity": "departed@1"}
        for amount, maximum in ((0, -1), (1, 0), (4.0, 3)):
            with self.subTest(amount=amount):
                self.assertEqual(maximum, bind_departure_artifact_return_target(
                    program, {**facts, "mana_value": amount})["target_schema_override"]["mana_value_max"])
        for invalid in (None, True, -1, 2.5, float("inf"), "3"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                bind_departure_artifact_return_target(program, {**facts, "mana_value": invalid})
        with self.assertRaises(ValueError):
            bind_departure_artifact_return_target(program, {**facts, "to": "exile", "mana_value": 3})
        forged = SimpleNamespace(**{**vars(program), "target_schema": {**node.target_schema, "owner_relation": "any"}})
        with self.assertRaises(ValueError):
            departure_artifact_return_runtime_dependencies(forged)

    def test_actual_simultaneous_artifact_departures_retain_separate_mana_bounds_and_replay(self):
        game = self.session(601_412)
        engine = game.engine
        trawler = self.add(engine, "Scrap Trawler", ref="departure-source")
        steel = self.add(engine, "Sculpting Steel", zone="hand", ref="departure-copy")
        model = self.add(engine, "Thran Dynamo", seat="B", ref="departure-opposing-artifact")
        borrowed = self.add(engine, "Hedron Archive", seat="B", zone="hand", ref="departure-borrowed")
        engine.move_card(borrowed.object_id, "battlefield", controller="A")
        ring = self.add(engine, "Sol Ring", ref="departure-one-mana")
        ornithopter = self.add(engine, "Ornithopter", ref="departure-zero-mana")
        bauble = self.add(engine, "Mishra's Bauble", zone="graveyard", ref="departure-return-zero")
        stone = self.add(engine, "Mind Stone", zone="graveyard", ref="departure-return-two")
        powerstone = self.add(engine, "Worn Powerstone", zone="graveyard", ref="departure-return-three")
        destroy = self.add(engine, "Creeping Corrosion", zone="hand", ref="departure-sweep")
        self.prepare(game, mana={"C": 5, "G": 2})
        self.checkpoint(game)
        for source in (trawler, steel, destroy):
            bound = self.binding(game, source)
            self.assertTrue(bound["strict_capability_ready"], bound)
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)

        self.cast(game, "A", steel, payment={"C": 3})
        self.assertEqual("replacement.order", self.resolve(game).kind)
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        copied, = (row for row in choices if model.ref in row["id"] and not row["id"].startswith("decline:"))
        answer = game.act("pilot:A", {"action_id": "choose", "replacement": copied["id"]})
        self.assertTrue(answer.ok, answer.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._effective_card_data(game.state.cards[steel.object_id])["mana_value"])

        self.cast(game, "A", destroy, payment={"C": 2, "G": 2})
        pending = self.resolve(game)
        self.assertEqual(("trigger.order", ["A"]), (pending.kind, list(pending.actors)))
        view = game.packet("pilot:A", full=True)["decision"]["ctx"]
        ordered = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in view["triggers"]]})
        self.assertTrue(ordered.ok, ordered.summary)
        low = {ornithopter.ref, bauble.ref}
        below_three = low | {ring.ref, stone.ref}
        below_four = below_three | {trawler.ref, steel.ref, powerstone.ref}
        expected = {
            ring.ref: (1, low, bauble),
            trawler.ref: (3, below_three, stone),
            steel.ref: (4, below_four, steel),
            borrowed.ref: (4, below_four, powerstone),
        }
        seen = set()
        for _ in expected:
            decision = game.packet("pilot:A", full=True)["decision"]
            self.assertEqual(("semantic.target", "A"), (decision["kind"], decision["actor"]))
            item, = (part for part in game.state.stack if part.ref == decision["ctx"]["stack"])
            cause = item.context["card"]
            value, legal, chosen = expected[cause]
            self.assertNotIn(cause, seen)
            self.assertEqual(value, item.context["mana_value"])
            self.assertEqual(value - 1, item.context["target_schema_override"]["mana_value_max"])
            self.assertEqual(legal, set(decision["ctx"]["target_schema"]["legal_refs"]))
            if cause == trawler.ref:
                before = authoritative_state_hash(game.state)
                accepted = len(game.commands)
                for pilot, target in (("pilot:A", powerstone.ref), ("pilot:B", stone.ref)):
                    rejected = game.act(pilot, {"action_id": "choose", "targets": [target]})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    self.assertEqual(accepted, len(game.commands))
            result = game.act("pilot:A", {"action_id": "choose", "targets": [chosen.ref]})
            self.assertTrue(result.ok, result.summary)
            seen.add(cause)
        self.assertEqual(set(expected), seen)
        self.assertEqual(3, self.db.lookup("Sculpting Steel").mana_value)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for returned in (bauble, stone, steel, powerstone):
            self.assertEqual("hand", game.state.cards[returned.object_id].zone)
        for excluded in (trawler, ring, ornithopter, model, borrowed):
            self.assertEqual("graveyard", game.state.cards[excluded.object_id].zone)
        self.replay(game, load=True)

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
