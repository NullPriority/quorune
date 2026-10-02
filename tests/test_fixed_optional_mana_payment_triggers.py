from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler.optional_payment_templates import (
    FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
    FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC,
    OPTIONAL_MANA_PAYMENT_OPERATION,
)
from quorune.model import StackItem
from quorune.fixed_effect_payment import FixedEffectPaymentSpec
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_choices import (
    SemanticChoiceContext,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoiceFrame,
    SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.payments import PAYMENT_CHOICE_HANDLERS
from quorune.semantics import SemanticProgram


def payment_record(
    text: str,
    *,
    name: str = "Fixed Payment Witness",
    type_line: str = "Creature — Fixture",
) -> CardRecord:
    return CardRecord(
        oracle_id="fixture:fixed-optional-mana-payment",
        name=name,
        mana_cost="{1}{G}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def payment_handler():
    return next(
        handler
        for handler in PAYMENT_CHOICE_HANDLERS
        if handler.operation == OPTIONAL_MANA_PAYMENT_OPERATION
    )


def payment_query(*, payable: bool = True) -> SnapshotSemanticChoiceQuery:
    requirements = {
        "GENERIC": 0,
        "W": 0,
        "U": 0,
        "B": 0,
        "R": 0,
        "G": 1,
        "C": 0,
    }
    key = SnapshotSemanticChoiceQuery._cost_key("A", requirements)
    return SnapshotSemanticChoiceQuery(
        seat_order=("A", "B", "C", "D"),
        active_order=("A", "B", "C", "D"),
        affordable_costs=frozenset({key} if payable else ()),
    )


def payment_context(*, query=None) -> SemanticChoiceContext:
    return SemanticChoiceContext(
        actor="A",
        stack_ref="S-fixed-payment",
        stack_controller="A",
        stack_label="Fixed payment witness",
        source_ref="source-fixed-payment",
        card_ref=None,
        semantic_program_id="test:fixed-payment",
        semantic_program_version=1,
        query=query or payment_query(),
    )


def payment_continuation(effect) -> SemanticChoiceContinuation:
    return SemanticChoiceContinuation(
        handler_id="choice.payment.optional-fixed-effect.v1",
        handler_version=1,
        stack_ref="S-fixed-payment",
        effect=effect,
        remaining=(),
        destination=None,
        note="Fixed payment witness",
        semantic_frame=SemanticChoiceFrame(
            semantic_program_id="test:fixed-payment",
            semantic_program_version=1,
            stack_object="S-fixed-payment",
            instruction_pointer=0,
            controller="A",
        ),
    )


class FixedOptionalManaPaymentCompilerTests(unittest.TestCase):
    def test_fixed_payment_interaction_evidence_obeys_current_pair_contract(self):
        import json
        from common import ROOT
        from quorune.reusable_pieces.interactions import validate_interaction_evidence
        value=json.loads((ROOT/'platform/reusable-piece-interaction-evidence.json').read_text(encoding='utf-8'))
        selected=[row for row in value['declarations']if row['test_id']in {
            'test_payment_replacement_choice_resumes_once_after_save_load_and_replays',
            'test_trusted_etb_sacrifice_uses_locked_trigger_controller_and_replays',
            'test_fixed_payment_with_unrepresented_prevention_fails_closed',
        }]
        self.assertEqual(3,len(selected))
        validate_interaction_evidence({'schema_version':2,'declarations':selected})

    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()

    def compile(self, text: str, *, name: str = "Fixed Payment Witness"):
        return compile_oracle_card(
            payment_record(text, name=name),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_payment_composes_across_normalized_trigger_families(self):
        fixtures = (
            (
                "Whenever you cast a creature spell, you may pay {G}. If you "
                "do, draw a card.",
                "draw",
            ),
            (
                "At the beginning of your upkeep, you may pay {2}. If you do, "
                "gain 2 life.",
                "life",
            ),
            (
                "When this creature enters, you may pay {1}. If you do, create "
                "a Treasure token.",
                "create_token",
            ),
            (
                "Whenever this creature attacks, you may pay {R}. If you do, "
                "target creature can't block this turn.",
                "grant_declaration_restriction_until_end_of_turn",
            ),
            (
                "Whenever this creature deals combat damage to a player, you "
                "may pay {U}. If you do, scry 1.",
                "scry",
            ),
            (
                "When this artifact is put into a graveyard from the "
                "battlefield, you may pay {1}{B}. If you do, return target card "
                "from your graveyard to your hand.",
                "return_graveyard_card_to_owner_hand",
            ),
        )
        for text, nested_operation in fixtures:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual("triggered_ability", node.kind)
                wrapper = node.effects[0]
                self.assertEqual(OPTIONAL_MANA_PAYMENT_OPERATION, wrapper["op"])
                self.assertEqual("$controller", wrapper["player"])
                self.assertEqual(nested_operation, wrapper["effects"][0]["op"])
                self.assertTrue(any(wrapper["cost"].values()))
                self.assertIn(
                    FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
                    node.capability_dependencies,
                )
                self.assertIn(
                    FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC,
                    node.mechanics,
                )

    def test_lifecrafters_bestiary_is_an_exact_real_card_witness(self):
        ir = self.compile(
            "At the beginning of your upkeep, scry 1.\n"
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "draw a card.",
            name="Lifecrafter's Bestiary",
        )

        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(2, len(ir.faces[0].nodes))
        wrapper = ir.faces[0].nodes[1].effects[0]
        self.assertEqual(OPTIONAL_MANA_PAYMENT_OPERATION, wrapper["op"])
        self.assertEqual(1, wrapper["cost"]["G"])

    def test_fixed_resolution_payments_share_cost_result_and_carrier_owners(self):
        cases=(
            ('You may discard a card. If you do, draw two cards.','Instant','discard'),
            ('{1}: You may sacrifice a land. If you do, draw a card.','Artifact','sacrifice'),
            ('Whenever you cast a creature spell, you may pay 2 life. If you do, draw a card.','Creature — Fixture','life'),
            ('At the beginning of your upkeep, you may pay {2}. If you do, draw a card and you lose 2 life.','Enchantment','mana'),
        )
        for text,type_line,kind in cases:
            with self.subTest(text=text):
                ir=compile_oracle_card(payment_record(text,type_line=type_line),capability_registry=self.capabilities,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.to_dict())
                node=ir.faces[0].nodes[0]
                self.assertEqual(2,node.effects[0]['schema_version'])
                self.assertEqual(kind,node.effects[0]['payment']['kind'])
                self.assertIn(FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,node.capability_dependencies)
                self.assertEqual(text,ir.faces[0].oracle_text[node.span.start:node.span.end])

    def test_fixed_resolution_payment_rejects_partial_nested_and_linked_grammar(self):
        cases=(
            'You may discard a card at random. If you do, draw a card.',
            'You may discard any number of cards. If you do, draw a card.',
            'You may pay {X}. If you do, draw a card.',
            'You may sacrifice a creature or discard a card. If you do, draw a card.',
            'You may discard a card. When you do, draw a card.',
            'You may discard a card. If you do, draw cards equal to its mana value.',
            'You may discard a card. If you do, you may pay {1}. If you do, draw a card.',
        )
        for text in cases:
            with self.subTest(text=text):
                ir=compile_oracle_card(payment_record(text,type_line='Instant'),capability_registry=self.capabilities,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_payment_shape_and_dependency_mutants_fail_closed(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        import json
        from common import ROOT
        text='You may discard a card. If you do, tap target creature.'
        ir=compile_oracle_card(payment_record(text,type_line='Instant'),capability_registry=self.capabilities,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.to_dict())
        node=ir.faces[0].nodes[0]
        self.assertIn(FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,capability_dependencies_for_node(effects=node.effects,target_schema=node.target_schema,mechanic_ids=node.mechanics))
        for path,value in ((('schema_version',),True),(('payment','amount'),True),(('payment','predicate','owner'),'$opponent'),(('effects',0,'unexpected'),True)):
            wrapper=deepcopy(dict(node.effects[0]));cursor=wrapper
            for key in path[:-1]:cursor=cursor[key]
            cursor[path[-1]]=value
            self.assertNotIn(FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,capability_dependencies_for_node(effects=(wrapper,),target_schema=node.target_schema,mechanic_ids=node.mechanics))
        self.assertNotIn(FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,capability_dependencies_for_node(effects=node.effects,target_schema=None,mechanic_ids=node.mechanics))
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        capability=next(row for row in data['capabilities']if row['id']=='choice.affected_player.fixed_discard')
        capability['status']='blocked';capability['blockers']=['Constructed cost dependency diagnostic']
        blocked=compile_oracle_card(payment_record(text,type_line='Instant'),capability_registry=CapabilityRegistry(data),capability_profile='commander_review')
        self.assertNotEqual('exact',blocked.status);self.assertTrue(blocked.material_residuals)

    def test_fixed_payment_with_unrepresented_prevention_fails_closed(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        generic=payment_record('{1}: You may discard a card. If you do, draw two cards.\nPrevent all damage that would be dealt to this permanent by red spells.',type_line='Artifact')
        ir=compile_oracle_card(generic,capability_registry=self.capabilities,capability_profile='commander_review')
        self.assertTrue(ir.faces[0].nodes[0].exact);self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
        class Rulings:
            def rulings(self,card):return ()
        program=compile_best_available_card_program(Rulings(),generic,semantic_registry=SemanticRegistry(),capability_registry=self.capabilities,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.capabilities,profile='commander_review')
        self.assertFalse(binding['strict_capability_ready'],binding)
        self.assertFalse(binding['compatible_ready'],binding)

    def test_nonfixed_nested_and_nonexact_forms_remain_residual(self):
        fixtures = (
            "Whenever you cast a creature spell, you may pay {X}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G/U}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G/P}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {S}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {0}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay X life. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G}. When you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "you may draw a card.",
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "draw a card, then discard that card.",
        )
        for text in fixtures:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_variable_payment_remains_residual_in_spell_and_activated_contexts(self):
        fixtures = (
            (
                "You may pay {X}. If you do, draw a card.",
                "Instant",
            ),
            (
                "{T}: You may pay {X}. If you do, draw a card.",
                "Artifact",
            ),
        )
        for text, type_line in fixtures:
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    payment_record(text, type_line=type_line),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_compiler_shape_and_handler_mutants_fail_closed(self):
        text = (
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "draw a card."
        )
        ir = self.compile(text)
        node = ir.faces[0].nodes[0]
        wrapper = dict(node.effects[0])
        nested = dict(wrapper["effects"][0])
        mutants = (
            ({**wrapper, "player": "$source.controller"}, node.target_schema),
            ({**wrapper, "unexpected": True}, node.target_schema),
            ({**wrapper, "cost": {"G": 1}}, node.target_schema),
            ({**wrapper, "cost": {**wrapper["cost"], "G": -1}}, node.target_schema),
            ({**wrapper, "cost": {key: 0 for key in wrapper["cost"]}}, node.target_schema),
            ({**wrapper, "effects": []}, node.target_schema),
            ({**wrapper, "effects": [{"op": "unknown"}]}, node.target_schema),
            (
                {
                    **wrapper,
                    "effects": [
                        {
                            "op": OPTIONAL_MANA_PAYMENT_OPERATION,
                            "player": "$controller",
                            "cost": wrapper["cost"],
                            "effects": [nested],
                        }
                    ],
                },
                node.target_schema,
            ),
        )
        for effect, schema in mutants:
            with self.subTest(effect=effect):
                self.assertNotIn(
                    FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=schema,
                        mechanic_ids=node.mechanics,
                    ),
                )
        self.assertNotIn(
            FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
            capability_dependencies_for_node(
                effects=node.effects,
                target_schema=node.target_schema,
                mechanic_ids=tuple(
                    mechanic
                    for mechanic in node.mechanics
                    if mechanic != FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC
                ),
            ),
        )
        with mock.patch(
            "quorune.compiler.optional_payment_templates.fixed_optional_mana_payment_template", return_value=None,
        ), mock.patch(
            "quorune.compiler.effect_template_composition.fixed_optional_mana_payment_template", return_value=None,
        ), mock.patch(
            "quorune.compiler.effect_template_composition.fixed_effect_payment_template", return_value=None,
        ), mock.patch(
            "quorune.compiler.effect_template_composition.fixed_effect_payment_with_mandatory_prefix", return_value=None,
        ):
            mutant = self.compile(text)
        self.assertNotEqual("exact", mutant.status)


class FixedOptionalManaPaymentChoiceTests(unittest.TestCase):
    def test_v2_sacrifice_uses_current_controller_and_closed_characteristics(self):
        from dataclasses import replace
        row = ObjectQueryResult(
            object_id='land', ref='LAND', printed_name='Generic land', owner='B',
            controller='A', zone='battlefield', types=('land',), logical_object_id='land:1',
        )
        other = replace(row, object_id='other', ref='OTHER', owner='A', controller='B')
        effect = {
            'op': OPTIONAL_MANA_PAYMENT_OPERATION, 'schema_version': 2, 'player': 'A',
            'payment': FixedEffectPaymentSpec('sacrifice', predicate=ObjectQuerySpec(
                zones=('battlefield',), controller='A', types_all=('land',),
            )).to_dict(),
            'effects': [{'op': 'draw', 'player': 'A', 'count': 1, 'private': True}],
        }
        query = SnapshotSemanticChoiceQuery(seat_order=('A', 'B'), active_order=('A', 'B'), object_rows=(row, other))
        prepared = payment_handler().prepare(effect, payment_context(query=query))
        self.assertEqual(('LAND',), prepared.request.choice.legal_refs)
        self.assertEqual('public', prepared.request.choice.visibility)
        completion = payment_handler().complete(payment_continuation(prepared.continuation_effect), {'cards': ['LAND']}, query)
        self.assertEqual('sacrifice', completion.intents[0].transition_kind.value)
        self.assertTrue(completion.intents[0].controlled_only)
        self.assertFalse(completion.intents[0].owned_only)
        for changed in (replace(row, controller='B'), replace(row, types=('creature',)), replace(row, logical_object_id='land:2')):
            stale = SnapshotSemanticChoiceQuery(seat_order=query.seats, active_order=query.active_seats, object_rows=(changed, other))
            with self.assertRaisesRegex(SemanticChoiceError, 'stale'):
                payment_handler().complete(payment_continuation(prepared.continuation_effect), {'cards': ['LAND']}, stale)

    def test_v2_malformed_object_choices_fail_before_intent_creation(self):
        row = ObjectQueryResult(object_id='card', ref='CARD', printed_name='Generic card', owner='A', controller='A', zone='hand', logical_object_id='card:1')
        query = SnapshotSemanticChoiceQuery(seat_order=('A', 'B'), active_order=('A', 'B'), object_rows=(row,))
        effect = {
            'op': OPTIONAL_MANA_PAYMENT_OPERATION, 'schema_version': 2, 'player': 'A',
            'payment': FixedEffectPaymentSpec('discard', predicate=ObjectQuerySpec(zones=('hand',), owner='A', known_to_actor=True)).to_dict(),
            'effects': [{'op': 'draw', 'player': 'A', 'count': 1, 'private': True}],
        }
        prepared = payment_handler().prepare(effect, payment_context(query=query))
        for response in ({'cards': [{}]}, {'cards': [True]}, {'cards': 'CARD'}, {'cards': ['CARD'], 'pay': 1}, {'cards': ['CARD'], 'unexpected': True}):
            with self.subTest(response=response), self.assertRaises(SemanticChoiceError):
                payment_handler().complete(payment_continuation(prepared.continuation_effect), response, query)

    def test_v2_payment_candidates_are_independent_of_checkpoint_object_order(self):
        rows = tuple(
            ObjectQueryResult(
                object_id=ref, ref=ref, printed_name='Generic payment card',
                owner='A', controller='A', zone='hand', logical_object_id=ref + ':1',
            )
            for ref in ('A02', 'A01')
        )
        payment = FixedEffectPaymentSpec(
            'discard', predicate=ObjectQuerySpec(zones=('hand',), owner='A', known_to_actor=True),
        )
        effect = {
            'op': OPTIONAL_MANA_PAYMENT_OPERATION, 'schema_version': 2, 'player': 'A',
            'payment': payment.to_dict(),
            'effects': [{'op': 'draw', 'player': 'A', 'count': 1, 'private': True}],
        }
        preparations = []
        for ordered_rows in (rows, tuple(reversed(rows))):
            query = SnapshotSemanticChoiceQuery(
                seat_order=('A', 'B'), active_order=('A', 'B'), object_rows=ordered_rows,
            )
            preparations.append(payment_handler().prepare(effect, payment_context(query=query)))
        self.assertEqual(preparations[0].continuation_effect, preparations[1].continuation_effect)
        self.assertEqual(preparations[0].request, preparations[1].request)
        self.assertEqual(('A01', 'A02'), preparations[0].request.choice.legal_refs)

    def test_v2_payment_cost_before_consequence_mutant_is_killed(self):
        from quorune.semantic_choices import fixed_effect_payment as owner
        original=owner.complete_fixed_effect_payment
        def omit_cost(continuation,response,query):
            value=original(continuation,response,query)
            from quorune.semantic_choices.model import SemanticChoiceCompletion
            return SemanticChoiceCompletion(prepend_effects=value.prepend_effects)
        with mock.patch.object(owner,'complete_fixed_effect_payment',omit_cost):
            with self.assertRaises((AssertionError,IndexError)):
                self.test_v2_zone_payment_choice_is_private_complete_and_identity_pinned()

    def test_v2_zone_payment_choice_is_private_complete_and_identity_pinned(self):
        row=ObjectQueryResult(object_id='owned',ref='OWNED',printed_name='Generic owned card',owner='A',controller='A',zone='hand',logical_object_id='owned:1')
        other=ObjectQueryResult(object_id='other',ref='OTHER',printed_name='Generic opposing card',owner='B',controller='B',zone='hand',logical_object_id='other:1')
        query=SnapshotSemanticChoiceQuery(seat_order=('A','B','C','D'),active_order=('A','B','C','D'),object_rows=(row,other))
        payment=FixedEffectPaymentSpec('discard',predicate=ObjectQuerySpec(zones=('hand',),owner='A',known_to_actor=True))
        effect={'op':OPTIONAL_MANA_PAYMENT_OPERATION,'schema_version':2,'player':'A','payment':payment.to_dict(),
                'effects':[{'op':'draw','player':'A','count':2,'private':True}]}
        handler=payment_handler();prepared=handler.prepare(effect,payment_context(query=query))
        self.assertEqual(('OWNED',),prepared.request.choice.legal_refs)
        self.assertEqual('actor_private',prepared.request.choice.visibility)
        completion=handler.complete(payment_continuation(prepared.continuation_effect),{'cards':['OWNED']},query)
        self.assertEqual('MoveObjectsSimultaneouslyIntent',type(completion.intents[0]).__name__)
        self.assertEqual('discard',completion.intents[0].transition_kind.value)
        self.assertEqual(2,completion.prepend_effects[0]['count'])
        decline=handler.complete(payment_continuation(prepared.continuation_effect),{'cards':[]},query)
        self.assertFalse(decline.intents);self.assertFalse(decline.prepend_effects)
        from dataclasses import replace
        stale=SnapshotSemanticChoiceQuery(seat_order=query.seats,active_order=query.active_seats,object_rows=(replace(row,logical_object_id='owned:2'),other))
        with self.assertRaisesRegex(SemanticChoiceError,'stale'):
            handler.complete(payment_continuation(prepared.continuation_effect),{'cards':['OWNED']},stale)
        with self.assertRaisesRegex(SemanticChoiceError,'stale'):
            handler.complete(payment_continuation(prepared.continuation_effect),{'cards':['OTHER']},query)
        two=FixedEffectPaymentSpec('discard',amount=2,predicate=payment.predicate)
        insufficient=handler.prepare({**effect,'payment':two.to_dict()},payment_context(query=query))
        self.assertEqual(0,insufficient.request.choice.maximum)
        with self.assertRaisesRegex(SemanticChoiceError,'malformed'):
            handler.complete(payment_continuation(insufficient.continuation_effect),{'cards':['OWNED']},query)

    def test_v2_life_payment_and_malformed_inputs_fail_before_mutation(self):
        query=SnapshotSemanticChoiceQuery(seat_order=('A','B'),active_order=('A','B'),life_by_seat={'A':2,'B':40})
        effect={'op':OPTIONAL_MANA_PAYMENT_OPERATION,'schema_version':2,'player':'A','payment':FixedEffectPaymentSpec('life',amount=2).to_dict(),
                'effects':[{'op':'draw','player':'A','count':1,'private':True}]}
        handler=payment_handler();prepared=handler.prepare(effect,payment_context(query=query))
        completion=handler.complete(payment_continuation(prepared.continuation_effect),{'pay':True},query)
        self.assertEqual('PayLifeIntent',type(completion.intents[0]).__name__)
        low=SnapshotSemanticChoiceQuery(seat_order=('A','B'),active_order=('A','B'),life_by_seat={'A':1,'B':40})
        self.assertEqual((False,),handler.prepare(effect,payment_context(query=low)).request.choice.legal_values)
        with self.assertRaisesRegex(SemanticChoiceError,'no longer payable'):
            handler.complete(payment_continuation(prepared.continuation_effect),{'pay':True},low)
        with self.assertRaises(SemanticChoiceError):
            handler.complete(payment_continuation(prepared.continuation_effect),{'pay':1},query)

    def setUp(self) -> None:
        self.handler = payment_handler()
        self.effect = {
            "op": OPTIONAL_MANA_PAYMENT_OPERATION,
            "player": "A",
            "cost": {
                "GENERIC": 0,
                "W": 0,
                "U": 0,
                "B": 0,
                "R": 0,
                "G": 1,
                "C": 0,
            },
            "effects": [
                {
                    "op": "draw",
                    "player": "A",
                    "count": 1,
                    "private": True,
                }
            ],
        }

    def test_pay_decline_unpayable_and_stale_affordability_are_atomic(self):
        prepared = self.handler.prepare(self.effect, payment_context())
        self.assertEqual((True, False), prepared.request.choice.legal_values)
        self.assertTrue(prepared.request.public_context["payable"])

        paid = self.handler.complete(
            payment_continuation(prepared.continuation_effect),
            {"pay": True},
            payment_query(),
        )
        declined = self.handler.complete(
            payment_continuation(prepared.continuation_effect),
            {"pay": False},
            payment_query(),
        )
        self.assertEqual("PayManaCostIntent", type(paid.intents[0]).__name__)
        self.assertEqual("draw", paid.prepend_effects[0]["op"])
        self.assertFalse(declined.intents)
        self.assertFalse(declined.prepend_effects)

        unpayable = self.handler.prepare(
            self.effect,
            payment_context(query=payment_query(payable=False)),
        )
        self.assertEqual((False,), unpayable.request.choice.legal_values)
        with self.assertRaisesRegex(SemanticChoiceError, "no longer payable"):
            self.handler.complete(
                payment_continuation(prepared.continuation_effect),
                {"pay": True},
                payment_query(payable=False),
            )

    def test_runtime_wrapper_rejects_wrong_actor_extra_fields_and_nesting(self):
        with self.assertRaisesRegex(SemanticChoiceError, "active controller"):
            self.handler.prepare(
                {**self.effect, "player": "B"},
                payment_context(),
            )
        with self.assertRaisesRegex(SemanticChoiceError, "Malformed"):
            self.handler.prepare(
                {**self.effect, "unexpected": True},
                payment_context(),
            )
        with self.assertRaisesRegex(SemanticChoiceError, "cannot nest"):
            self.handler.prepare(
                {**self.effect, "effects": [self.effect]},
                payment_context(),
            )


class FixedOptionalManaPaymentIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def test_real_card_payment_is_private_commits_before_draw_and_replays(self):
        ir = compile_oracle_card(
            payment_record(
                "Whenever you cast a creature spell, you may pay {G}. If you "
                "do, draw a card.",
                name="Lifecrafter's Bestiary",
                type_line="Artifact",
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effects = ir.faces[0].nodes[0].effects

        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=14101,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.players["B"].mana_pool["G"] = 1
        program = SemanticProgram(
            key="test:lifecrafters-bestiary-payment",
            label="Lifecrafter's Bestiary payment",
            effects=list(effects),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="lifecrafters-bestiary-payment",
            ref="S-lifecrafters-bestiary-payment",
            kind="triggered_ability",
            controller="B",
            label=program.label,
            semantic_key=program.key,
            visibility=["A", "B", "C", "D"],
        )
        engine.state.stack.append(item)
        hand_before = len(engine.state.players["B"].zones["hand"])

        engine._begin_resolve_item(
            item,
            program.effects,
            None,
            note="Lifecrafter's Bestiary payment",
        )

        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(1, engine.state.players["B"].mana_pool["G"])
        self.assertEqual(hand_before, len(engine.state.players["B"].zones["hand"]))
        projector = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projector._decision("pilot:B"))
        for seat in ("A", "C", "D"):
            with self.subTest(seat=seat):
                self.assertIsNone(projector._decision(f"pilot:{seat}"))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "pay": True,
                "reason": "Pay for Lifecrafter's Bestiary.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, engine.state.players["B"].mana_pool["G"])
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["B"].zones["hand"]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "lifecrafters-bestiary-payment"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


class FixedResolutionPaymentActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'fixed-payment.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/fixed-resolution-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Generic payment deck',[DeckEntry('Generic Payment Commander',1,'commander'),DeckEntry('Generic Payment Plains',30)],['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        from copy import deepcopy
        from quorune.model import GameConfig
        from quorune.session import CommanderSession
        from quorune.oracle_ir import register_generated_programs
        session=CommanderSession.create(self.db,{seat:deepcopy(self.deck)for seat in 'ABCD'},first_player='A',seed=seed,config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        keep_all(session);engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine.state.priority_player=None;engine.state.priority_passes=[]
        register_generated_programs(self.db,engine.semantics,tuple(self.db.iter_cards()),trust_level='trusted',capability_registry=self.registry,capability_profile='commander_review',
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        return session

    def add(self,engine,name,*,zone='battlefield',seat='A',ref):
        from quorune.model import CardInstance
        row=self.db.lookup(name)
        card=CardInstance(object_id='payment:'+ref,ref=ref,oracle_id=row.oracle_id,printed_name=row.name,owner=seat,controller=seat,zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),known_to=list(engine.seats)if zone!='hand'else[seat],revealed_to=list(engine.seats)if zone!='hand'else[])
        engine.state.cards[card.object_id]=card;engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def ready(self,session,source,mana):
        engine=session.engine;engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='A';engine.state.started=True;engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players['A'].mana_pool.update(mana);engine._grant_priority('A');engine.pump()
        programs=engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(programs);self.assertTrue(all(engine.semantic_program_is_current_trusted(p)for p in programs))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        return next(a for a in actions if a['id']==f'cast:{source.ref}'or a['id'].startswith(f'activate:{source.ref}:'))

    def resolve(self,session):
        for _ in range(16):
            if session.state.pending_decision and session.state.pending_decision.kind!='priority':return
            if not session.state.stack:return
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Fixed payment stack did not resolve')

    def replay(self,session):
        from quorune.record import authoritative_state_hash
        expected=authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'fixed-payment-record';session.save(path)
            from quorune.session import CommanderSession
            self.assertEqual(expected,authoritative_state_hash(CommanderSession.load(self.db,path).state))
            result=replay_record(path,self.db,verify=True)
        self.assertTrue(result['ok'],result);self.assertEqual(expected,result['final_state_hash'])

    def test_trusted_discard_spell_uses_real_cast_private_payment_and_pre_action_replay(self):
        from quorune.record import authoritative_state_hash
        session=self.session(2381181201);engine=session.engine
        spell=self.add(engine,'Generic Discard Payment Spell',zone='hand',ref='PAYMENT-SPELL')
        payment=self.add(engine,'Generic Payment Plains',zone='hand',ref='PAYMENT-OWNED')
        opponent=self.add(engine,'Generic Payment Plains',zone='hand',seat='B',ref='PAYMENT-OPPONENT')
        action=self.ready(session,spell,{'W':1})
        session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
        cast=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(cast.ok,cast.summary);self.resolve(session)
        self.assertEqual('semantic.choice',engine.state.pending_decision.kind)
        decision=session.packet('pilot:A',full=True)['decision']
        self.assertIn(payment.ref,decision['ctx']['legal_actions'][0]['choice_schema']['legal_refs'])
        self.assertNotIn(opponent.ref,str(decision['ctx']))
        for seat in ('B','C','D'):self.assertIsNone(session.packet(f'pilot:{seat}',full=True)['decision'])
        before=authoritative_state_hash(engine.state)
        bad=session.act('pilot:A',{'action_id':'choose','cards':[opponent.ref]})
        self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        hand_before=len(engine.state.players['A'].zones['hand'])
        paid=session.act('pilot:A',{'action_id':'choose','cards':[payment.ref]})
        self.assertTrue(paid.ok,paid.summary)
        self.assertEqual('graveyard',engine.state.cards[payment.object_id].zone)
        self.assertEqual(hand_before+1,len(engine.state.players['A'].zones['hand']))
        self.assertEqual('graveyard',engine.state.cards[spell.object_id].zone)
        self.replay(session)

    def test_actual_v237_payment_record_is_explicitly_incompatible(self):
        import json
        from common import ROOT
        path=ROOT/'tests/fixtures/records/fixed-payment-v237-2756dd2b'
        provenance=json.loads((path/'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual('oracle-ir-v237',provenance['compiler_version'])
        self.assertEqual('explicit_runtime_trust_incompatibility',provenance['current_runtime_disposition'])
        programs=json.loads((path/'semantics.json').read_text(encoding='utf-8'))['programs'].values()
        historical=next(p for p in programs if p['oracle_id']=='00000000-0000-4000-8000-000000000810')
        self.assertEqual(OPTIONAL_MANA_PAYMENT_OPERATION,historical['effects'][0]['op'])
        self.assertNotIn('schema_version',historical['effects'][0])
        self.assertEqual(5,len((path/'commands.jsonl').read_text(encoding='utf-8').splitlines()))
        with self.assertRaisesRegex(ValueError,'Runtime trust provenance mismatch in record manifest'):
            replay_record(path,self.db,verify=True)

    def test_payment_replacement_choice_resumes_once_after_save_load_and_replays(self):
        from quorune.session import CommanderSession
        session=self.session(2381181202);engine=session.engine
        spell=self.add(engine,'Generic Discard Payment Spell',zone='hand',ref='REPLACED-SPELL')
        payment=self.add(engine,'Generic Payment Plains',zone='hand',ref='REPLACED-PAYMENT')
        self.add(engine,'Generic Payment Destination Replacement',seat='B',ref='PAYMENT-EXILE-ONE')
        self.add(engine,'Generic Payment Competing Replacement',seat='C',ref='PAYMENT-EXILE-TWO')
        action=self.ready(session,spell,{'W':1})
        session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok);self.resolve(session)
        before=len(engine.state.players['A'].zones['hand'])
        paid=session.act('pilot:A',{'action_id':'choose','cards':[payment.ref]})
        self.assertTrue(paid.ok,paid.summary)
        self.assertIn('replacement',engine.state.pending_decision.kind)
        self.assertEqual('hand',payment.zone)
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'pending-payment';session.save(path);session=CommanderSession.load(self.db,path)
        engine=session.engine
        for _ in range(4):
            if engine.state.pending_decision is None or 'replacement'not in engine.state.pending_decision.kind:break
            decision=session.packet('pilot:A',full=True)['decision']
            choice=session.act('pilot:A',{'action_id':'choose','replacement':decision['ctx']['options'][0]['id']})
            self.assertTrue(choice.ok,choice.summary)
        card=engine.state.cards[payment.object_id]
        self.assertEqual('exile',card.zone)
        self.assertEqual(1,sum(card.counters.values()))
        self.assertEqual(before+1,len(engine.state.players['A'].zones['hand']))
        # The resolving spell itself can encounter the same destination owner.
        for _ in range(4):
            if not engine.state.pending_decision or 'replacement'not in engine.state.pending_decision.kind:break
            decision=session.packet('pilot:A',full=True)['decision']
            result=session.act('pilot:A',{'action_id':'choose','replacement':decision['ctx']['options'][0]['id']})
            self.assertTrue(result.ok,result.summary)
        self.replay(session)

    def test_trusted_life_activation_and_declined_prefix_preserve_cost_scope(self):
        session=self.session(2381181203);engine=session.engine
        source=self.add(engine,'Generic Life Payment Source',ref='LIFE-PAYMENT-SOURCE')
        action=self.ready(session,source,{'C':1})
        session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
        hand_before=len(engine.state.players['A'].zones['hand']);life_before=engine.state.players['A'].life
        activated=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(activated.ok,activated.summary);self.resolve(session)
        self.assertEqual(life_before,engine.state.players['A'].life)
        paid=session.act('pilot:A',{'action_id':'choose','pay':True})
        self.assertTrue(paid.ok,paid.summary)
        self.assertEqual(life_before-2,engine.state.players['A'].life)
        self.assertEqual(hand_before+1,len(engine.state.players['A'].zones['hand']))
        self.replay(session)
        spell=self.add(engine,'Generic Payment Prefix Spell',zone='hand',ref='PREFIX-PAYMENT-SPELL')
        action=self.ready(session,spell,{'W':1});life_before=engine.state.players['A'].life
        hand_before=len(engine.state.players['A'].zones['hand'])
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok);self.resolve(session)
        self.assertEqual(life_before+2,engine.state.players['A'].life)
        declined=session.act('pilot:A',{'action_id':'choose','cards':[]})
        self.assertTrue(declined.ok,declined.summary)
        self.assertEqual(hand_before-1,len(engine.state.players['A'].zones['hand']))
        self.assertEqual('graveyard',spell.zone)

    def test_trusted_etb_sacrifice_uses_locked_trigger_controller_and_replays(self):
        from quorune.record import authoritative_state_hash
        session=self.session(2381181204);engine=session.engine
        source=self.add(engine,'Generic Sacrifice Payment Source',zone='hand',ref='SACRIFICE-SOURCE')
        payment=self.add(engine,'Generic Payment Plains',ref='SACRIFICE-LAND')
        payment.owner='B'
        opponent=self.add(engine,'Generic Payment Plains',seat='B',ref='OPPOSING-LAND')
        action=self.ready(session,source,{'W':1})
        session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok);self.resolve(session)
        self.assertEqual('semantic.choice',engine.state.pending_decision.kind)
        legal=session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertIn(payment.ref,legal);self.assertNotIn(opponent.ref,legal)
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'choose','cards':[opponent.ref]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        before_hand=len(engine.state.players['A'].zones['hand'])
        result=session.act('pilot:A',{'action_id':'choose','cards':[payment.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',engine.state.cards[payment.object_id].zone)
        self.assertIn(payment.object_id,engine.state.players['B'].zones['graveyard'])
        self.assertEqual('battlefield',engine.state.cards[opponent.object_id].zone)
        self.assertEqual(before_hand+1,len(engine.state.players['A'].zones['hand']))
        self.replay(session)

    def test_trusted_conditional_target_is_selected_before_payment_and_revalidated(self):
        from quorune.record import authoritative_state_hash
        for leaves in (False,True):
            with self.subTest(leaves=leaves):
                session=self.session(2381181205+leaves);engine=session.engine
                spell=self.add(engine,'Generic Targeted Payment Spell',zone='hand',ref='TARGETED-PAYMENT')
                payment=self.add(engine,'Generic Payment Plains',zone='hand',ref='TARGETED-PAYMENT-CARD')
                target=self.add(engine,'Generic Life Payment Source',seat='B',ref='PAYMENT-TARGET')
                noncreature=self.add(engine,'Generic Payment Plains',seat='B',ref='NOT-CREATURE')
                action=self.ready(session,spell,{'W':1})
                self.assertIn(target.ref,action['target_schema']['legal_refs'])
                self.assertNotIn(noncreature.ref,action['target_schema']['legal_refs'])
                before=authoritative_state_hash(engine.state)
                invalid=session.act('pilot:A',{'action_id':action['id'],'targets':[noncreature.ref],'pay':'auto'})
                self.assertFalse(invalid.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                engine=session.engine
                session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
                cast=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
                self.assertTrue(cast.ok,cast.summary)
                if leaves:
                    engine.move_card(target.object_id,'hand',reason='Constructed pre-resolution target departure')
                    # The departure is a separate fixture transition; replay the
                    # remaining canonical priority commands from its checkpoint.
                    session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
                self.resolve(session)
                if leaves:
                    self.assertNotEqual('semantic.choice',engine.state.pending_decision.kind if engine.state.pending_decision else None)
                    self.assertEqual('hand',engine.state.cards[payment.object_id].zone)
                else:
                    self.assertEqual('semantic.choice',engine.state.pending_decision.kind)
                    result=session.act('pilot:A',{'action_id':'choose','cards':[payment.ref]})
                    self.assertTrue(result.ok,result.summary)
                    self.assertTrue(engine.state.cards[target.object_id].tapped)
                    self.assertEqual('graveyard',engine.state.cards[payment.object_id].zone)
                self.replay(session)


if __name__ == "__main__":
    unittest.main()
