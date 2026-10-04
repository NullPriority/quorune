from __future__ import annotations

"""CR 400.7j/603.7c: follow the exiled incarnation, never a reused card ID."""

import unittest
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.linked_exile_return_templates import linked_exile_return_effect_template
from quorune.rules.linked_exile_return_shapes import linked_exile_return_node_capabilities
from quorune.linked_exile_return import resolve_linked_exile_return, return_linked_exiled_objects
from quorune.linked_exile_return_model import LinkedExileReturnSpec
from quorune.errors import GameRuleError
from pathlib import Path
import tempfile
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, GameConfig, StackItem
from quorune.session import CommanderSession
from quorune.record import checkpoint_envelope, authoritative_state_hash, replay_record
from quorune.oracle_ir import register_generated_programs
from common import ROOT, keep_all
from scripts.build_test_database import build_fixture_database


def record(text: str, *, type_line: str = "Instant") -> CardRecord:
    return CardRecord(
        oracle_id="fixture:linked-exile-return", name="Generic Exile Return Fixture",
        mana_cost="{1}{W}", mana_value=2, type_line=type_line,
        oracle_text=text, power="2" if "Creature" in type_line else None,
        toughness="3" if "Creature" in type_line else None, loyalty=None,
        defense=None, colors=("W",), color_identity=("W",), keywords=(),
        produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander": "legal"}, faces=(), raw={},
    )


class LinkedExileReturnCompilerTests(unittest.TestCase):
    def test_optional_cardinality_and_source_exclusion_are_independent(self):
        registry = load_default_capability_registry()
        for subject, maximum, optional, excluded in (
            ("up to one target creature you control", 1, True, False),
            ("another target creature you control", 1, False, True),
            ("up to one other target creature you control", 1, True, True),
            ("up to two other target creatures you control", 2, True, True),
        ):
            with self.subTest(subject=subject):
                reference = "those cards" if maximum > 1 else "that card"
                card = record(
                    "{W}: Exile " + subject + ", then return " + reference + " to the battlefield.",
                    type_line="Creature — Test",
                )
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                schema = ir.faces[0].nodes[0].target_schema
                self.assertEqual(excluded, bool(schema.get("source_exclusion")), schema)
                self.assertEqual(maximum, schema.get("up_to" if optional else "count"), schema)
                self.assertNotIn("count" if optional else "up_to", schema)

    def test_optional_qualifier_erasure_compiler_mutant_is_killed(self):
        import quorune.compiler.resolution_effect_templates as owner

        original = owner.linked_exile_return_effect_template

        def erase_optional_qualifier(text, **kwargs):
            return original(text.replace("up to one other target", "another target"), **kwargs)

        registry = load_default_capability_registry()
        card = record(
            "{W}: Exile up to one other target creature you control, then return that card to the battlefield.",
            type_line="Creature — Test",
        )
        with patch.object(owner, "linked_exile_return_effect_template", erase_optional_qualifier):
            ir = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        with self.assertRaises(AssertionError):
            self.assertEqual(1, ir.faces[0].nodes[0].target_schema.get("up_to"))

    def test_cohort_measurement_counts_net_exact_nodes_not_promoted_carriers(self):
        from scripts.work_selection_cohort_measurements import _bound_effect_program_measurement
        frontier={"cards":[{"oracle_id":"fixture:composition","oracle_ir_status":"partial","abilities":[
            {"face_id":"front","ability_id":"old","status":"exact","residuals":[]},
            {"face_id":"front","ability_id":"first","status":"unresolved","residuals":[{}]},
            {"face_id":"front","ability_id":"second","status":"unresolved","residuals":[{}]},
        ]}]}
        nodes=tuple(SimpleNamespace(node_id=identity,exact=True,capability_dependencies=("zone.linked_exile_return.fixed",)) for identity in ("first","second"))
        ir=SimpleNamespace(status="partial",faces=(SimpleNamespace(face_id="front",nodes=nodes),))
        with patch("scripts.work_selection_cohort_measurements.compile_oracle_card",return_value=ir):
            measurement=_bound_effect_program_measurement(frontier=frontier,bundle_id="bundle:fixture",probe_id="linked-exile-return-existing-owner-v2",
                cards_by_oracle_id={"fixture:composition":SimpleNamespace(oracle_text="Exile target creature, then return it to the battlefield.")},
                coverage={"minimum_complete_card_gain":50,"minimum_exact_ability_gain":100,"minimum_material_residual_reduction":100},cohort_fingerprint="fixture",database=object())
        self.assertEqual(1,measurement["exact_ability_gain"])
        self.assertEqual(2,measurement["material_residual_reduction"])

    def test_closed_descriptor_has_exact_capability_shape(self):
        compiled = linked_exile_return_effect_template("Exile target creature, then return that card to the battlefield under its owner's control.", card_name="Generic")
        self.assertIsNotNone(compiled)
        _, effects, schema, mechanics = compiled
        self.assertIn("zone.linked_exile_return.fixed", linked_exile_return_node_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for altered in (
            ({**effects[0], "unknown": True}, effects[1]),
            (effects[0], {**effects[1], "binding_id": "blink:different"}),
            ({**effects[0], "spec": {**effects[0]["spec"], "tapped": 1}}, effects[1]),
        ):
            with self.subTest(altered=altered):
                self.assertEqual((), linked_exile_return_node_capabilities(effects=altered, target_schema=schema, mechanic_ids=mechanics))

    def test_missing_linked_return_dependency_is_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        import json
        from pathlib import Path
        value = json.loads((Path(__file__).parents[1] / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for dependency in ("target.revalidate_resolution", "zone.change.destination_replacement", "trigger.placement.apnap"):
            changed = deepcopy(value)
            next(row for row in changed["capabilities"] if row["id"] == dependency)["status"] = "blocked"
            registry = CapabilityRegistry(changed)
            closure = registry.closure(("zone.linked_exile_return.fixed",), profile="commander_review")
            self.assertFalse(closure.trusted)
            self.assertIn("status:"+dependency+":blocked",closure.blockers)


class LinkedExileReturnRuntimeContractTests(unittest.TestCase):
    def test_source_placeholder_excludes_absent_and_reentered_incarnations(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        card=SimpleNamespace(object_id="source",ref="SOURCE",logical_object_id="new",zone="battlefield",phased_out=False)
        host=SimpleNamespace(state=SimpleNamespace(cards={"source":card}))
        item=SimpleNamespace(source_object_id="source",context={"source_logical_object_id":"old"})
        self.assertIsNone(resolve_semantic_value(host,"$source.zone_object",item))
        card.logical_object_id="old";card.zone="hand"
        self.assertIsNone(resolve_semantic_value(host,"$source.zone_object",item))
        card.zone="battlefield"
        self.assertEqual("SOURCE",resolve_semantic_value(host,"$source.zone_object",item))

    def test_return_keyword_uses_new_identity_and_cleanup_expiration(self):
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        host,card,_=self.host()
        card.zone="exile";card.logical_object_id="exile@2"
        host.state.continuous_effects=[]
        host._next_ref=lambda prefix:prefix+"1"
        host._next_zone_timestamp=lambda:1
        def move(owner,changes,**kwargs):
            card.zone="battlefield";card.logical_object_id="returned@3"
            return [card]
        effect={"op":"return_linked_exiled_objects","objects":[{"object_id":card.object_id,"ref":card.ref,"logical_object_id":"exile@2"}],"spec":LinkedExileReturnSpec(keywords=("First Strike",)).to_dict(),"_runtime_source":self.source()}
        with patch("quorune.linked_exile_return.ZoneTransitionOwner.move_cards_simultaneously",move):
            self.assertEqual([card.ref],return_linked_exiled_objects(host,effect,actor="A",operation=effect["op"],reason="fixture"))
        self.assertEqual("returned@3",host.state.continuous_effects[0].locked_objects[0].logical_object_id)
        self.assertEqual(1,expire_end_of_turn_continuous_effects(host.state))
        self.assertEqual([],host.state.continuous_effects)

    def host(self):
        card = SimpleNamespace(object_id="object", ref="OBJECT", logical_object_id="object@1", zone="battlefield", owner="B", controller="A", phased_out=False, is_token=False, is_spell_copy=False)
        item = SimpleNamespace(ref="STACK", context={})
        host = SimpleNamespace(state=SimpleNamespace(cards={"object":card}, stack=[item], turn_sequence=1, event_sequence=1), active_seats=("A", "B", "C", "D"))
        host._resolve_object = lambda actor, ref, zones: card if card.ref == ref and card.zone in zones else self.fail("Invalid generic fixture reference")
        return host, card, item

    @staticmethod
    def source():
        return {"stack_ref":"STACK", "object_id":None, "logical_object_id":None, "card_ref":None}

    def test_committed_exile_identity_and_return_incarnation_are_distinct(self):
        host, card, item = self.host()
        def move(owner, changes, **kwargs):
            for _, zone in changes:
                card.zone = zone
                card.logical_object_id = "object@2" if zone == "exile" else "object@3"
                if zone == "battlefield": card.controller = kwargs["destination_controllers"]["object"]
            return [card]
        spec = LinkedExileReturnSpec().to_dict()
        common = {"op":"linked_exile_return", "binding_id":"blink:fixture", "spec":spec, "_runtime_source":self.source()}
        with patch("quorune.linked_exile_return.ZoneTransitionOwner.move_cards_simultaneously", move):
            resolve_linked_exile_return(host, {**common,"phase":"exile","cards":["OBJECT"]}, actor="A",operation="linked_exile_return",reason="fixture")
            binding = item.context["linked_exile_returns"]["blink:fixture"]
            self.assertEqual("object@2", binding["objects"][0]["logical_object_id"])
            resolve_linked_exile_return(host, {**common,"phase":"return"}, actor="A",operation="linked_exile_return",reason="fixture")
        self.assertEqual(("battlefield", "B", "object@3"), (card.zone, card.controller, card.logical_object_id))
        self.assertTrue(binding["completed"])
        with self.assertRaises(GameRuleError):
            resolve_linked_exile_return(host, {**common,"phase":"return"},actor="A",operation="linked_exile_return",reason="fixture")

    def test_stale_exiled_incarnations_and_tokens_do_not_return(self):
        host, card, _ = self.host()
        objects=[{"object_id":"object","ref":"OBJECT","logical_object_id":"object@2"}]
        effect={"op":"return_linked_exiled_objects","objects":objects,"spec":LinkedExileReturnSpec().to_dict()}
        for zone, incarnation, token in (("exile","object@3",False),("hand","object@2",False),("exile","object@2",True)):
            with self.subTest(zone=zone, incarnation=incarnation, token=token):
                card.zone, card.logical_object_id, card.is_token = zone, incarnation, token
                with patch("quorune.linked_exile_return.ZoneTransitionOwner.move_cards_simultaneously") as move:
                    self.assertEqual([],return_linked_exiled_objects(host,effect,actor="A",operation=effect["op"],reason="fixture"))
                    move.assert_not_called()

    def test_identity_guard_mutant_is_killed(self):
        host, card, _ = self.host()
        card.zone = "exile"
        effect={"op":"return_linked_exiled_objects","objects":[{"object_id":"object","ref":"OBJECT","logical_object_id":"old"}],"spec":LinkedExileReturnSpec().to_dict()}
        with patch("quorune.linked_exile_return._eligible_exiled",return_value=(card,)), patch("quorune.linked_exile_return.ZoneTransitionOwner.move_cards_simultaneously",return_value=[card]):
            card.zone = "battlefield"
            with self.assertRaises(AssertionError):
                self.assertEqual([],return_linked_exiled_objects(host,effect,actor="A",operation=effect["op"],reason="fixture"))

class LinkedExileReturnIntegratedCompilerTests(unittest.TestCase):
    def test_repeated_modal_blink_instructions_have_distinct_binding_ids(self):
        from quorune.targets import mode_effects
        ir=compile_oracle_card(record("Choose two —\n• Exile target creature, then return it to the battlefield under its owner's control.\n• Exile target creature, then return it to the battlefield under its owner's control.\n• Draw a card."),capability_registry=load_default_capability_registry(),capability_profile="commander_review")
        self.assertEqual("exact",ir.status)
        node=ir.faces[0].nodes[0]
        effects=mode_effects(node.target_schema,["mode_1","mode_2"],target_groups={"mode_1_target_1":["FIRST"],"mode_2_target_1":["SECOND"]})
        bindings=[effect["binding_id"] for effect in effects if effect.get("phase")=="exile"]
        self.assertEqual(2,len(bindings));self.assertEqual(2,len(set(bindings)))

    def test_immediate_and_delayed_blink_share_a_typed_lifecycle(self):
        cases = (
            ("Exile target creature you control, then return that card to the battlefield under its owner's control.", "Instant", "immediate", False),
            ("Exile another target nonland permanent. Return it to the battlefield tapped under your control at the beginning of the next end step.", "Instant", "next_end_step", True),
            ("{1}{W}: Exile this creature. Return it to the battlefield under its owner's control at the beginning of the next end step.", "Creature — Test", "next_end_step", False),
            ("Whenever this creature attacks, exile another target creature. Return that card to the battlefield under its owner's control at the beginning of the next end step.", "Creature — Test", "next_end_step", False),
            ("Exile up to two target creatures you control, then return those cards to the battlefield under their owner's control.", "Instant", "immediate", False),
        )
        registry = load_default_capability_registry()
        for text, type_line, timing, tapped in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, type_line=type_line), capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status)
                effects = [effect for face in ir.faces for node in face.nodes for effect in node.effects]
                blink = next(effect for effect in effects if effect.get("op") == "linked_exile_return")
                self.assertEqual(timing, blink["spec"]["timing"])
                self.assertIs(tapped, blink["spec"]["tapped"])

    def test_optional_blink_is_one_choice_over_the_validated_pair(self):
        registry=load_default_capability_registry()
        ir=compile_oracle_card(record("When this creature enters, you may exile another target creature you control, then return it to the battlefield under its owner's control.",type_line="Creature — Test"),capability_registry=registry,capability_profile="commander_review")
        self.assertEqual("exact",ir.status)
        node=ir.faces[0].nodes[0]
        self.assertEqual("offer_optional_effect",node.effects[0]["op"])
        self.assertEqual(["exile","return"],[effect["phase"] for effect in node.effects[0]["effects"]])

    def test_unrepresented_linked_and_transformed_forms_remain_residual(self):
        registry = load_default_capability_registry()
        for text in (
            "Exile target creature until this creature leaves the battlefield.",
            "Exile target creature, then return that card to the battlefield transformed under your control.",
            "Exile target creature. If it was a Pirate, draw a card. Return it to the battlefield under your control.",
            "Exile target creature and all Auras attached to it, then return those cards to the battlefield under your control.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(record(text), capability_registry=registry, capability_profile="commander_review").status)


class LinkedExileReturnActionTests(unittest.TestCase):
    def register_tap_fixtures(self, session):
        register_generated_programs(
            self.db, session.engine.semantics,
            tuple(card for card in self.db.iter_cards() if card.oracle_text),
            trust_level="provisional", capability_registry=load_default_capability_registry(),
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        for name in ("Generic Tap Transition Witness", "Generic Event Controller Mill Witness"):
            programs = session.engine.semantics.programs_for_oracle(self.db.lookup(name).oracle_id)
            self.assertTrue(programs)
            self.assertTrue(all(session.engine.semantic_program_is_current_trusted(program) for program in programs))

    def optional_other_fixture(self, seed, *, eligible, extra_other=False):
        session = self.session(seed)
        register_generated_programs(
            self.db, session.engine.semantics,
            tuple(card for card in self.db.iter_cards() if card.oracle_text),
            trust_level="provisional", capability_registry=load_default_capability_registry(),
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        source = self.add(session, "Generic Optional Other Blink", "OPTIONAL-SOURCE", zone="battlefield")
        victim = self.add(session, "Generic Blink Victim", "OPTIONAL-VICTIM", zone="battlefield") if eligible else None
        if extra_other:
            self.add(session, "Generic Blink Victim", "OPTIONAL-EXTRA", zone="battlefield")
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            self.assertTrue(session.engine.semantic_program_is_current_trusted(program))
        self.seal(session)
        decision = session.packet("pilot:A", full=True)["decision"]
        actions = [action for action in decision["ctx"]["legal"]["actions"]
                   if action.get("source") == source.ref and action["id"].startswith("activate:")]
        self.assertEqual(1, len(actions), "An up-to-one activation remains offered without an eligible target")
        return session, source, victim, actions[0]

    def optional_other_zero_targets(self, seed, *, eligible):
        session, source, victim, action = self.optional_other_fixture(seed, eligible=eligible)
        original = victim.logical_object_id if victim else None
        self.assertEqual(1, action["target_schema"]["up_to"])
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        # No target was selected; there is no later optional-effect decline.
        self.assertEqual([], session.state.stack[-1].targets)
        self.finish(session)
        if victim:
            self.assertEqual(original, session.state.cards[victim.object_id].logical_object_id)
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.assertEqual(7, session.state.players["A"].mana_pool["W"])
        self.replay(session)

    def test_optional_other_zero_targets_with_eligible_object_and_replay(self):
        self.optional_other_zero_targets(24700201, eligible=True)

    def test_optional_other_zero_targets_without_eligible_object_and_replay(self):
        self.optional_other_zero_targets(24700202, eligible=False)

    def test_optional_other_one_target_illegal_source_excess_rollback_and_replay(self):
        session, source, victim, action = self.optional_other_fixture(
            24700203, eligible=True, extra_other=True,
        )
        extra = next(card for card in session.state.cards.values() if card.ref == "OPTIONAL-EXTRA")
        for refs in ([source.ref], [victim.ref, extra.ref]):
            before = authoritative_state_hash(session.state)
            result = session.act("pilot:A", {"action_id": action["id"], "targets": refs, "pay": "auto"})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        original = victim.logical_object_id
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [victim.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.finish(session)
        returned = session.state.cards[victim.object_id]
        self.assertNotEqual(original, returned.logical_object_id)
        self.assertEqual("battlefield", returned.zone)
        self.assertEqual(2, returned.zone_change_counter)
        self.replay(session)

    def test_exact_blink_does_not_admit_unsupported_target_siblings(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        registry=load_default_capability_registry()
        for sibling in (
            "{W}: Two target creatures with different names each get +1/+1 until end of turn.",
            "{W}: Target creature with the chosen name gains flying until end of turn.",
        ):
            with self.subTest(sibling=sibling):
                card=record("{W}: Exile this creature, then return it to the battlefield.\n"+sibling,type_line="Creature — Test")
                ir=compile_oracle_card(card,capability_registry=registry,capability_profile="commander_review")
                self.assertTrue(any(node.exact and "zone.linked_exile_return.fixed" in node.capability_dependencies for face in ir.faces for node in face.nodes))
                self.assertTrue(ir.material_residuals)
                program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=registry,capability_profile="commander_review")
                binding=bind_card_program_runtime(program,capability_registry=registry,profile="commander_review")
                self.assertFalse(binding["strict_capability_ready"])
                self.assertFalse(binding["compatible_ready"])
                self.assertEqual("unresolved",binding["trust_basis"])
                self.assertTrue(binding["blockers"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "blink.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-exile-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.deck = DeckDefinition("Generic blink", [DeckEntry("Generic Blink Commander",1,"commander"), DeckEntry("Generic Blink Plains",15)], ["Generic Blink Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed=24400001):
        session = CommanderSession.create(self.db, {seat:deepcopy(self.deck) for seat in "ABCD"}, first_player="A", seed=seed, config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        keep_all(session)
        session.state.pending_decision = None
        session.state.priority_player = None
        session.state.priority_passes = []
        session.state.active_player = "A"
        session.state.started = True
        session.state.phase = "precombat_main"; session.state.step = "main"
        session.state.phase_index = 3
        session.state.players["A"].mana_pool["W"] = 8
        return session

    def add(self, session, name, ref, *, owner="A", controller=None, zone="hand"):
        row = self.db.lookup(name); controller = controller or owner
        card = CardInstance(object_id="blink:"+ref, ref=ref, oracle_id=row.oracle_id, printed_name=row.name, owner=owner, controller=controller,zone=zone,zone_timestamp=session.engine._next_zone_timestamp(),known_to=list("ABCD") if zone=="battlefield" else [owner])
        session.state.cards[card.object_id]=card
        session.state.players[controller if zone=="battlefield" else owner].zones[zone].append(card.object_id)
        return card

    def test_actual_zone_owner_returns_owner_and_seals_departure_history(self):
        session = self.session()
        card = self.add(session,"Generic Blink Victim","VICTIM",owner="B",controller="A",zone="battlefield")
        item = StackItem(stack_id="fixture:stack",ref="STACK",kind="triggered_ability",controller="A",label="Generic diagnostic")
        session.state.stack.append(item)
        spec = LinkedExileReturnSpec().to_dict()
        common={"op":"linked_exile_return","binding_id":"blink:direct","spec":spec,"_runtime_source": {"stack_ref":"STACK","object_id":None,"logical_object_id":None,"card_ref":None}}
        original=card.logical_object_id
        resolve_linked_exile_return(session.engine,{**common,"phase":"exile","cards":[card.ref]},actor="A",operation=common["op"],reason="generic boundary diagnostic")
        exiled=card.logical_object_id
        self.assertEqual("exile",card.zone); self.assertNotEqual(original,exiled)
        resolve_linked_exile_return(session.engine,{**common,"phase":"return"},actor="A",operation=common["op"],reason="generic boundary diagnostic")
        self.assertEqual(("battlefield","B"),(card.zone,card.controller))
        self.assertNotEqual(exiled,card.logical_object_id)
        self.assertIn(card.object_id,session.state.players["B"].zones["battlefield"])
        session.state.stack.clear()

    def register(self, session):
        register_generated_programs(self.db,session.engine.semantics,tuple(record for record in self.db.iter_cards() if record.oracle_text),
            trust_level="trusted",capability_registry=load_default_capability_registry(),capability_profile="commander_review",
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)

    def seal(self, session):
        session.engine._grant_priority("A"); session.engine.pump()
        session.initial_checkpoint=checkpoint_envelope(session.state)
        session.commands.clear(); session.decisions.clear()

    def cast(self, session, spell, targets):
        session.engine.pump()
        decision=session.packet("pilot:A",full=True)["decision"]
        action=next(action for action in decision["ctx"]["legal"]["actions"] if action["id"]=="cast:"+spell.ref)
        if targets:
            self.assertTrue(set(targets)<=set(action["target_schema"]["legal_refs"]))
        result=session.act("pilot:A",{"action_id":action["id"],"targets":list(targets),"pay":"auto"})
        self.assertTrue(result.ok,result.summary)

    def finish(self, session):
        for _ in range(40):
            if not session.state.stack and not session.state.pending_trigger_batches: return
            session.engine.pump()
            principal=session.pending_principals()[0]
            decision=session.packet(principal,full=True)["decision"]
            self.assertEqual("priority",decision["kind"])
            result=session.act(principal,{"action_id":"pass"});self.assertTrue(result.ok,result.summary)
        self.fail("Generic linked return did not finish")

    def advance(self, session, phase, step):
        for _ in range(120):
            if (session.state.phase,session.state.step)==(phase,step): return
            session.engine.pump()
            principal=session.pending_principals()[0]
            decision=session.packet(principal,full=True)["decision"]
            action={"action_id":"pass"} if decision["kind"]=="priority" else {"action_id":"attack","attackers":[]} if decision["kind"]=="declare_attackers" else None
            self.assertIsNotNone(action,decision)
            result=session.act(principal,action);self.assertTrue(result.ok,result.summary)
        self.fail("Generic turn did not reach its end step")

    def replay(self,session):
        expected=authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"record";session.save(path)
            loaded=CommanderSession.load(self.db,path)
            self.assertEqual(expected,authoritative_state_hash(loaded.state))
            replay=replay_record(path,self.db,verify=True)
            self.assertTrue(replay["ok"],replay)
            self.assertEqual(expected,replay["final_state_hash"])

    def test_trusted_tap_cost_and_untap_effect_dispatch_and_replay(self):
        session = self.session(24800001)
        self.register_tap_fixtures(session)
        source = self.add(session, "Generic Tap Transition Witness", "TAP_SOURCE", zone="battlefield")
        spell = self.add(session, "Generic Untap Transition Spell", "UNTAP")
        self.add(session, "Generic Event Controller Mill Witness", "OBSERVER", controller="B", owner="B", zone="battlefield")
        self.seal(session)
        before_hand = len(session.state.players["A"].zones["hand"])
        decision = session.packet("pilot:A", full=True)["decision"]
        action = next(value for value in decision["ctx"]["legal"]["actions"]
                      if value.get("source") == source.ref and value["id"].startswith("activate:"))
        result = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(source.tapped)
        self.assertTrue(session.state.stack or session.state.pending_trigger_batches)
        before_rejected = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": action["id"]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before_rejected, authoritative_state_hash(session.state))
        self.finish(session)
        self.assertEqual(before_hand + 1, len(session.state.players["A"].zones["hand"]))
        drawn_id = session.state.players["A"].zones["hand"][-1]
        drawn = session.state.cards[drawn_id]
        for seat in "BCD":
            self.assertNotIn(drawn.oracle_id, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        before_life = session.state.players["A"].life
        before_graveyard = len(session.state.players["A"].zones["graveyard"])
        self.cast(session, spell, [source.ref])
        self.finish(session)
        self.assertFalse(source.tapped)
        self.assertEqual(before_life + 2, session.state.players["A"].life)
        self.assertEqual(before_graveyard + 2, len(session.state.players["A"].zones["graveyard"]))
        self.replay(session)

    def test_trusted_untap_step_holds_trigger_and_preserves_incarnation(self):
        session = self.session(24800002)
        self.register_tap_fixtures(session)
        source = self.add(session, "Generic Tap Transition Witness", "UNTAP_STEP_SOURCE", zone="battlefield")
        source.tapped = True
        before_life = session.state.players["A"].life
        from quorune.engine import TURN_STEPS
        session.state.phase_index = TURN_STEPS.index(("ending", "cleanup"))
        session.state.phase = "ending"
        session.state.step = "cleanup"
        self.seal(session)
        # This direct boundary witness is distinct from the command witness
        # above: the existing turn coordinator holds triggers through no-priority
        # untap and places them only when upkeep supplies priority.
        session.state.phase_index = TURN_STEPS.index(("beginning", "untap"))
        session.engine._enter_step()
        self.assertFalse(source.tapped)
        self.assertEqual(("beginning", "upkeep"), (session.state.phase, session.state.step))
        self.assertEqual(before_life, session.state.players["A"].life)
        self.assertTrue(session.state.stack or session.state.pending_trigger_batches)
        self.finish(session)
        self.assertEqual(before_life + 2, session.state.players["A"].life)

    def test_trusted_immediate_blink_offer_payment_owner_privacy_and_replay(self):
        session=self.session(24400002)
        self.register(session)
        victim=self.add(session,"Generic Blink Entry Witness","VICTIM",owner="B",controller="A",zone="battlefield")
        spell=self.add(session,"Generic Immediate Blink","BLINK")
        opponent=self.add(session,"Generic Blink Victim","OPPONENT",owner="C",zone="battlefield")
        for program in session.engine.semantics.programs_for_oracle(spell.oracle_id):
            self.assertTrue(session.engine.semantic_program_is_current_trusted(program))
        self.seal(session)
        before=authoritative_state_hash(session.state)
        result=session.act("pilot:A",{"action_id":"cast:"+spell.ref,"targets":[opponent.ref],"pay":"auto"})
        self.assertFalse(result.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        old=session.state.cards[victim.object_id].logical_object_id
        self.cast(session,session.state.cards[spell.object_id],[victim.ref])
        self.finish(session)
        returned=session.state.cards[victim.object_id]
        self.assertEqual(("battlefield","B"),(returned.zone,returned.controller))
        self.assertNotEqual(old,returned.logical_object_id)
        self.assertEqual("graveyard",session.state.cards[spell.object_id].zone)
        self.assertEqual(8,len(session.state.players["B"].zones["hand"]))
        packet=session.packet("pilot:C",full=True)
        self.assertNotIn("library_order",str(packet))
        self.replay(session)

    def test_trusted_delayed_blink_save_load_then_returns_once_and_replays(self):
        session=self.session(24400003);self.register(session)
        victim=self.add(session,"Generic Blink Victim","DELAY-VICTIM",owner="B",zone="battlefield")
        spell=self.add(session,"Generic Delayed Blink","DELAY")
        self.seal(session);old=victim.logical_object_id
        self.cast(session,spell,[victim.ref]);self.finish(session)
        self.assertEqual("exile",session.state.cards[victim.object_id].zone)
        delayed=[trigger for trigger in session.state.delayed_triggers if trigger.active]
        self.assertEqual(1,len(delayed));self.assertEqual("A",delayed[0].controller)
        exiled=session.state.cards[victim.object_id].logical_object_id
        self.assertNotEqual(old,exiled)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"pending";session.save(path)
            session=CommanderSession.load(self.db,path)
        self.advance(session,"ending","end_step");self.finish(session)
        returned=session.state.cards[victim.object_id]
        self.assertEqual(("battlefield","B"),(returned.zone,returned.controller))
        self.assertNotEqual(exiled,returned.logical_object_id)
        self.assertEqual(1,len(session.state.delayed_triggers))
        self.assertFalse(session.state.delayed_triggers[0].active)
        self.replay(session)

    def test_trusted_group_partial_illegal_target_replays(self):
        session=self.session(24400004);self.register(session)
        first=self.add(session,"Generic Blink Victim","FIRST",zone="battlefield")
        second=self.add(session,"Generic Blink Victim","SECOND",zone="battlefield")
        group=self.add(session,"Generic Group Blink","GROUP")
        destroy=self.add(session,"Generic Blink Destroy","DESTROY")
        self.seal(session);first_old=first.logical_object_id;second_old=second.logical_object_id
        self.cast(session,group,[first.ref,second.ref])
        self.cast(session,destroy,[first.ref]);self.finish(session)
        self.assertEqual("graveyard",session.state.cards[first.object_id].zone)
        self.assertEqual("battlefield",session.state.cards[second.object_id].zone)
        self.assertEqual(1,session.state.cards[first.object_id].zone_change_counter)
        self.assertEqual(2,session.state.cards[second.object_id].zone_change_counter)
        self.assertNotEqual(first_old,session.state.cards[first.object_id].logical_object_id)
        self.assertNotEqual(second_old,session.state.cards[second.object_id].logical_object_id)
        self.replay(session)

    def test_trusted_counter_entry_replacement_choice_checkpoint_resumes_return_only(self):
        session=self.session(24400005);self.register(session)
        victim=self.add(session,"Generic Blink Victim","COUNTER-VICTIM",zone="battlefield")
        doubler=self.add(session,"Generic Blink Counter Doubler","DOUBLE",zone="battlefield")
        addition=self.add(session,"Generic Blink Counter Addition","PLUS",zone="battlefield")
        spell=self.add(session,"Generic Counter Blink","COUNTER-BLINK")
        for source in (doubler,addition):
            self.assertTrue(all(session.engine.semantic_program_is_current_trusted(program) for program in session.engine.semantics.programs_for_oracle(source.oracle_id)))
        self.seal(session);self.cast(session,spell,[victim.ref])
        for _ in range(16):
            session.engine.pump()
            if session.state.pending_decision.kind=="replacement.order":break
            principal=session.pending_principals()[0]
            self.assertTrue(session.act(principal,{"action_id":"pass"}).ok)
        self.assertEqual("replacement.order",session.state.pending_decision.kind)
        self.assertEqual("exile",session.state.cards[victim.object_id].zone)
        exile_identity=session.state.cards[victim.object_id].logical_object_id
        self.assertEqual(1,session.state.cards[victim.object_id].zone_change_counter)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"choice";session.save(path)
            session=CommanderSession.load(self.db,path)
        choice=session.state.pending_decision
        principal=session.pending_principals()[0]
        self.assertEqual("pilot:A",principal)
        options=session.packet(principal,full=True)["decision"]["ctx"]["options"]
        selected=next(option["id"] for option in options if "PLUS" in option["id"])
        result=session.act(principal,{"action_id":"choose","replacement":selected})
        self.assertTrue(result.ok,result.summary)
        self.finish(session)
        returned=session.state.cards[victim.object_id]
        self.assertEqual("battlefield",returned.zone)
        self.assertEqual(4,returned.counters["+1/+1"])
        self.assertEqual(2,returned.zone_change_counter)
        self.assertNotEqual(exile_identity,returned.logical_object_id)
        self.replay(session)

    def test_trusted_self_activation_and_optional_entry_decline(self):
        session=self.session(24400006);self.register(session)
        source=self.add(session,"Generic Self Blink","SELF",zone="battlefield")
        victim=self.add(session,"Generic Blink Victim","OPTIONAL-VICTIM",zone="battlefield")
        entry=self.add(session,"Generic Optional Entry Blink","OPTIONAL")
        apply_entry=self.add(session,"Generic Optional Entry Blink","APPLY-ENTRY")
        self.seal(session)
        decision=session.packet("pilot:A",full=True)["decision"]
        action=next(action for action in decision["ctx"]["legal"]["actions"] if action.get("source")==source.ref and action["id"].startswith("activate:"))
        result=session.act("pilot:A",{"action_id":action["id"],"pay":"auto"})
        self.assertTrue(result.ok,result.summary);self.finish(session)
        self.assertEqual("exile",session.state.cards[source.object_id].zone)
        self.assertEqual(1,len([trigger for trigger in session.state.delayed_triggers if trigger.active]))
        self.cast(session,entry,())
        for _ in range(24):
            session.engine.pump();principal=session.pending_principals()[0]
            decision=session.packet(principal,full=True)["decision"]
            if decision["kind"]=="semantic.target":
                schema=decision["ctx"]["target_schema"]
                self.assertNotIn(entry.ref,schema["legal_refs"])
                result=session.act(principal,{"action_id":"choose","targets":{"target_0":[victim.ref]}})
            elif decision["kind"]=="semantic.choice":break
            else:
                self.assertEqual("priority",decision["kind"])
                result=session.act(principal,{"action_id":"pass"})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual("semantic.choice",session.state.pending_decision.kind)
        original=session.state.cards[victim.object_id].logical_object_id
        result=session.act("pilot:A",{"action_id":"choose","choice":"decline"})
        self.assertTrue(result.ok,result.summary);self.finish(session)
        self.assertEqual(original,session.state.cards[victim.object_id].logical_object_id)
        self.cast(session,apply_entry,())
        for _ in range(24):
            session.engine.pump();principal=session.pending_principals()[0]
            decision=session.packet(principal,full=True)["decision"]
            if decision["kind"]=="semantic.target":
                result=session.act(principal,{"action_id":"choose","targets":{"target_0":[victim.ref]}})
            elif decision["kind"]=="semantic.choice":break
            else:
                self.assertEqual("priority",decision["kind"])
                result=session.act(principal,{"action_id":"pass"})
            self.assertTrue(result.ok,result.summary)
        result=session.act("pilot:A",{"action_id":"choose","choice":"apply"})
        self.assertTrue(result.ok,result.summary);self.finish(session)
        self.assertNotEqual(original,session.state.cards[victim.object_id].logical_object_id)
        self.assertEqual("battlefield",session.state.cards[victim.object_id].zone)
        self.replay(session)

    def test_trusted_self_old_activation_ignores_absent_source_and_keyword_grants(self):
        session=self.session(24400007);self.register(session)
        source=self.add(session,"Generic Self Blink","STALE-SELF",zone="battlefield")
        bounce=self.add(session,"Generic Blink Bounce","BOUNCE")
        keyword=self.add(session,"Generic Keyword Blink","KEYWORD")
        victim=self.add(session,"Generic Blink Victim","KEYWORD-VICTIM",zone="battlefield")
        self.seal(session)
        action=next(action for action in session.packet("pilot:A",full=True)["decision"]["ctx"]["legal"]["actions"] if action.get("source")==source.ref and action["id"].startswith("activate:"))
        self.assertTrue(session.act("pilot:A",{"action_id":action["id"],"pay":"auto"}).ok)
        self.cast(session,bounce,[source.ref])
        # Resolve only the response, retaining the old source-pinned activation.
        for _ in range(16):
            if len(session.state.stack)==1:break
            session.engine.pump();principal=session.pending_principals()[0]
            self.assertTrue(session.act(principal,{"action_id":"pass"}).ok)
        self.assertEqual("hand",session.state.cards[source.object_id].zone)
        self.finish(session)
        self.assertFalse(session.state.delayed_triggers)
        self.cast(session,session.state.cards[source.object_id],());self.finish(session)
        new_identity=session.state.cards[source.object_id].logical_object_id
        self.assertEqual("battlefield",session.state.cards[source.object_id].zone)
        self.cast(session,keyword,[victim.ref]);self.finish(session)
        self.assertIn("First Strike",session.engine._effective_card_data(session.state.cards[victim.object_id])["keywords"])
        self.assertEqual(new_identity,session.state.cards[source.object_id].logical_object_id)
        self.replay(session)

    def test_trusted_zero_target_token_and_immediate_commander_blink(self):
        session=self.session(24400008);self.register(session)
        empty=self.add(session,"Generic Group Blink","EMPTY")
        token_spell=self.add(session,"Generic Immediate Blink","TOKEN-BLINK")
        commander_spell=self.add(session,"Generic Immediate Blink","COMMANDER-BLINK")
        token=self.add(session,"Generic Blink Victim","TOKEN",zone="battlefield")
        token.is_token=True;token.object_kind="token"
        commander=next(card for card in session.state.cards.values() if card.owner=="A" and card.zone=="command")
        session.engine.move_card(commander.object_id,"battlefield",controller="A",reason="generic checkpoint fixture")
        self.seal(session)
        self.cast(session,empty,());self.finish(session)
        self.assertEqual("graveyard",session.state.cards[empty.object_id].zone)
        self.cast(session,token_spell,[token.ref]);self.finish(session)
        self.assertNotEqual("battlefield",session.state.cards[token.object_id].zone)
        old=session.state.cards[commander.object_id].logical_object_id
        self.cast(session,commander_spell,[commander.ref]);self.finish(session)
        returned=session.state.cards[commander.object_id]
        self.assertEqual("battlefield",returned.zone)
        self.assertTrue(returned.is_commander)
        self.assertNotEqual(old,returned.logical_object_id)
        self.assertEqual(0,len([decision for decision in session.decisions if decision.get("kind")=="commander.zone"]))
        self.replay(session)

    def test_trusted_group_blink_commits_two_simultaneous_movements(self):
        session=self.session(24400009);self.register(session)
        first=self.add(session,"Generic Blink Victim","GROUP-FIRST",zone="battlefield")
        second=self.add(session,"Generic Blink Victim","GROUP-SECOND",owner="B",controller="A",zone="battlefield")
        spell=self.add(session,"Generic Group Blink","GROUP-BLINK")
        self.seal(session);self.cast(session,spell,[first.ref,second.ref]);self.finish(session)
        first=session.state.cards[first.object_id];second=session.state.cards[second.object_id]
        self.assertEqual(("A","B"),(first.controller,second.controller))
        self.assertEqual(2,first.zone_change_counter);self.assertEqual(2,second.zone_change_counter)
        self.assertEqual(first.zone_timestamp,second.zone_timestamp)
        departures=[event for event in session.state.turn_history.events if event.kind=="permanent_left"]
        self.assertEqual({first.logical_object_id.rsplit("@",1)[0],second.logical_object_id.rsplit("@",1)[0]}, {event.object_incarnation.rsplit("@",1)[0] for event in departures})
        self.replay(session)


if __name__ == "__main__":
    unittest.main()
