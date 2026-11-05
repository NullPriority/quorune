"""CR 608.2c/h/i, 614.6, 701.8 and 730.3 for actual destruction quantities."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.destruction_result_model import DESTRUCTION_RESULT_CAPABILITY, DestructionFollowup
from quorune.oracle_ir import compile_oracle_card
from quorune.permanent_composition import MergePosition
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_bound_effect_programs as bound_actions
import test_composition_commander_departure as composition_controls
import test_state_based_destination_replacements as replacement_controls


class CommittedDestructionFollowupCompilerTests(unittest.TestCase):
    def test_destruction_followup_preserves_source_span_and_typed_scope(self):
        registry = load_default_capability_registry()
        text = "Destroy all creatures. You gain life equal to the number of creatures you controlled that were destroyed this way."
        ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertIn(DESTRUCTION_RESULT_CAPABILITY, node.capability_dependencies)
        action, = node.effects
        scope = DestructionFollowup.from_dict(action["followup"]).scope
        self.assertEqual("actor", scope.controller_relation.value)
        self.assertEqual(("creature",), scope.query.types_all)

    def test_destruction_followup_rejects_unbound_and_malformed_results(self):
        registry = load_default_capability_registry()
        for text in (
            "Exile all creatures. Draw a card for each creature destroyed this way.",
            "Destroy target creature. Draw a card for each creature destroyed this way.",
            "Destroy all creatures. Draw a card for each land card destroyed this way.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=registry)
                self.assertNotEqual("exact", ir.status)
        ir = compile_oracle_card(record(
            "Destroy all creatures. Draw a card for each creature destroyed this way.", "Sorcery",
        ), capability_registry=registry)
        descriptor = ir.faces[0].nodes[0].effects[0]["followup"]
        for key, value in (("schema_version", True), ("effect", {"op": "draw", "count": 1}),
                           ("scope", {**descriptor["scope"], "controller_relation": "opponents"})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                DestructionFollowup.from_dict({**descriptor, key: value})

    def test_destruction_followup_withdrawn_capability_keeps_ordinary_destroy(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf8"))
        capability = next(row for row in raw["capabilities"] if row["id"] == DESTRUCTION_RESULT_CAPABILITY)
        capability.update(status="blocked", blockers=["Committed destruction result withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        text = "Destroy all creatures. Draw a card for each creature destroyed this way."
        ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=registry)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any(DESTRUCTION_RESULT_CAPABILITY in blocker
                            for residual in ir.material_residuals for blocker in residual.blockers))
        ordinary = compile_oracle_card(record("Destroy all creatures.", "Sorcery"), capability_registry=registry)
        self.assertEqual("exact", ordinary.status)

    def test_committed_destruction_quantity_multiplier_mutation_is_killed(self):
        ir = compile_oracle_card(record(
            "Destroy all creatures. You gain 2 life for each creature destroyed this way.", "Sorcery",
        ), capability_registry=load_default_capability_registry())
        spec = DestructionFollowup.from_dict(ir.faces[0].nodes[0].effects[0]["followup"])

        def witness():
            self.assertEqual((), spec.from_committed_count(0).effects)
            self.assertEqual(6, spec.from_committed_count(3).effects[0]["delta"])

        witness()
        original = DestructionFollowup.from_committed_count
        with patch.object(DestructionFollowup, "from_committed_count",
                          lambda current, count: original(current, int(bool(count)))):
            with self.assertRaises(AssertionError):
                witness()


class CommittedDestructionFollowupNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = bound_actions.BoundEffectProgramRuntimeTests.session
    add = bound_actions.BoundEffectProgramRuntimeTests.add
    checkpoint = bound_actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound_actions.BoundEffectProgramRuntimeTests.resolve
    replay = bound_actions.BoundEffectProgramRuntimeTests.replay
    priority = composition_controls.CompositionCommanderDepartureTests.priority
    cast = composition_controls.CompositionCommanderDepartureTests.cast
    assert_closed = composition_controls.CompositionCommanderDepartureTests.assert_closed
    composed_fixture = composition_controls.CompositionCommanderDepartureTests.fixture
    order_and_commander_choice = composition_controls.CompositionCommanderDepartureTests.order_and_commander_choice
    choose_destination = replacement_controls.StateBasedDestinationReplacementTests.choose
    replacements = replacement_controls.StateBasedDestinationReplacementTests.replacements

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "committed-destruction-followups.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "activation-view-equivalence-cards",
            "announced-x-activation-cards", "composition-commander-cards",
            "state-based-destination-cards", "umbra-armor-cards", "conditional-exile-cards",
            "committed-destruction-followup-cards",
        )
        build_fixture_database([ROOT / "tests/fixtures" / f"{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed destruction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_actual_death_begets_life_draws_for_committed_redirected_results(self):
        from quorune.counter_state import CounterChange, apply_counter_changes, plan_counter_changes

        session = self.session(608_270_811)
        engine = session.engine
        spell = self.add(engine, "Death Begets Life", zone="hand")
        protected = self.add(engine, "Grizzly Bears", seat="B", ref="causal-umbra-body")
        aura = self.add(engine, "Hyena Umbra", seat="B")
        redirected = self.add(engine, "Grizzly Bears", ref="causal-finality")
        self.replacements(session)
        indestructible = self.add(engine, "Darksteel Myr")
        token_ref, = engine.create_token("A", name="", copy_of=redirected.ref)
        self.assertTrue(token_ref)
        private = self.add(engine, "Generic Bound Growth", seat="C", zone="hand")
        attach_objects(session.state.cards, aura, protected, source_timestamp=engine._next_zone_timestamp())
        protected.marked_damage = 1
        apply_counter_changes(engine, plan_counter_changes(engine, [
            CounterChange("permanent", redirected.object_id, "finality", 1, "battlefield",
                          redirected.logical_object_id),
        ]))
        self.assert_closed(session, spell)
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime

        (program,) = (program for program in engine.semantics.programs_for_oracle(spell.oracle_id)
                      if any("followup" in effect for effect in program.effects))
        dependencies = tuple(value for value in program.capability_dependencies if value != DESTRUCTION_RESULT_CAPABILITY)
        withheld = replace(program, capability_dependencies=dependencies,
                           capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(withheld, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + DESTRUCTION_RESULT_CAPABILITY, binding["blockers"])
        self.priority(session, "A", {"C": 5, "B": 1, "G": 1, "U": 1})
        self.checkpoint(session)
        before = len(session.state.players["A"].zones["hand"])
        self.cast(session, "A", spell)
        self.assertEqual("replacement.order", self.resolve(session).kind)
        self.assertEqual(before - 1, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(1, session.state.cards[protected.object_id].marked_damage)
        self.assertEqual("battlefield", session.state.cards[aura.object_id].zone)
        session = self.replay(session, load=True)
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:B", full=True)))
        options = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        before_rejection = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:C", {"action_id": "choose", "replacement": options[0]["id"]}).ok)
        self.assertEqual(before_rejection, authoritative_state_hash(session.state))
        self.choose_destination(session, seat="B")
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before - 1 + 5, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("exile", session.state.cards[redirected.object_id].zone)
        self.assertEqual("exile", session.state.cards[aura.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[indestructible.object_id].zone)
        self.assertEqual(0, session.state.cards[protected.object_id].marked_damage)
        drawn = session.state.cards[session.state.players["A"].zones["hand"][-1]].ref
        self.assertIn(drawn, json.dumps(session.packet("pilot:A", full=True)))
        for seat in ("B", "C", "D"):
            self.assertNotIn(drawn, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        self.replay(session)

    def test_actual_ceaseless_conflict_counts_predeparture_controller_and_nontokens(self):
        session = self.session(608_270_812)
        engine = session.engine
        spell = self.add(engine, "Ceaseless Conflict", zone="hand")
        own = self.add(engine, "Grizzly Bears", ref="causal-own-creature")
        borrowed = self.add(engine, "Grizzly Bears", seat="B", ref="causal-borrowed-creature")
        foreign = self.add(engine, "Grizzly Bears", seat="B", ref="causal-foreign-creature")
        engine.change_control(borrowed.object_id, "A", reason="Prepared controller contrast")
        engine.create_token("A", name="", copy_of=own.ref)
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 3, "W": 2})
        self.checkpoint(session)
        self.cast(session, "A", spell)
        self.assertIsNone(self.resolve(session))
        spirits = [
            card for card in session.state.cards.values()
            if card.zone == "battlefield" and card.is_token and card.printed_name == "Spirit"
        ]
        self.assertEqual(["A", "A"], [card.controller for card in spirits])
        for creature in (own, borrowed, foreign):
            self.assertEqual("graveyard", session.state.cards[creature.object_id].zone)
        self.replay(session)

    def test_actual_rain_of_daggers_loses_life_for_the_selected_opponents_results(self):
        session = self.session(608_270_813)
        engine = session.engine
        spell = self.add(engine, "Rain of Daggers", zone="hand")
        victims = [self.add(engine, "Grizzly Bears", seat="B", ref=f"causal-victim-{index}") for index in range(2)]
        shielded = self.add(engine, "Grizzly Bears", seat="B", ref="causal-shielded")
        shielded.counters["shield"] = 1
        foreign = self.add(engine, "Grizzly Bears", seat="C", ref="causal-other-opponent")
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 4, "B": 2})
        self.checkpoint(session)
        before = session.state.players["A"].life
        self.cast(session, "A", spell, targets=("B",))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before - 4, session.state.players["A"].life)
        for victim in victims:
            self.assertEqual("graveyard", session.state.cards[victim.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[shielded.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[foreign.object_id].zone)
        self.replay(session)

    def test_actual_bane_of_progress_places_counters_from_its_committed_results(self):
        session = self.session(608_270_814)
        engine = session.engine
        spell = self.add(engine, "Bane of Progress", zone="hand")
        artifact = self.add(engine, "Ornithopter", seat="B")
        bearer = self.add(engine, "Grizzly Bears", seat="B")
        aura = self.add(engine, "Hyena Umbra", seat="B")
        attach_objects(session.state.cards, aura, bearer, source_timestamp=engine._next_zone_timestamp())
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 4, "G": 2})
        self.checkpoint(session)
        self.cast(session, "A", spell)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.cards[spell.object_id].counters.get("+1/+1"))
        for card in (artifact, aura):
            self.assertEqual("graveyard", session.state.cards[card.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[bearer.object_id].zone)
        self.replay(session)

    def test_actual_counted_destruction_retains_draws_through_conditional_aura_return(self):
        session = self.session(608_270_815)
        engine = session.engine
        source = self.add(engine, "Banishing Light", zone="hand")
        spell = self.add(engine, "Death Begets Life", zone="hand")
        doomed = self.add(engine, "Grizzly Bears", seat="B", ref="causal-return-original-bear")
        held = self.add(engine, "Hyena Umbra", seat="B", ref="causal-return-held-aura")
        survivor = self.add(engine, "Darksteel Myr", seat="B", ref="causal-return-survivor")
        attach_objects(session.state.cards, held, doomed, source_timestamp=engine._next_zone_timestamp())
        self.assert_closed(session, source)
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 7, "W": 1, "B": 1, "G": 1, "U": 1})
        self.checkpoint(session)

        self.cast(session, "A", source)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        accepted = session.act("pilot:A", {"action_id": "choose", "targets": [held.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[held.object_id].zone)
        before = len(session.state.players["A"].zones["hand"])
        self.cast(session, "A", spell)
        self.assertEqual("aura.entry", self.resolve(session).kind)
        self.assertEqual(["pilot:B"], list(session.pending_principals()))
        self.assertEqual(before - 1, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[doomed.object_id].zone)
        self.assertEqual("exile", session.state.cards[held.object_id].zone)

        session = self.replay(session, load=True)
        command = {"action_id": "choose", "aura_target": survivor.ref}
        before_rejection = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", command).ok)
        self.assertEqual(before_rejection, authoritative_state_hash(session.state))
        accepted = session.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        returned = session.state.cards[held.object_id]
        self.assertEqual(("battlefield", survivor.object_id), (returned.zone, returned.attached_to))
        self.assertEqual(before - 1 + 2, len(session.state.players["A"].zones["hand"]))
        self.replay(session)

    def test_actual_decree_draws_once_after_composed_order_and_save_load(self):
        session, root, commander, _, _control, _shock, _exile = self.composed_fixture(MergePosition.BOTTOM, 959330)
        spell = self.add(session.engine, "Decree of Pain", seat="C", zone="hand")
        self.assert_closed(session, spell)
        self.priority(session, "C", {"C": 6, "B": 2})
        self.checkpoint(session)
        before = len(session.state.players["C"].zones["hand"])
        self.cast(session, "C", spell)
        order = self.resolve(session)
        self.assertEqual(before - 1, len(session.state.players["C"].zones["hand"]))
        session = self.order_and_commander_choice(
            session, root, commander, order, zone="graveyard", ordering_actor="A", action="choose",
        )
        self.assertEqual(before, len(session.state.players["C"].zones["hand"]))
        result = session.act("pilot:A", {"action_id": "remain", "choice": "remain"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.replay(session)
