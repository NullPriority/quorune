from __future__ import annotations

"""CR 107.1b/107.2/113.7a/608.2h: bound scalar information, not printed constants.

Expected contract: current information applies while the same referenced object
remains in its expected public zone; otherwise use its LKI, never a new
incarnation. Trigger quantities retain the committed event's actual amount.
Negative ordinary effect amounts become zero before the result's printed sign.
Unavailable rules information remains unsupported, rather than guessed zero.
"""

import unittest
from types import SimpleNamespace
from copy import deepcopy
from unittest import mock
from pathlib import Path
import tempfile

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import SCALAR_REFERENCE_CONTEXT, ScalarAmountOrigin, ScalarEffectAmountSpec
from quorune.scalar_effect_amounts import resolve_scalar_effect_amount, scalar_source_context, pin_scalar_characteristic_departures
from quorune.query_effect_amount_model import PublicQueryAmountError
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import register_generated_programs
from scripts.build_test_database import build_fixture_database
from common import ROOT
import test_linked_exile_return as action_helpers
from quorune.record import authoritative_state_hash


def scalar_record(text: str, *, type_line: str = "Creature — Goblin") -> CardRecord:
    return CardRecord(
        oracle_id="fixture:scalar-effect-amount", name="Generic Scalar Fixture",
        mana_cost="{2}{G}", mana_value=3.0, type_line=type_line, oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="3" if "Creature" in type_line else None,
        loyalty=None, defense=None, colors=("G",), color_identity=("G",),
        keywords=(), produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander": "legal"}, faces=(), raw={},
    )


class ScalarEffectAmountCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, *, type_line="Creature — Goblin"):
        return compile_oracle_card(
            scalar_record(text, type_line=type_line), capability_registry=self.registry,
            capability_profile="commander_review",
        )

    def test_referenced_and_sealed_quantities_lower_through_existing_results(self):
        cases = (
            ("{G}: This creature gets +X/+X until end of turn, where X is its power.", "Creature — Goblin"),
            ("When this creature dies, draw cards equal to its power.", "Creature — Goblin"),
            ("Whenever another creature you control enters, you gain life equal to its toughness.", "Enchantment"),
            ("Target creature gets +X/+X until end of turn, where X is its power.", "Instant"),
            ("Whenever you gain life, target opponent loses that much life.", "Enchantment"),
            ("Whenever this creature is dealt damage, create that many 1/1 green Insect creature tokens with flying.", "Creature — Goblin"),
            ("{G}: Target opponent loses X life, where X is the amount of life you gained this turn.", "Artifact"),
        )
        for text, type_line in cases:
            with self.subTest(text=text):
                compiled = self.compile(text, type_line=type_line)
                self.assertEqual("exact", compiled.status)
                self.assertFalse(compiled.material_residuals)

    def test_open_ambiguous_and_cost_quantities_stay_residual(self):
        for text in (
            "Draw X cards, where X is the greatest power among creatures you control.",
            "Destroy target creature with mana value X, where X is this creature's power.",
            "Target creature gets +X/+X until end of turn, where X is the chosen creature's power.",
            "Draw that many cards.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", self.compile(text, type_line="Instant").status)
        self.assertNotEqual("exact", self.compile("When this creature enters, tap up to one target creature. You gain life equal to that creature's power.").status)

    def test_scalar_target_corpus_assurance_preserves_exact_source_and_shape(self):
        from quorune.compiler.target_effect_corpus_assurance import TargetEffectCorpusCollector
        from dataclasses import replace
        for text, type_line in (
            ("{G}: Target creature gets +X/+X until end of turn, where X is this creature's power.", "Creature — Goblin"),
            ("Target creature gets +X/+X until end of turn, where X is its power.", "Instant"),
        ):
            with self.subTest(text=text):
                record=scalar_record(text,type_line=type_line)
                ir=self.compile(text,type_line=type_line)
                self.assertEqual("exact",ir.status)
                collector=TargetEffectCorpusCollector()
                collector.observe(record,ir)
                # Altering the producer after lowering must still be rejected.
                node=ir.faces[0].nodes[0]
                changed=deepcopy(node.effects)
                changed[0]["power"]["characteristic"]="toughness"
                bad_face=replace(ir.faces[0],nodes=(replace(node,effects=changed),))
                with self.assertRaises(ValueError):
                    TargetEffectCorpusCollector().observe(record,replace(ir,faces=(bad_face,)))


class ScalarEffectAmountValueTests(unittest.TestCase):
    def test_discard_history_quantity_filters_locked_controller_and_rejects_unknown(self):
        from quorune.model import TurnHistory,TurnHistoryEvent
        history=TurnHistory(turn_sequence=1,events=[TurnHistoryEvent(kind='card_discarded',actor='A'),
            TurnHistoryEvent(kind='card_discarded',actor='B'),TurnHistoryEvent(kind='card_discarded',actor='A')])
        host=SimpleNamespace(state=SimpleNamespace(turn_history=history,turn_sequence=1))
        item=SimpleNamespace(controller='A',context={})
        spec=ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY,history_fact='cards_discarded')
        self.assertEqual(2,resolve_scalar_effect_amount(host,spec.to_dict(),item))
        item.controller='B';self.assertEqual(1,resolve_scalar_effect_amount(host,spec.to_dict(),item))
        host.state.turn_history=None
        with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(host,spec.to_dict(),item)

    def host_and_item(self, power=2):
        card = SimpleNamespace(object_id="source-id", logical_object_id="source:1", ref="SOURCE",
            zone="battlefield", controller="A", owner="A", phased_out=False, annotations={}, counters={})
        data = {"type_line":"Creature — Goblin", "power":str(power), "toughness":"3", "mana_value":3.0}
        spec = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, characteristic="power")
        effects = ({"op":"apply_source_characteristics_until_end_of_turn", "power":spec.to_dict(), "toughness":0},)
        item = SimpleNamespace(controller="A", ref="STACK", source_object_id=card.object_id,
            card_object_id=None, targets=[], semantic_key="program", context={"source_logical_object_id":card.logical_object_id})
        host = SimpleNamespace(state=SimpleNamespace(cards={card.object_id:card}, stack=[item]),
            _effective_card_data=lambda obj:data, _type_parts=lambda value:({"creature"}, {"goblin"}, set()),
            semantics=SimpleNamespace(get=lambda key:SimpleNamespace(effects=effects)))
        item.context.update(scalar_source_context(host,card,effects))
        return host, item, card, data, spec

    def reference_fixture(self, origin):
        host, item, card, data, _ = self.host_and_item()
        card.zone_change_counter = 0
        spec = ScalarEffectAmountSpec(origin, characteristic="power")
        effects = ({"op": "gain_life", "amount": spec.to_dict()},)
        host.semantics = SimpleNamespace(get=lambda _key: SimpleNamespace(effects=effects))
        if origin is ScalarAmountOrigin.TARGET:
            item.targets = [card.ref]
            item.context = {"target_snapshots": {card.ref: {"zone_change_counter": 0}}}
        elif origin is ScalarAmountOrigin.EVENT_OBJECT:
            item.context = {"event_context": {
                "card": card.ref, "card_object_identity": card.logical_object_id,
                "power": 2, "toughness": 3, "mana_value": 3.0,
            }}
        return host, item, card, data, spec

    def test_departure_pin_does_not_read_or_cache_unrelated_references(self):
        for origin in (ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.TARGET, ScalarAmountOrigin.EVENT_OBJECT):
            for group_size in (0, 1, 2):
                with self.subTest(origin=origin.value, group_size=group_size):
                    host, item, source, _, _ = self.reference_fixture(origin)
                    source.phased_out = True
                    unrelated = [SimpleNamespace(
                        object_id=f"other-{index}", ref=f"OTHER-{index}",
                        logical_object_id=f"other-{index}:1", zone="battlefield",
                    ) for index in range(group_size)]
                    host.state.cards.update((card.object_id, card) for card in unrelated)
                    before = deepcopy(item.context)
                    with mock.patch.object(host, "_effective_card_data") as evaluate:
                        pin_scalar_characteristic_departures(host, unrelated)
                    evaluate.assert_not_called()
                    self.assertEqual(before, item.context)

    def test_relevant_departures_pin_immediate_lki_for_each_reference_origin(self):
        for origin in (ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.TARGET, ScalarAmountOrigin.EVENT_OBJECT):
            with self.subTest(origin=origin.value):
                host, item, source, data, spec = self.reference_fixture(origin)
                data["power"] = "5"
                pin_scalar_characteristic_departures(host, [source])
                source.zone = "graveyard"
                source.logical_object_id = "source:2"
                source.zone_change_counter = 1
                data["power"] = "99"
                self.assertEqual(5, resolve_scalar_effect_amount(host, spec.to_dict(), item))

    def test_departed_and_reentered_references_do_not_pin_new_incarnations(self):
        for origin in (ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.TARGET, ScalarAmountOrigin.EVENT_OBJECT):
            for zone in ("graveyard", "battlefield"):
                with self.subTest(origin=origin.value, zone=zone):
                    host, item, source, _, spec = self.reference_fixture(origin)
                    # Capture the old incarnation, then construct its later state.
                    self.assertEqual(2, resolve_scalar_effect_amount(host, spec.to_dict(), item))
                    source.zone = zone
                    source.logical_object_id = "source:2"
                    source.zone_change_counter = 1
                    before = deepcopy(item.context)
                    with mock.patch.object(host, "_effective_card_data") as evaluate:
                        pin_scalar_characteristic_departures(host, [source])
                    evaluate.assert_not_called()
                    self.assertEqual(before, item.context)

    def test_multiple_pending_items_only_update_the_departing_reference(self):
        host, relevant, source, data, spec = self.reference_fixture(ScalarAmountOrigin.SOURCE)
        data["power"] = "5"
        unrelated_source = SimpleNamespace(
            object_id="unrelated-source", ref="UNRELATED-SOURCE", logical_object_id="unrelated:1",
            zone="battlefield", phased_out=True, annotations={}, counters={},
        )
        unrelated = SimpleNamespace(
            controller="B", ref="UNRELATED-STACK", source_object_id=unrelated_source.object_id,
            card_object_id=None, targets=[], semantic_key="program",
            context={"source_logical_object_id": unrelated_source.logical_object_id},
        )
        host.state.cards[unrelated_source.object_id] = unrelated_source
        host.state.stack.append(unrelated)
        before = deepcopy(unrelated.context)
        pin_scalar_characteristic_departures(host, [source])
        self.assertEqual(before, unrelated.context)
        self.assertEqual(5, relevant.context[SCALAR_REFERENCE_CONTEXT]["source"]["power"])

    def test_zone_owner_restores_all_contexts_after_relevant_pin_failure(self):
        from quorune.errors import StateInvariantError
        from quorune.zone_transitions import ZoneTransitionOwner

        host, item, source, data, _ = self.reference_fixture(ScalarAmountOrigin.SOURCE)
        data["power"] = "5"
        malformed = SimpleNamespace(
            controller="A", ref="MALFORMED-STACK", source_object_id=source.object_id,
            card_object_id=None, targets=[], semantic_key="program",
            context={"source_logical_object_id": source.logical_object_id, SCALAR_REFERENCE_CONTEXT: []},
        )
        host.state.stack.append(malformed)
        before = [deepcopy(pending.context) for pending in host.state.stack]
        with self.assertRaisesRegex(StateInvariantError, "continuation is malformed"):
            ZoneTransitionOwner(host).pin_characteristic_departures([source])
        self.assertEqual(before, [pending.context for pending in host.state.stack])

    def test_simultaneous_relevant_references_use_the_complete_pre_event_state(self):
        host, first, source, _, _ = self.reference_fixture(ScalarAmountOrigin.SOURCE)
        second_source = SimpleNamespace(
            object_id="second-source", ref="SECOND-SOURCE", logical_object_id="second:1",
            zone="battlefield", phased_out=False, annotations={}, counters={},
        )
        second = SimpleNamespace(
            controller="B", ref="SECOND-STACK", source_object_id=second_source.object_id,
            card_object_id=None, targets=[], semantic_key="program",
            context={"source_logical_object_id": second_source.logical_object_id},
        )
        host.state.cards[second_source.object_id] = second_source
        host.state.stack.append(second)

        def current_characteristics(card):
            self.assertTrue(all(member.zone == "battlefield" for member in (source, second_source)))
            return {"type_line": "Creature — Goblin", "power": "5" if card is source else "7",
                    "toughness": "3", "mana_value": 3.0}

        host._effective_card_data = current_characteristics
        pin_scalar_characteristic_departures(host, [source, second_source])
        self.assertEqual(5, first.context[SCALAR_REFERENCE_CONTEXT]["source"]["power"])
        self.assertEqual(7, second.context[SCALAR_REFERENCE_CONTEXT]["source"]["power"])

    def test_unrelated_departure_relevance_mutant_is_killed(self):
        host, item, source, _, _ = self.reference_fixture(ScalarAmountOrigin.SOURCE)
        source.phased_out = True
        unrelated = SimpleNamespace(
            object_id="unrelated", ref="UNRELATED", logical_object_id="unrelated:1", zone="battlefield",
        )
        host.state.cards[unrelated.object_id] = unrelated
        with mock.patch("quorune.scalar_effect_amounts._reference_identity",
                        return_value=(unrelated.object_id, unrelated.logical_object_id, "battlefield")):
            with self.assertRaisesRegex(PublicQueryAmountError, "phased out"):
                pin_scalar_characteristic_departures(host, [unrelated])

    def test_own_phased_reference_remains_explicitly_unavailable(self):
        for origin in (ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.TARGET, ScalarAmountOrigin.EVENT_OBJECT):
            with self.subTest(origin=origin.value):
                host, item, source, _, spec = self.reference_fixture(origin)
                source.phased_out = True
                with self.assertRaisesRegex(PublicQueryAmountError, "phased out"):
                    resolve_scalar_effect_amount(host, spec.to_dict(), item)

    def test_current_then_immediate_departure_lki_never_reads_new_incarnation(self):
        host,item,card,data,spec = self.host_and_item()
        data["power"]="5"
        self.assertEqual(5,resolve_scalar_effect_amount(host,spec.to_dict(),item))
        pin_scalar_characteristic_departures(host,[card])
        card.logical_object_id="source:2"
        data["power"]="99"
        self.assertEqual(5,resolve_scalar_effect_amount(host,spec.to_dict(),item))

    def test_negative_ordinary_amount_zero_and_signed_result_coefficients(self):
        host,item,card,data,spec = self.host_and_item(-2)
        self.assertEqual(0,resolve_scalar_effect_amount(host,spec.to_dict(),item))
        negative = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, characteristic="power", coefficient=-1)
        self.assertEqual(0,resolve_scalar_effect_amount(host,negative.to_dict(),item))
        data["power"]="4"
        self.assertEqual(-4,resolve_scalar_effect_amount(host,negative.to_dict(),item))

    def test_declaration_freezes_once_but_independent_reads_remain_live(self):
        host,item,card,data,spec = self.host_and_item()
        declared = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, characteristic="power", binding_id="instruction:x")
        self.assertEqual(2,resolve_scalar_effect_amount(host,declared.to_dict(),item))
        data["power"]="7"
        self.assertEqual(2,resolve_scalar_effect_amount(host,declared.to_dict(),item))
        self.assertEqual(7,resolve_scalar_effect_amount(host,spec.to_dict(),item))
        saved = deepcopy(item.context)
        item.context=deepcopy(saved)
        self.assertEqual(2,resolve_scalar_effect_amount(host,declared.to_dict(),item))

    def test_committed_event_amount_is_not_current_life_or_damage_request(self):
        host,item,card,data,spec = self.host_and_item()
        item.context["amount"]=3
        event = ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT)
        self.assertEqual(3,resolve_scalar_effect_amount(host,event.to_dict(),item))
        item.context["amount"]=True
        with self.assertRaises(PublicQueryAmountError):
            resolve_scalar_effect_amount(host,event.to_dict(),item)

    def test_malformed_descriptor_and_unavailable_characteristics_fail_closed(self):
        host,item,card,data,spec = self.host_and_item()
        for changes in ({"schema_version":True},{"coefficient":True},{"origin":"unknown"},{"extra":1}):
            with self.subTest(changes=changes),self.assertRaises(PublicQueryAmountError):
                resolve_scalar_effect_amount(host,{**spec.to_dict(),**changes},item)
        data["power"]="*"
        with self.assertRaises(PublicQueryAmountError):
            resolve_scalar_effect_amount(host,spec.to_dict(),item)

    def test_reentered_current_object_mutation_is_killed(self):
        def read_new_object(host,item,spec):
            from quorune.scalar_effect_amounts import _snapshot
            return _snapshot(host,host.state.cards[item.source_object_id])
        case = ScalarEffectAmountValueTests("test_current_then_immediate_departure_lki_never_reads_new_incarnation")
        with mock.patch("quorune.scalar_effect_amounts._reference",side_effect=read_new_object):
            with self.assertRaises(AssertionError):
                case.test_current_then_immediate_departure_lki_never_reads_new_incarnation()


class ScalarEffectAmountActionTests(unittest.TestCase):
    session = action_helpers.LinkedExileReturnActionTests.session
    add = action_helpers.LinkedExileReturnActionTests.add
    seal = action_helpers.LinkedExileReturnActionTests.seal
    cast = action_helpers.LinkedExileReturnActionTests.cast
    finish = action_helpers.LinkedExileReturnActionTests.finish
    replay = action_helpers.LinkedExileReturnActionTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "scalar.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-exile-return-cards.json",
                               ROOT / "tests/fixtures/scalar-effect-amount-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.deck = DeckDefinition("Generic scalar",[DeckEntry("Generic Blink Commander",1,"commander"),
            DeckEntry("Generic Blink Plains",15)],["Generic Blink Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def register(self, session):
        register_generated_programs(self.db,session.engine.semantics,
            tuple(record for record in self.db.iter_cards() if record.oracle_text),
            trust_level="provisional",capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True)

    def activate(self, session, source, targets=()):
        session.engine.pump()
        decision = session.packet("pilot:A",full=True)["decision"]
        action = next(action for action in decision["ctx"]["legal"]["actions"]
            if action.get("source")==source.ref and action["id"].startswith("activate:"))
        result = session.act("pilot:A",{"action_id":action["id"],"targets":list(targets),"pay":"auto"})
        self.assertTrue(result.ok,result.summary)

    def test_unrelated_phased_scalar_does_not_reject_actual_destroy_and_replays(self):
        """Real commands from a constructed checkpoint, not a phasing action.

        Phasing transitions remain unsupported. The pending ability is created
        through its actual registered activation before the checkpoint setup.
        Only the later, unrelated movement is claimed as supported behavior.
        """
        session = self.session(24500120)
        self.register(session)
        source = self.add(session, "Generic Scalar Source Life", "UNRELATED-SCALAR", zone="battlefield")
        victim = self.add(session, "Generic Blink Victim", "UNRELATED-VICTIM", zone="battlefield")
        spell = self.add(session, "Generic Blink Destroy", "UNRELATED-DESTROY")
        self.activate(session, source)
        self.assertEqual(1, len(session.state.stack))
        source.phased_out = True
        # The checkpoint explicitly contains this constructed phasing state;
        # no unrecorded mutation occurs after the unrelated action starts.
        self.seal(session)
        scalar_context = deepcopy(session.state.stack[0].context)
        self.cast(session, spell, [victim.ref])
        for _ in range(20):
            if session.state.cards[victim.object_id].zone == "graveyard":
                break
            session.engine.pump()
            principal = session.pending_principals()[0]
            decision = session.packet(principal, full=True)["decision"]
            self.assertEqual("priority", decision["kind"])
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[victim.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.assertEqual(1, len(session.state.stack))
        self.assertEqual(scalar_context, session.state.stack[0].context)
        self.assertTrue(session.state.cards[source.object_id].phased_out)
        self.assertIsNone(session.engine._semantic_pause_annotation())
        self.replay(session)

    def _unrelated_scalar_movement_checkpoint(self, seed, spell_name, victim_count):
        """Create a real pending activation plus labeled unsupported-state setup."""
        session = self.session(seed)
        self.register(session)
        source = self.add(session, "Generic Scalar Source Life", "PENDING-SCALAR", zone="battlefield")
        victims = [self.add(session, "Generic Blink Victim", f"MOVING-{index}", zone="battlefield")
                   for index in range(victim_count)]
        spell = self.add(session, spell_name, "MOVEMENT-SPELL")
        self.activate(session, source)
        source.phased_out = True
        self.seal(session)
        context = deepcopy(session.state.stack[0].context)
        self.cast(session, spell, [card.ref for card in victims])
        for _ in range(20):
            if len(session.state.stack) == 1:
                break
            session.engine.pump()
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(session.state.stack))
        self.assertEqual(context, session.state.stack[0].context)
        self.assertTrue(session.state.cards[source.object_id].phased_out)
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.assertIsNone(session.engine._semantic_pause_annotation())
        return session, victims

    def test_unrelated_phased_scalar_preserves_actual_single_return_and_replay(self):
        session, victims = self._unrelated_scalar_movement_checkpoint(
            24500121, "Generic Blink Bounce", 1,
        )
        self.assertEqual("hand", session.state.cards[victims[0].object_id].zone)
        self.replay(session)

    def test_unrelated_phased_scalar_preserves_actual_group_blink_and_replay(self):
        session, victims = self._unrelated_scalar_movement_checkpoint(
            24500122, "Generic Group Blink", 2,
        )
        for victim in victims:
            current = session.state.cards[victim.object_id]
            self.assertEqual("battlefield", current.zone)
            self.assertEqual(2, current.zone_change_counter)
        self.replay(session)

    def finish_with_targets(self, session, chosen="B"):
        for _ in range(50):
            if not session.state.stack and not session.state.pending_trigger_batches:
                return
            session.engine.pump()
            principal=session.pending_principals()[0]
            decision=session.packet(principal,full=True)["decision"]
            if decision["kind"]=="semantic.target":
                response={"action_id":"choose","targets":{"target_0":[chosen]}}
            else:
                self.assertEqual("priority",decision["kind"])
                response={"action_id":"pass"}
            result=session.act(principal,response)
            self.assertTrue(result.ok,result.summary)
        self.fail("Scalar fixture did not finish")

    def test_trusted_scalar_actions_controller_privacy_and_replay(self):
        session = self.session(24500001)
        self.register(session)
        source = self.add(session,"Generic Scalar Self","SCALAR-SOURCE",zone="battlefield")
        spell = self.add(session,"Generic Scalar Target","SCALAR-SPELL")
        self.seal(session)
        self.activate(session,source)
        self.finish(session)
        self.assertEqual(4,session.engine._numeric_stat(source.object_id,"power"))
        before=authoritative_state_hash(session.state)
        land=next(card for card in session.state.cards.values() if card.owner=="A" and card.printed_name=="Generic Blink Plains")
        rejected=session.act("pilot:A",{"action_id":"cast:"+spell.ref,"targets":[land.ref],"pay":"auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,spell,[source.ref])
        self.finish(session)
        self.assertEqual(8,session.engine._numeric_stat(source.object_id,"power"))
        for seat in "BCD":
            packet = session.packet("pilot:"+seat,full=True)
            self.assertNotIn("authoritative_checkpoint",packet)
        self.replay(session)

    def test_trusted_scalar_source_departure_reentry_and_replay(self):
        session = self.session(24500002)
        self.register(session)
        source = self.add(session,"Generic Scalar Source Life","SCALAR-LKI",zone="battlefield")
        response = self.add(session,"Generic Immediate Blink","SCALAR-BLINK")
        growth = self.add(session,"Generic Scalar Target","SCALAR-GROWTH")
        self.seal(session)
        before_life = session.state.players["A"].life
        self.activate(session,source)
        old_identity = source.logical_object_id
        self.cast(session,growth,[source.ref])
        for _ in range(20):
            if len(session.state.stack)==1:
                break
            session.engine.pump()
            principal=session.pending_principals()[0]
            result=session.act(principal,{"action_id":"pass"})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual(4,session.engine._numeric_stat(source.object_id,"power"))
        self.cast(session,response,[source.ref])
        for _ in range(20):
            if len(session.state.stack)==1:
                break
            session.engine.pump()
            principal=session.pending_principals()[0]
            result=session.act(principal,{"action_id":"pass"})
            self.assertTrue(result.ok,result.summary)
        self.assertNotEqual(old_identity,session.state.cards[source.object_id].logical_object_id)
        self.finish(session)
        self.assertEqual(before_life+4,session.state.players["A"].life)
        self.replay(session)

    def test_trusted_committed_damage_amount_after_prevention_and_replay(self):
        session=self.session(24500003)
        self.register(session)
        observer=self.add(session,"Generic Scalar Damage Observer","DAMAGE-OBSERVER",zone="battlefield")
        prevention=self.add(session,"Generic Scalar Prevention","PREVENT")
        damage=self.add(session,"Generic Scalar Damage","DAMAGE")
        self.seal(session)
        self.cast(session,prevention,[observer.ref])
        self.finish(session)
        self.cast(session,damage,[observer.ref])
        self.finish(session)
        tokens=[card for card in session.state.cards.values() if card.is_token and card.zone=="battlefield" and card.owner=="A"]
        self.assertEqual(2,len(tokens))
        self.assertEqual(2,session.state.cards[observer.object_id].marked_damage)
        self.replay(session)

    def test_trusted_zone_event_object_then_live_current_characteristics_replay(self):
        session=self.session(24500004)
        self.register(session)
        observer=self.add(session,"Generic Scalar Entry Observer","ENTRY-OBSERVER",zone="battlefield")
        entrant=self.add(session,"Generic Scalar Self","ENTRANT")
        growth=self.add(session,"Generic Scalar Target","ENTRY-GROWTH")
        self.seal(session)
        before=session.state.players["A"].life
        self.cast(session,entrant,())
        for _ in range(20):
            if session.state.stack and session.state.stack[-1].kind=="triggered_ability":
                break
            session.engine.pump()
            principal=session.pending_principals()[0]
            self.assertTrue(session.act(principal,{"action_id":"pass"}).ok)
        self.assertEqual("battlefield",session.state.cards[entrant.object_id].zone)
        self.cast(session,growth,[entrant.ref])
        self.finish(session)
        self.assertEqual(before+5,session.state.players["A"].life)
        self.replay(session)

    def test_trusted_life_event_and_public_history_receiver_not_life_total(self):
        session=self.session(24500005)
        self.register(session)
        source=self.add(session,"Generic Scalar Source Life","GAIN-SOURCE",zone="battlefield")
        observer=self.add(session,"Generic Scalar Life Observer","LIFE-OBSERVER",zone="battlefield")
        history=self.add(session,"Generic Scalar History","HISTORY-SOURCE",zone="battlefield")
        self.seal(session)
        before={seat:player.life for seat,player in session.state.players.items()}
        self.activate(session,source)
        self.finish_with_targets(session,"B")
        self.assertEqual(before["A"]+2,session.state.players["A"].life)
        self.assertEqual(before["B"]-2,session.state.players["B"].life)
        self.activate(session,history,["C"])
        self.finish(session)
        self.assertEqual(before["C"]-2,session.state.players["C"].life)
        self.replay(session)

    def test_trusted_self_death_reads_departure_characteristics_and_replays(self):
        session=self.session(24500006)
        self.register(session)
        source=self.add(session,"Generic Scalar Death Draw","DEATH-DRAW",zone="battlefield")
        growth=self.add(session,"Generic Scalar Target","DEATH-GROWTH")
        destroy=self.add(session,"Generic Blink Destroy","DEATH-DESTROY")
        self.seal(session)
        self.cast(session,growth,[source.ref])
        self.finish(session)
        self.assertEqual(4,session.engine._numeric_stat(source.object_id,"power"))
        before=len(session.state.players["A"].zones["hand"])
        self.cast(session,destroy,[source.ref])
        self.finish(session)
        self.assertEqual("graveyard",session.state.cards[source.object_id].zone)
        self.assertEqual(before-1+4,len(session.state.players["A"].zones["hand"]))
        self.replay(session)


if __name__ == "__main__":
    unittest.main()
