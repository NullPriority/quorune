from __future__ import annotations

"""CR 115.3/608.2c: one target occurrence, reused reference, printed order.

A targeted player draws then loses life; the ability controller is distinct.
A later 'it' refers to the same selected incarnation, preserving qualifiers.
Two explicit target clauses still require independent choices and stay outside
this grammar. A suspended replacement resumes after already committed effects.
"""

from copy import deepcopy
from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.bound_effect_programs import (
    BOUND_EFFECT_PROGRAM_CAPABILITY as CAPABILITY,
    BOUND_EFFECT_PROGRAM_MECHANIC as MECHANIC,
    bound_effect_program_template,
)
from quorune.compiler.closed_effect_programs import closed_effect_program_template
from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import _reviewed_atomic_effect_template, compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.bound_effect_program_shapes import bound_effect_program_node_capabilities
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.session import CommanderSession
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from scripts.work_selection_cohort_measurements import _bound_effect_program_measurement
from scripts import work_selection_cohort_measurements as cohort_measurements
from quorune.compiler.unlock_frontier import analyze_card_unlocks


FIXTURE = ROOT / "tests/fixtures/bound-effect-program-cards.json"


def record(text, type_line="Instant"):
    return CardRecord(
        oracle_id="fixture:bound-compiler", name="Generic Bound Compiler Fixture",
        mana_cost="{G}", mana_value=1, type_line=type_line, oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="6" if "Creature" in type_line else None,
        loyalty=None, defense=None, colors=("G",), color_identity=("G",),
        keywords=(), produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander":"legal"}, faces=(), raw={},
    )


class BoundEffectProgramCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, type_line="Instant", registry=None):
        return compile_oracle_card(record(text, type_line), capability_registry=registry or self.registry,
                                   capability_profile="commander_review")

    def test_bound_effect_grammar_compiles_across_contexts(self):
        bodies = (
            "Target player draws two cards and loses 2 life.",
            "Target opponent discards a card, mills two cards, and loses 1 life.",
            "Each opponent discards a card and loses 2 life.",
            "You gain 3 life and get {E}{E}{E}.",
            "Untap target creature you control with power 2 or less. It gets +2/+2 and gains trample until end of turn.",
            "Tap target artifact or creature an opponent controls. Put a stun counter on it.",
            "Put a +1/+1 counter on target creature. That creature gains reach until end of turn. You gain 2 life.",
            "Target creature gets +2/+1 until end of turn. Put a flying counter on it. Untap it.",
            "Another target creature you control gains flying until end of turn. Untap it.",
        )
        for body in bodies:
            for text, kind in (
                (body, "Instant"), ("{1}, {T}: " + body, "Artifact"),
                ("When this creature enters, " + body, "Creature — Human"),
                ("Choose one —\n• " + body + "\n• Draw a card.", "Sorcery"),
            ):
                with self.subTest(text=text):
                    ir = self.compile(text, kind)
                    self.assertEqual("exact", ir.status, ir.material_residuals)
                    self.assertTrue(any(CAPABILITY in n.capability_dependencies for f in ir.faces for n in f.nodes))
                    self.assertTrue(all(n.text == text[n.span.start:n.span.end] for f in ir.faces for n in f.nodes))

    def test_bound_effect_exclusions_and_malformed_shapes(self):
        compile_atomic = partial(_reviewed_atomic_effect_template, card_name="Generic Bound Compiler Fixture")
        valid = "Target player draws two cards and loses 2 life."
        self.assertIsNone(closed_effect_program_template(valid, compile_component=compile_atomic))
        for text in (
            "Untap target creature. Target creature gets +2/+2 until end of turn.",
            "Target player draws two cards. Target player loses 2 life.",
            "Target creature gets +2/+2 until end of turn. Its controller draws a card.",
            "Return target creature card from your graveyard to your hand. Untap that card.",
            "Target creature gets +2/+2 until end of turn. If it is attacking, untap it.",
            "Target player draws two cards and discards a card at random.",
            "Target creature gets +2/+2 until your next turn. Untap it.",
            "Target creature gets +2/+2 until end of turn. That player draws a card.",
            "Target player draws a card. Untap that creature.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(bound_effect_program_template(text, compile_component=compile_atomic))
                self.assertNotEqual("exact", self.compile(text).status)
        template = bound_effect_program_template(valid, compile_component=compile_atomic)
        self.assertIsNotNone(template)
        _, effects, schema, mechanics = template.compiled()
        self.assertIn(CAPABILITY, bound_effect_program_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for altered in (
            ({**effects[0], "player":"$target.1"}, effects[1]),
            ({**effects[0], "count":True}, effects[1]),
            ({**effects[0], "unknown":True}, effects[1]),
            (effects[0], {"op":"unknown"}),
        ):
            self.assertEqual((), bound_effect_program_node_capabilities(
                effects=altered, target_schema=schema, mechanic_ids=mechanics))
        for changed_schema in (None, {**schema, "count":True}, {**schema, "count":2}):
            self.assertEqual((), bound_effect_program_node_capabilities(
                effects=effects, target_schema=changed_schema, mechanic_ids=mechanics))

    def test_bound_effect_dependency_and_compiler_mutants(self):
        text = "Target player draws two cards and loses 2 life."
        self.assertEqual("exact", self.compile(text).status)
        for dependency in (CAPABILITY, "zone.draw.library_to_hand", "target.revalidate_resolution"):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text())
            row = next(r for r in raw["capabilities"] if r["id"] == dependency)
            row.update(status="blocked", blockers=["bound-program dependency mutation"])
            registry = CapabilityRegistry(raw)
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            self.assertNotEqual("exact", self.compile(text, registry=registry).status)
        with patch("quorune.compiler.effect_template_composition.bound_effect_program_template", return_value=None):
            self.assertNotEqual("exact", self.compile(text).status)

    def test_measurement_requires_real_whole_program_closure(self):
        candidates = tuple(replace(record(text, kind), oracle_id=f"fixture:bound-probe-{index}")
            for index, (text, kind) in enumerate((
                ("Target player draws two cards and loses 2 life.", "Instant"),
                ("Untap target creature. It gains flying until end of turn.", "Instant"),
                ("Warp {2}\nWhen this creature enters, target player draws two cards and loses 2 life.",
                 "Creature — Human"),
            )))
        with patch("quorune.compiler.effect_template_composition.bound_effect_program_template", return_value=None):
            baseline = [analyze_card_unlocks(
                compile_oracle_card(row, capability_registry=self.registry, capability_profile="commander_review"),
                program=None, program_error=None, capabilities=self.registry, profile="commander_review",
            ) for row in candidates]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"bound-probe.sqlite3"
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as database:
                arguments = dict(
                    frontier={"cards":baseline}, bundle_id="bundle:bound-effect-program-closure",
                    probe_id="bound-effect-program-existing-owner-v1",
                    cards_by_oracle_id={r.oracle_id:r for r in candidates},
                    coverage={"minimum_complete_card_gain":50, "minimum_exact_ability_gain":100,
                              "minimum_material_residual_reduction":100},
                    cohort_fingerprint="constructed-current-frontier", database=database,
                )
                compile_program = cohort_measurements.compile_best_available_card_program
                with patch.object(
                    cohort_measurements, "SemanticRegistry", wraps=SemanticRegistry
                ) as registry_factory, patch.object(
                    cohort_measurements, "compile_best_available_card_program", wraps=compile_program
                ) as compile_spy:
                    measured = _bound_effect_program_measurement(**arguments)
                    registry_factory.assert_called_once_with()
                    first_registry = compile_spy.call_args_list[0].kwargs["semantic_registry"]
                    self.assertGreaterEqual(compile_spy.call_count, 2)
                    self.assertTrue(all(
                        call.kwargs["semantic_registry"] is first_registry
                        for call in compile_spy.call_args_list
                    ))
                    registry_factory.reset_mock()
                    compile_spy.reset_mock()
                    self.assertEqual(measured, _bound_effect_program_measurement(**arguments))
                    registry_factory.assert_called_once_with()
                    self.assertIsNot(
                        first_registry, compile_spy.call_args_list[0].kwargs["semantic_registry"]
                    )

                def compile_with_fresh_registry(*args, **kwargs):
                    kwargs["semantic_registry"] = SemanticRegistry()
                    return compile_program(*args, **kwargs)

                with patch.object(
                    cohort_measurements, "compile_best_available_card_program",
                    side_effect=compile_with_fresh_registry,
                ):
                    self.assertEqual(measured, _bound_effect_program_measurement(**arguments))
                with patch.object(cohort_measurements, "SemanticRegistry") as registry_factory:
                    self.assertEqual(0, _bound_effect_program_measurement(
                        **{**arguments, "frontier":{"cards":[]}}
                    )["complete_card_gain"])
                    registry_factory.assert_not_called()
        self.assertEqual(3, measured["affected_commander_cards"])
        self.assertEqual(2, measured["complete_card_gain"])
        self.assertEqual(3, measured["exact_ability_gain"])
        self.assertFalse(measured["grants_gameplay_trust"])


class BoundEffectProgramRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "bound-effects.sqlite3"
        build_fixture_database([FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Generic Bound Deck", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 99),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed):
        session = CommanderSession.create(self.db, {s:deepcopy(self.deck) for s in "ABCD"},
            first_player="A", seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        records = tuple(self.db.iter_cards())
        register_generated_programs(self.db, engine.semantics, records, trust_level="provisional",
            capability_registry=self.registry, capability_profile="commander_review",
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, *, seat="A", zone="battlefield", ref=None):
        row = self.db.lookup(name)
        key = ref or name.lower().replace(" ", "-")
        card = CardInstance(object_id="fixture:"+key, ref=key, oracle_id=row.oracle_id,
            printed_name=row.name, owner=seat, controller=seat, zone=zone,
            zone_timestamp=engine._next_zone_timestamp(), known_to=list(engine.seats) if zone=="battlefield" else [seat],
            revealed_to=list(engine.seats) if zone=="battlefield" else [seat])
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def ready(self, session, source, mana):
        engine = session.engine
        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(programs)
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in programs))
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player="A"
        engine.state.started=True
        engine.state.phase="precombat_main"
        engine.state.step="main"
        engine.state.players["A"].mana_pool.update(mana)
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(r for r in actions if r.get("card")==source.ref or r["id"].startswith("activate:"+source.ref+":"))

    def checkpoint(self, session):
        session.initial_checkpoint=checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

    def resolve(self, session):
        for _ in range(48):
            decision = session.state.pending_decision
            if decision is not None and decision.kind!="priority": return decision
            if not session.state.stack: return None
            result=session.act(session.pending_principals()[0], {"action_id":"pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Bound effect program did not resolve")

    def replay(self, session, *, load=False):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"bound-program"
            session.save(path)
            result=replay_record(path, self.db, verify=True)
            self.assertTrue(result["ok"], result)
            self.assertEqual(authoritative_state_hash(session.state), result["final_state_hash"])
            if load:
                resumed=CommanderSession.load(self.db, path)
                self.assertEqual(authoritative_state_hash(session.state), authoritative_state_hash(resumed.state))
                return resumed

    def test_trusted_qualified_target_binding_and_stale_rollback(self):
        session=self.session(228001); engine=session.engine
        source=self.add(engine,"Generic Bound Growth",zone="hand")
        target=self.add(engine,"Generic Bound Body"); target.tapped=True
        enemy=self.add(engine,"Generic Bound Body",seat="B",ref="enemy")
        action=self.ready(session,source,{"G":1})
        legal=action["target_schema"]["legal_refs"]
        self.assertIn(target.ref,legal); self.assertNotIn(enemy.ref,legal)
        before=authoritative_state_hash(session.state)
        rejected=session.act("pilot:A",{"action_id":action["id"],"targets":[enemy.ref],"pay":"auto"})
        self.assertFalse(rejected.ok); self.assertEqual(before,authoritative_state_hash(session.state))
        # Command rollback restores a fresh authoritative object graph.
        target=engine.state.cards[target.object_id]
        self.checkpoint(session)
        accepted=session.act("pilot:A",{"action_id":action["id"],"targets":[target.ref],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary); self.resolve(session)
        self.assertFalse(target.tapped); self.assertEqual(4,engine._numeric_stat(target.object_id,"power"))
        self.assertIn("trample",engine._combat_keywords(target)); self.replay(session)
        expire_end_of_turn_continuous_effects(engine.state)
        mentor=self.add(engine,"Generic Bound Mentor")
        action=self.ready(session,mentor,{"C":1})
        self.assertNotIn(mentor.ref,action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        accepted=session.act("pilot:A",{"action_id":action["id"],"targets":[target.ref],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary); self.resolve(session); self.replay(session)
        self.assertTrue(mentor.tapped); self.assertIn("flying",engine._combat_keywords(target))
        source=self.add(engine,"Generic Bound Growth",zone="hand",ref="stale-growth")
        action=self.ready(session,source,{"G":1}); target.tapped=True
        accepted=session.act("pilot:A",{"action_id":action["id"],"targets":[target.ref],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        engine.move_card(target.object_id,"exile",log=False)
        engine.move_card(target.object_id,"battlefield",controller="A",log=False); target.tapped=True
        self.resolve(session)
        self.assertTrue(target.tapped); self.assertEqual(2,engine._numeric_stat(target.object_id,"power"))

    def test_trusted_player_subject_draw_loss_and_discard(self):
        session=self.session(228002); engine=session.engine
        source=self.add(engine,"Generic Bound Draw Loss",zone="hand")
        action=self.ready(session,source,{"B":1}); self.checkpoint(session)
        hand_before=len(engine.state.players["B"].zones["hand"]); life_before=engine.state.players["B"].life
        accepted=session.act("pilot:A",{"action_id":action["id"],"targets":["B"],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary); self.resolve(session)
        self.assertEqual(hand_before+2,len(engine.state.players["B"].zones["hand"]))
        self.assertEqual(life_before-2,engine.state.players["B"].life)
        self.assertEqual(40,engine.state.players["A"].life); self.replay(session)
        source=self.add(engine,"Generic Bound Discard Loss",zone="hand")
        action=self.ready(session,source,{"B":1}); self.checkpoint(session)
        accepted=session.act("pilot:A",{"action_id":action["id"],"targets":["B"],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session); self.assertEqual("choice.apnap",pending.kind)
        self.assertEqual(life_before-2,engine.state.players["B"].life)
        for seat in "ACD": self.assertIsNone(session.packet("pilot:"+seat,full=True)["decision"])
        resumed=self.replay(session,load=True)
        decision=resumed.packet("pilot:B",full=True)["decision"]
        choice=resumed.state.cards[resumed.state.players["B"].zones["hand"][0]].ref
        accepted=resumed.act("pilot:B",{"action_id":"choose","cards":[choice]})
        self.assertTrue(accepted.ok,accepted.summary); self.resolve(resumed); self.replay(resumed)
        self.assertEqual(life_before-3,resumed.state.players["B"].life)
        self.assertEqual(hand_before+1,len(resumed.state.players["B"].zones["hand"]))

    def test_counter_choice_resumes_after_prior_effect_once(self):
        session=self.session(228003); engine=session.engine
        source=self.add(engine,"Generic Bound Counter Sequence",zone="hand")
        target=self.add(engine,"Generic Bound Body"); target.tapped=True
        self.add(engine,"Generic Bound Counter Doubler"); self.add(engine,"Generic Bound Counter Adder")
        action=self.ready(session,source,{"G":1}); self.checkpoint(session)
        accepted=session.act("pilot:A",{"action_id":action["id"],"targets":[target.ref],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(session); self.assertEqual("replacement.order",decision.kind)
        self.assertEqual(4,engine._numeric_stat(target.object_id,"power")); self.assertTrue(target.tapped)
        self.assertNotIn("flying",target.counters)
        resumed=self.replay(session,load=True)
        for _ in range(8):
            pending=resumed.state.pending_decision
            if pending is None or pending.kind!="replacement.order": break
            packet=resumed.packet("pilot:A",full=True)["decision"]
            option=packet["ctx"]["options"][0]["id"]
            accepted=resumed.act("pilot:A",{"action_id":"choose","replacement":option})
            self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(resumed); current=resumed.state.cards[target.object_id]
        self.assertEqual(4,resumed.engine._numeric_stat(current.object_id,"power"))
        self.assertIn(current.counters["flying"],(3,4)); self.assertFalse(current.tapped)
        self.assertEqual(1,len(resumed.state.continuous_effects)); self.replay(resumed)

    def test_target_revalidation_precedes_but_does_not_repeat_between_effects(self):
        session=self.session(228004); engine=session.engine
        target=self.add(engine,"Generic Bound Body"); target.tapped=True
        source=self.add(engine,"Generic Bound Growth",zone="hand")
        action=self.ready(session,source,{"G":1})
        accepted=session.act("pilot:A",{"action_id":action["id"],"targets":[target.ref],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        engine.change_control(target.object_id,"B")
        self.resolve(session)
        self.assertTrue(target.tapped)
        self.assertEqual(2,engine._numeric_stat(target.object_id,"power"))
        self.assertNotIn("trample",engine._combat_keywords(target))
        engine.change_control(target.object_id,"A")
        source=self.add(engine,"Generic Bound Shroud Untap",zone="hand")
        action=self.ready(session,source,{"G":1}); self.checkpoint(session)
        accepted=session.act("pilot:A",{"action_id":action["id"],"targets":[target.ref],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary); self.resolve(session)
        self.assertIn("shroud",engine._combat_keywords(target))
        self.assertFalse(target.tapped)
        self.assertEqual(2,engine._numeric_stat(target.object_id,"power"))
        self.replay(session)


if __name__=="__main__": unittest.main()
