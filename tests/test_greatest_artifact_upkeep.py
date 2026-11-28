"""Closed upkeep comparison and independent artifact protection."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.continuous_conditions import (
    GREATEST_ARTIFACT_MANA_VALUE_CAPABILITY,
    FixedPublicStateConditionSpec,
    greatest_artifact_mana_value_condition,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


PADEEM = "Padeem, Consul of Innovation"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
TRIGGER = ("At the beginning of your upkeep, if you control the artifact with the greatest mana value "
           "or tied for the greatest mana value, draw a card.")


class GreatestArtifactUpkeepTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "artifact-upkeep.sqlite3"
        build_fixture_database([
            FIXTURES / "scryfall-exact-lists.json",
            FIXTURES / "bound-effect-program-cards.json",
            FIXTURES / "activation-result-views-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Greatest artifact upkeep", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_upkeep_predicate_binding_and_owner_withdrawal(self):
        capability = GREATEST_ARTIFACT_MANA_VALUE_CAPABILITY
        card = query_record("Artifacts you control have hexproof.\n" + TRIGGER)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        static, trigger = ir.faces[0].nodes
        self.assertNotIn(capability, static.capability_dependencies)
        self.assertIn(capability, trigger.capability_dependencies)
        self.assertEqual(TRIGGER, card.oracle_text[trigger.span.start:trigger.span.end])
        predicate = trigger.event_condition["all"][1]["condition"]
        self.assertEqual(greatest_artifact_mana_value_condition(), FixedPublicStateConditionSpec.from_dict(predicate))
        for altered in (
            TRIGGER.replace("your upkeep", "each upkeep"),
            TRIGGER.replace("artifact", "permanent"),
            TRIGGER.replace(" or tied for the greatest mana value", ""),
            TRIGGER.replace("draw a card", "draw two cards"),
        ):
            with self.subTest(altered=altered):
                rejected = compile_oracle_card(replace(card, oracle_text=altered), capability_registry=self.registry,
                                                capability_profile=PROFILE)
                self.assertNotEqual("exact", rejected.status)

        admitted, semantics = register_card(self.db, self.registry, PADEEM)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        self.assertEqual([], admitted["compatibility_provenance"])
        original, = [program for program in semantics.programs_for_oracle(self.db.lookup(PADEEM).oracle_id)
                     if capability in program.capability_dependencies]
        altered = deepcopy(original.event_condition)
        altered["all"][0]["all"][0]["value"] = "$source.owner"
        verdict = bind_semantic_program_runtime(replace(original, event_condition=altered),
                                                 capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(verdict["strict"])
        self.assertTrue(any("closed controller upkeep" in reason for reason in verdict["blockers"]), verdict)

        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in value["capabilities"] if row["id"] == capability).update(
            status="blocked", blockers=["current artifact comparison withdrawn"],
        )
        withdrawn = CapabilityRegistry(value)
        index = build_index(registry_value=value,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        result = compile_oracle_card(card, capability_registry=withdrawn, capability_profile=PROFILE)
        self.assertNotEqual("exact", result.status)
        remaining, = [node for node in result.faces[0].nodes if node.node_id == static.node_id]
        self.assertEqual(static.text, remaining.text)
        self.assertEqual(static.capability_dependencies, remaining.capability_dependencies)
        self.assertTrue(result.material_residuals)

    def start_upkeep(self, session):
        self.prepare(session, active="D", actor="D")
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        session.state.phase = "ending"
        session.state.step = "end_step"
        session.state.last_normal_turn_player = "D"
        session.state.priority_passes = []
        session.engine._grant_priority("D")
        session.engine.pump()
        self.checkpoint(session)
        self.until(session, lambda game: game.state.active_player == "A" and game.state.step == "upkeep")

    def test_no_artifact_and_superior_opponent_artifact_suppress_initial_trigger(self):
        for has_owned in (False, True):
            with self.subTest(has_owned=has_owned):
                session = self.session(113203001 + has_owned)
                source = self.add(session.engine, PADEEM)
                self.add(session.engine, "Sol Ring", seat="B")
                if has_owned:
                    self.add(session.engine, "Darksteel Citadel")
                self.assert_closed(session, source)
                self.start_upkeep(session)
                self.assertEqual(0, self.trigger_count(session, source))
                self.replay(session, load=True)

    def test_copy_checks_its_current_controller_and_keeps_the_upkeep_occurrence(self):
        session = self.session(113203200)
        source = self.add(session.engine, PADEEM)
        self.add(session.engine, "Sol Ring")
        self.add(session.engine, "Sol Ring", seat="B", ref="artifact-upkeep-copy-tie")
        self.add(session.engine, "Darksteel Citadel", seat="C")
        self.assert_closed(session, source)
        self.start_upkeep(session)
        original = session.state.stack[-1]
        self.assertEqual("A", original.controller)
        for seat in ("B", "C"):
            copy = session.engine._copy_stack_item(
                controller=seat, target=original, targets=(), target_groups={},
                reason="Greatest artifact upkeep copied controller interaction",
            )
            self.assertEqual((seat, "A", original.ref),
                             (copy.controller, copy.context["player"], copy.context["copied_from_stack"]))
        self.checkpoint(session)
        session = self.replay(session, load=True)
        before = {seat: len(session.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.assertIsNone(self.resolve(session))
        self.assertEqual({**before, "A": before["A"] - 1, "B": before["B"] - 1},
                         {seat: len(session.state.players[seat].zones["library"]) for seat in "ABCD"})
        self.replay(session, load=True)

    def test_tied_artifact_upkeep_uses_current_board_and_survives_source_departure(self):
        for remove_artifact in (False, True):
            with self.subTest(remove_artifact=remove_artifact):
                session = self.session(113203100 + remove_artifact)
                source = self.add(session.engine, PADEEM)
                owned = self.add(session.engine, "Sol Ring" if remove_artifact else "Darksteel Citadel")
                rival = self.add(session.engine, owned.printed_name, seat="B", ref="artifact-upkeep-rival")
                ignored = self.add(session.engine, "Portal to Phyrexia", seat="C")
                ignored.phased_out = True
                bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
                self.add(session.engine, "Island", seat="B")
                disposal = self.add(session.engine, "Disenchant", seat="A", zone="hand")
                denied = self.add(session.engine, "Disenchant", seat="B", zone="hand", ref="artifact-upkeep-opponent-spell")
                self.add(session.engine, "Plains")
                self.add(session.engine, "Plains", ref="artifact-upkeep-other-plains")
                self.add(session.engine, "Plains", seat="B", ref="artifact-upkeep-opponent-plains")
                for card in (source, bounce, disposal, denied):
                    self.assert_closed(session, card)

                self.start_upkeep(session)
                self.assertEqual(1, self.trigger_count(session, source))
                self.assertEqual("A", session.state.stack[-1].controller)
                session = self.replay(session, load=True)
                before_library = len(session.state.players["A"].zones["library"])

                if remove_artifact:
                    self.priority(session, "B")
                    offered = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                    action = next(row for row in offered if row["id"] == "cast:" + denied.ref)
                    before = authoritative_state_hash(session.state)
                    invalid = session.act("pilot:B", {"action_id": action["id"], "targets": [owned.ref], "pay": "auto"})
                    self.assertFalse(invalid.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    self.cast(session, "B", denied, [rival.ref])
                    # A can target its own artifact while Padeem is present.
                    self.cast(session, "A", disposal, [owned.ref])
                    self.resolve_top(session)
                    self.resolve_top(session)
                    self.assertEqual("graveyard", session.state.cards[owned.object_id].zone)
                    self.assertEqual("graveyard", session.state.cards[rival.object_id].zone)
                else:
                    self.cast(session, "B", bounce, [source.ref])
                    self.resolve_top(session)
                    self.assertEqual("hand", session.state.cards[source.object_id].zone)
                    self.assertNotIn("Hexproof", session.engine._effective_card_data(owned.object_id)["keywords"])

                self.assertIsNone(self.resolve(session))
                self.assertEqual(before_library - int(not remove_artifact), len(session.state.players["A"].zones["library"]))
                if not remove_artifact:
                    drawn = session.state.cards[session.state.players["A"].zones["hand"][-1]]
                    for other in "BCD":
                        self.assertNotIn(drawn.ref, json.dumps(session.packet("pilot:" + other, full=True)))
                self.replay(session, load=True)
