from __future__ import annotations

"""Independent CR 109.2/603.2/603.6/603.10 qualified zone-event contract.

One committed object triggers each applicable subscription. Entry reads the
post-entry public characteristics; departure reads its previous battlefield
characteristics and controller, even when the observer also leaves. A source
union admits the source independently of the other-object qualifier. Token,
source-exclusion and ownership predicates are not interchangeable. Prevented
or redirected movements cannot manufacture a death. Ordinary APNAP placement,
target selection, continuations and replay remain owned by the existing engine.

The grammar excludes counters, history, relative comparisons, chosen subjects,
one-or-more aggregation, cross-zone card subjects and independent unknown bodies.
"""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_bound_effect_programs as bound_witnesses
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.unlock_frontier import analyze_card_unlocks
from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects, create_resolution_continuous_effect, ResolutionEffectSource
from quorune.continuous_effect_model import Layer, ContinuousOperation
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from scripts.work_selection_cohort_measurements import _bound_effect_program_measurement


def query_record(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="fixture:qualified-zone-query", name="Generic Query Observer",
        mana_cost="{W}", mana_value=1, type_line="Creature — Human",
        oracle_text=text, power="2", toughness="6", loyalty=None, defense=None,
        colors=("W",), color_identity=("W",), keywords=(), produced_mana=(),
        layout="normal", released_at="2026-01-01", legalities={"commander":"legal"},
        faces=(), raw={},
    )


def toughness_change() -> ContinuousOperation:
    return ContinuousOperation("modify_power_toughness", [0, 3])


class QualifiedZoneEventCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, registry=None):
        return compile_oracle_card(query_record(text),
            capability_registry=registry or self.registry, capability_profile="commander_review")

    def test_qualified_zone_event_grammar_preserves_domains(self):
        cases = (
            ("Whenever another legendary artifact you control enters, you gain 1 life.", "permanent.enter"),
            ("Whenever another white creature you control enters, you gain 1 life.", "permanent.enter"),
            ("Whenever Generic Query Observer or another red creature you control enters, you gain 1 life.", "permanent.enter"),
            ("Whenever a creature you control with toughness 4 or greater dies, you gain 1 life.", "creature.dies"),
            ("Whenever a creature with flying dies, you gain 1 life.", "creature.dies"),
            ("Whenever another white creature you control with flying enters, you gain 1 life.", "permanent.enter"),
            ("Whenever another nontoken creature you control with toughness 4 or greater dies, you gain 1 life.", "creature.dies"),
            ("Whenever another creature or planeswalker you control dies, you gain 1 life.", "permanent.graveyard"),
            ("Whenever another artifact or creature you control is put into a graveyard from the battlefield, you gain 1 life.", "permanent.graveyard"),
            ("Whenever a creature token leaves the battlefield, you gain 1 life.", "permanent.leave"),
            ("Whenever another Goblin dies, you gain 1 life.", "creature.dies"),
            ("Whenever another Goblin is put into a graveyard from the battlefield, you gain 1 life.", "permanent.graveyard"),
        )
        for text, event in cases:
            with self.subTest(text=text):
                compiled = self.compile(text)
                self.assertEqual("exact", compiled.status)
                nodes = tuple(n for f in compiled.faces for n in f.nodes)
                self.assertEqual(1, len(nodes))
                self.assertEqual(event, nodes[0].event)
                self.assertEqual(text, nodes[0].text)
                self.assertEqual(1, nodes[0].span.line)

    def test_qualified_zone_event_exclusions_remain_residual(self):
        excluded = (
            "Whenever one or more legendary creatures you control enter, draw a card.",
            "Whenever a creature with a +1/+1 counter on it dies, draw a card.",
            "Whenever a creature of the chosen type enters, draw a card.",
            "Whenever a creature with power greater than this creature's power dies, draw a card.",
            "Whenever a creature card is put into your graveyard from anywhere, draw a card.",
            "Whenever another red creature you control enters, draw a card. This ability triggers only once each turn.",
            "Whenever another legendary creature you control enters, choose a card at random.",
        )
        for text in excluded:
            with self.subTest(text=text):
                self.assertNotEqual("exact", self.compile(text).status)

    def test_qualified_zone_dependencies_and_parser_mutation_fail_closed(self):
        text = "Whenever another legendary creature you control enters, you gain 1 life."
        self.assertEqual("exact", self.compile(text).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for dependency in ("trigger.event.qualified_zone_change", "trigger.event.normalized_zone_change", "trigger.placement.apnap"):
            mutated = deepcopy(raw)
            row = next(r for r in mutated["capabilities"] if r["id"] == dependency)
            row["status"] = "blocked"
            row["blockers"] = ["constructed missing owner"]
            registry = CapabilityRegistry(mutated)
            self.assertNotEqual("exact", self.compile(text, registry=registry).status)
        with patch("quorune.compiler.fixed_counter_trigger_nodes.qualified_public_zone_event_binding_spec", return_value=None):
            self.assertNotEqual("exact", self.compile(text).status)

    def test_qualified_zone_measurement_requires_real_runtime_closure(self):
        text = "Whenever another legendary creature you control enters, you gain 1 life."
        candidates = (
            replace(query_record(text), oracle_id="fixture:zone-probe-1"),
            replace(query_record("Warp {2}\n"+text), oracle_id="fixture:zone-probe-2"),
        )
        with patch("quorune.compiler.fixed_counter_trigger_nodes.qualified_public_zone_event_binding_spec", return_value=None):
            baseline = [analyze_card_unlocks(
                compile_oracle_card(row, capability_registry=self.registry, capability_profile="commander_review"),
                program=None, program_error=None, capabilities=self.registry, profile="commander_review",
            ) for row in candidates]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"zone-probe.sqlite3"
            build_fixture_database([bound_witnesses.FIXTURE], path)
            with CardDatabase(path) as database:
                measured = _bound_effect_program_measurement(
                    frontier={"cards":baseline}, bundle_id="bundle:qualified-zone-event-queries",
                    probe_id="qualified-zone-event-query-existing-owner-v1",
                    cards_by_oracle_id={r.oracle_id:r for r in candidates},
                    coverage={"minimum_complete_card_gain":50, "minimum_exact_ability_gain":100,
                              "minimum_material_residual_reduction":100},
                    cohort_fingerprint="constructed-current-frontier", database=database,
                )
        self.assertEqual(2, measured["affected_commander_cards"])
        self.assertEqual(1, measured["complete_card_gain"])
        self.assertEqual(2, measured["exact_ability_gain"])
        self.assertFalse(measured["grants_gameplay_trust"])

    def test_qualified_zone_interaction_fixture_descriptors_are_closed(self):
        # Validate fixture descriptors without constructing a full four-seat
        # session, so API/schema wiring mistakes fail before the slow witness.
        self.assertEqual([0, 3], toughness_change().to_dict()["value"])
        self.assertEqual("remove_all_abilities", ContinuousOperation("remove_all_abilities").to_dict()["op"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"fixture-contract.sqlite3"
            build_fixture_database([ROOT/"tests/fixtures/qualified-zone-event-cards.json"], path)
            with CardDatabase(path) as database:
                for row in database.iter_cards():
                    with self.subTest(card=row.name):
                        compiled = compile_oracle_card(row, capability_registry=self.registry, capability_profile="commander_review")
                        self.assertEqual("exact", compiled.status)


class QualifiedZoneEventRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "qualified-zone.sqlite3"
        build_fixture_database([bound_witnesses.FIXTURE, ROOT / "tests/fixtures/qualified-zone-event-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Generic Query Deck", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 99)
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    session = bound_witnesses.BoundEffectProgramRuntimeTests.session
    add = bound_witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint = bound_witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound_witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = bound_witnesses.BoundEffectProgramRuntimeTests.replay

    def ready(self, session, source, mana):
        # An exact generic permanent with no text legitimately has no executable
        # SemanticProgram; its ordinary card declaration supplies cast trust.
        programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
        if programs:
            return bound_witnesses.BoundEffectProgramRuntimeTests.ready(self, session, source, mana)
        engine = session.engine
        self.assertTrue(engine._trusted_generic_spell(engine.card_record(source)))
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool.update(mana)
        engine._grant_priority("A")
        engine.pump()
        return next(action for action in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"] if action.get("card") == source.ref)

    def cast(self, session, source, mana, targets=()):
        action = self.ready(session, source, mana)
        result = session.act("pilot:A", {"action_id":action["id"], "targets":list(targets), "pay":"auto"})
        self.assertTrue(result.ok, result.summary)
        return result

    def test_trusted_entry_queries_use_current_characteristics_without_targeting(self):
        session = self.session(229001)
        engine = session.engine
        observer = self.add(engine, "Generic Query Entry Observer")
        wing = self.add(engine, "Generic Query Wing", zone="hand")
        action = self.ready(session, wing, {"W":1})
        self.checkpoint(session)
        self.assertTrue(engine.semantic_program_is_current_trusted(engine.semantics.programs_for_oracle(observer.oracle_id)[0]))
        result = session.act("pilot:A", {"action_id":action["id"], "pay":"auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual("battlefield", wing.zone)
        self.assertEqual(41, engine.state.players["A"].life)
        self.assertIn("hexproof", engine._combat_keywords(wing))
        self.assertEqual(0, engine.state.players["A"].mana_pool["W"])
        self.assertTrue(any(event.kind == "permanent_entered" for event in engine.state.turn_history.events))
        self.replay(session, load=True)
        body = self.add(engine, "Generic Bound Body", zone="hand", ref="white-nonflying")
        self.cast(session, body, {"W":1})
        self.resolve(session)
        self.assertEqual(41, engine.state.players["A"].life)

    def test_source_union_and_shared_ability_removal_use_existing_owners(self):
        session = self.session(229002)
        engine = session.engine
        source = self.add(engine, "Generic Query Union Observer", zone="hand")
        self.cast(session, source, {"W":1})
        self.resolve(session)
        self.assertEqual(41, engine.state.players["A"].life)
        # The source is white, so its self branch must not acquire the red qualifier.
        white = self.add(engine, "Generic Query Wing", zone="hand")
        self.cast(session, white, {"W":1})
        self.resolve(session)
        self.assertEqual(41, engine.state.players["A"].life)
        observer = self.add(engine, "Generic Query Entry Observer")
        # This fixture-only layer interaction uses the represented canonical
        # effect owner, not an unimplemented printed removal spell.
        create_resolution_continuous_effect(
            engine, source=ResolutionEffectSource(stack_ref="fixture:layer-6-removal"),
            targets=(observer,), layer=Layer.ABILITY, sublayer="6",
            operations=(ContinuousOperation("remove_all_abilities"),),
        )
        wing = self.add(engine, "Generic Query Wing", zone="hand", ref="muted-wing")
        self.cast(session, wing, {"W":1})
        self.resolve(session)
        self.assertEqual(41, engine.state.players["A"].life)
        expire_end_of_turn_continuous_effects(engine.state)
        next_wing = self.add(engine, "Generic Query Wing", zone="hand", ref="restored-wing")
        action = self.ready(session, next_wing, {"W":1})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id":action["id"], "pay":"auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(42, engine.state.players["A"].life)
        self.replay(session)

    def test_departure_queries_preserve_lki_controller_and_replacement_destinations(self):
        session = self.session(229003)
        engine = session.engine
        observer_a = self.add(engine, "Generic Query Death Observer")
        observer_b = self.add(engine, "Generic Query Death Observer", seat="B", ref="observer-b")
        stolen = self.add(engine, "Generic Query Small Body", ref="stolen")
        create_resolution_continuous_effect(
            engine, source=ResolutionEffectSource(stack_ref="fixture:toughness-change"),
            targets=(stolen,), layer=Layer.POWER_TOUGHNESS, sublayer="7c",
            operations=(toughness_change(),),
        )
        self.assertEqual(4, engine._numeric_stat(stolen.object_id, "toughness"))
        engine.change_control(stolen.object_id, "B", reason="constructed control effect")
        engine.move_card(stolen.object_id, "graveyard", semantic_events=True, log=False)
        engine._stabilize()
        engine._grant_priority("A")
        engine.pump()
        self.resolve(session)
        self.assertEqual(40, engine.state.players["A"].life)
        self.assertEqual(41, engine.state.players["B"].life)
        self.assertEqual(1, engine._numeric_stat(stolen.object_id, "toughness"))
        self.assertIn(stolen.object_id, engine.state.players["A"].zones["graveyard"])
        token = self.add(engine, "Generic Query Wing", ref="token")
        token.is_token = True
        token.object_kind = "token"
        engine.move_card(token.object_id, "graveyard", semantic_events=True, log=False)
        engine._stabilize()
        self.assertFalse(engine.state.stack)
        self.add(engine, "Generic Query Leave Observer")
        self.add(engine, "Generic Query Exiler", seat="B")
        recipient = self.add(engine, "Generic Query Wing", ref="redirected")
        engine.move_card(recipient.object_id, "graveyard", semantic_events=True, log=False)
        engine._stabilize()
        engine._grant_priority("A")
        engine.pump()
        self.resolve(session)
        self.assertEqual("exile", recipient.zone)
        # Its leave occurred, but the competing destination prevented its death.
        self.assertEqual(42, engine.state.players["A"].life)
        self.assertEqual(41, engine.state.players["B"].life)
        self.assertEqual("battlefield", observer_a.zone)
        self.assertEqual("battlefield", observer_b.zone)

    def test_simultaneous_qualified_deaths_keep_apnap_and_full_consequences(self):
        session = self.session(229004)
        engine = session.engine
        sources = {seat:self.add(engine, "Generic Query Death Observer", seat=seat, ref="observer-"+seat) for seat in "AC"}
        victims = {seat:self.add(engine, "Generic Query Small Body", seat=seat, ref="victim-"+seat) for seat in "AC"}
        for seat, victim in victims.items():
            create_resolution_continuous_effect(
                engine, source=ResolutionEffectSource(stack_ref="fixture:toughness-"+seat),
                targets=(victim,), layer=Layer.POWER_TOUGHNESS, sublayer="7c",
                operations=(toughness_change(),),
            )
            self.assertEqual(4, engine._numeric_stat(victim.object_id, "toughness"))
        private = self.add(engine, "Generic Bound Growth", seat="B", zone="hand", ref="private-b-hand")
        wipe = self.add(engine, "Generic Query Wipe", zone="hand")
        action = self.ready(session, wipe, {"W":1})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id":action["id"], "pay":"auto"})
        self.assertTrue(result.ok, result.summary)
        # Resolve the wipe but retain the ordinary APNAP triggers for inspection.
        for _ in range(12):
            if not any(item.kind == "spell" for item in engine.state.stack):
                break
            result = session.act(session.pending_principals()[0], {"action_id":"pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(["A", "C"], [item.controller for item in engine.state.stack])
        self.assertEqual([sources[seat].object_id for seat in "AC"], [item.source_object_id for item in engine.state.stack])
        self.assertTrue(all(item.context["event"] == "creature.dies" for item in engine.state.stack))
        for seat in "AC":
            self.assertEqual("graveyard", sources[seat].zone)
            self.assertEqual("graveyard", victims[seat].zone)
            self.assertEqual(1, engine._numeric_stat(victims[seat].object_id, "toughness"))
            self.assertEqual(40, engine.state.players[seat].life)
        for seat in "ABCD":
            packet = session.packet("pilot:"+seat, full=True)
            self.assertNotIn("library_order", json.dumps(packet))
            if seat != "B":
                self.assertNotIn(private.ref, json.dumps(packet))
        resumed = self.replay(session, load=True)
        self.resolve(resumed)
        self.assertEqual(41, resumed.state.players["A"].life)
        self.assertEqual(41, resumed.state.players["C"].life)
        self.replay(resumed)

    def test_qualified_trigger_targets_share_offer_validation_and_stale_revalidation(self):
        session = self.session(229005)
        engine = session.engine
        observer = self.add(engine, "Generic Query Target Observer")
        legal = self.add(engine, "Generic Bound Body", seat="B", ref="legal-target")
        hexproof = self.add(engine, "Generic Query Wing", seat="B", ref="illegal-hexproof")
        artifact = self.add(engine, "Generic Query Legendary Artifact", zone="hand")
        self.cast(session, artifact, {"C":2})
        pending = self.resolve(session)
        self.assertEqual("semantic.target", pending.kind)
        packet = session.packet("pilot:A", full=True)["decision"]
        schema = packet["ctx"]["target_schema"]
        offered = json.dumps(schema)
        self.assertIn(legal.ref, offered)
        self.assertNotIn(hexproof.ref, offered)
        self.assertNotIn(observer.ref, offered)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id":"choose", "targets":[hexproof.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        legal = engine.state.cards[legal.object_id]
        accepted = session.act("pilot:A", {"action_id":"choose", "targets":[legal.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        engine.move_card(legal.object_id, "exile", log=False)
        engine.move_card(legal.object_id, "battlefield", controller="B", log=False)
        engine._stabilize()
        self.resolve(session)
        self.assertFalse(legal.tapped)
