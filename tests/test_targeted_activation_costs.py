"""CR601/602 and original Equip rulings establish target-dependent prices."""

from copy import deepcopy
from dataclasses import replace
from itertools import combinations, product
import json
from pathlib import Path
import random
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
from test_qualified_zone_event_queries import query_record
from quorune.activation_cost_modifiers import (
    ActivationCostModifierSource, FixedActivationCostModifierSpec, activation_cost_prices,
    TARGETED_ACTIVATION_COST_CAPABILITY, TARGETED_ACTIVATION_COST_HANDLER,
)
from quorune.compiler.targeted_activation_costs import targeted_activation_cost_template
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.target_cost_subject import TargetCostSubjectSpec
from quorune.rules.target_cost_subjects import target_cost_subject_refs
from quorune.targets import TargetGroup, TargetPlan
from quorune.selection.target_validation import target_assignment_witnesses
from quorune.semantic_runtime.activation_costs import FixedActivationCostModifierHandler, TargetedActivationCostModifierHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.oracle_ir import compile_oracle_card
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


EQUIP = 'Equip abilities you activate that target this creature cost {3} less to activate.'


class TargetedActivationCostModelTests(unittest.TestCase):
    def spec(self, amount=-3):
        return FixedActivationCostModifierSpec(amount, subject_relation='any', schema_version=2,
            ability_scope='equip', target_condition=TargetCostSubjectSpec('source'))

    def test_targeted_activation_codec_is_closed_and_legacy_payload_is_exact(self):
        old = FixedActivationCostModifierSpec(-2, subject_relation='any', ability_scope='equip').to_dict()
        self.assertNotIn('target_condition', old)
        self.assertEqual(json.dumps(old), json.dumps(FixedActivationCostModifierSpec.from_dict(old).to_dict()))
        spec = self.spec()
        self.assertEqual(spec, FixedActivationCostModifierSpec.from_dict(spec.to_dict()))
        for change in ({'schema_version': 1}, {'source_is_provider': 1}, {'target_condition': None}, {'extra': True}):
            with self.assertRaises((TypeError, ValueError)):
                FixedActivationCostModifierSpec.from_dict({**spec.to_dict(), **change})
        descriptor = targeted_activation_cost_template(EQUIP, source_name='Generic Champion')[1]
        self.assertEqual(replace(spec, controller_relation='source_controller'),
            TargetedActivationCostModifierHandler().validate(descriptor))
        with self.assertRaises(SemanticNodeError):
            FixedActivationCostModifierHandler().validate({**descriptor,
                'handler_id': FixedActivationCostModifierHandler().handler_id, 'schema_version': 1})

    def test_public_target_subject_preserves_provider_controller_owner_and_incarnation(self):
        def row(ref, controller, owner, *, types=('creature',), phased=False):
            return ObjectQueryResult(ref, ref, 'Merfolk', owner, controller, 'battlefield',
                types=types, subtypes=('merfolk',), phased_out=phased)
        query = TargetCostSubjectSpec('query', ObjectQuerySpec(zones=('battlefield',),
            subtypes_all=('merfolk',)), 'source_controller')
        objects = (row('borrowed:1', 'A', 'C'), row('enemy:1', 'D', 'A'),
            row('kindred:1', 'A', 'D', types=('enchantment', 'kindred')), row('phased:1', 'A', 'B', phased=True))
        self.assertEqual(('borrowed:1', 'kindred:1'), target_cost_subject_refs(query, source_ref='provider:1', controller='A', objects=objects))
        self.assertEqual(('enemy:1',), target_cost_subject_refs(query, source_ref='provider:1', controller='D', objects=objects))
        for relation, expected in (('source', ('provider:2',)), ('attached', ('receiver:3',))):
            self.assertEqual(expected, target_cost_subject_refs(TargetCostSubjectSpec(relation),
                source_ref='provider:2', controller='B', attached_ref='receiver:3'))
        self.assertEqual((), target_cost_subject_refs(TargetCostSubjectSpec('attached'), source_ref='provider:1', controller='A'))
        for query in (ObjectQuerySpec(zones=('hand',)), ObjectQuerySpec(zones=('battlefield',), tapped=True)):
            with self.assertRaises(ValueError): TargetCostSubjectSpec('query', query)

    def test_one_modifier_counts_the_target_set_once_and_preserves_colored_mana(self):
        spec = replace(self.spec(2), ability_scope='all')
        first = ActivationCostModifierSource('one', spec, ('merfolk:1', 'merfolk:2'))
        second = replace(first, component_id='two')
        base = {'GENERIC': 1, 'U': 1}
        for sources, targets, expected in (((first,), ('merfolk:1', 'merfolk:2'), 3),
                ((first,), ('merfolk:1', 'merfolk:1'), 3), ((first, second), ('merfolk:1',), 5),
                ((first,), ('other:1',), 1)):
            mods = tuple(source.spec for source in sources if source.applies(targets))
            price, = activation_cost_prices(base, mods)
            self.assertEqual((expected, 1), (price.requirements['GENERIC'], price.requirements['U']))
        price, = activation_cost_prices(base, (self.spec(),))
        self.assertEqual((0, 1), (price.requirements['GENERIC'], price.requirements['U']))

    def test_signature_representatives_retain_all_independent_cost_and_relation_witnesses(self):
        rng = random.Random(601602)
        refs = tuple('object:'+str(index) for index in range(8))
        for iteration in range(14):
            snapshots = {ref: {'owner': rng.choice('AC'), 'controller': rng.choice('BD')} for ref in refs}
            host = SimpleNamespace(_target_snapshot=lambda ref: snapshots[ref])
            same_owner = bool(iteration % 2); same_player = bool(iteration % 3)
            plan = TargetPlan((TargetGroup('first', min_targets=2, max_targets=2, same_owner=same_owner),
                TargetGroup('second', different_from_groups=('first',))),
                globally_distinct=True, same_player_groups=(('first', 'second'),) if same_player else ())
            eligible = (frozenset(ref for ref in refs if rng.randrange(2)),
                        frozenset(ref for ref in refs if rng.randrange(2)))
            candidates = {'first': refs, 'second': refs[2:]}
            for needed in (None, ('first', refs[0]), ('second', refs[-1])):
                expected = set()
                for first, second in product(combinations(refs, 2), refs[2:]):
                    if second in first or (needed is not None and needed[1] not in (first if needed[0] == 'first' else (second,))):
                        continue
                    if same_owner and len({snapshots[ref]['owner'] for ref in first}) != 1:
                        continue
                    if same_player and any(snapshots[ref]['controller'] != snapshots[second]['controller'] for ref in first):
                        continue
                    selected = (*first, second)
                    expected.add(tuple(bool(set(selected) & values) for values in eligible))
                actual = {tuple(bool(set(selected) & values) for values in eligible)
                    for selected, _groups in target_assignment_witnesses(host, plan, candidates,
                        required=needed, extra_targets=2, eligible_sets=eligible)}
                self.assertEqual(expected, actual, (iteration, needed))

    def test_reusable_target_count_can_exceed_the_number_of_distinct_references(self):
        host = SimpleNamespace(_target_snapshot=lambda ref: {'owner': 'A', 'controller': 'A'})
        plan = TargetPlan((TargetGroup('target', min_targets=3, max_targets=3, allow_reuse=True),))
        witnesses = tuple(target_assignment_witnesses(host, plan, {'target': ('only:1',)}))
        self.assertEqual((('only:1',) * 3,), tuple(selected for selected, _groups in witnesses))


class TargetedActivationCostCompilerTests(unittest.TestCase):
    def test_original_target_cost_grammar_spans_and_unsupported_variants(self):
        cases = (EQUIP,
            'Abilities your opponents activate that target a Merfolk you control cost {2} more to activate.',
            'Activated abilities of Equipment you control that target this creature cost {1} less to activate.',
            "This Equipment's equip ability costs {2} less to activate if it targets a colorless creature.",
            'Equip abilities you activate that target enchanted creature cost {3} less to activate.')
        registry = load_default_capability_registry()
        for text in cases:
            ir = compile_oracle_card(query_record(text), capability_registry=registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(text, query_record(text).oracle_text[node.span.start:node.span.end])
            self.assertEqual(TARGETED_ACTIVATION_COST_HANDLER, node.handlers[0]['handler_id'])
        for text in ('Equip abilities you activate that target this creature cost {X} less to activate.',
                     'Equip abilities you activate that target this creature cost {W} less to activate.',
                     'Equip abilities you activate that target a tapped creature cost {2} less to activate.',
                     'Equip abilities you activate that target another named source cost {2} less to activate.'):
            self.assertIsNone(targeted_activation_cost_template(text, source_name='Generic Champion'))

    def test_target_modifier_dependencies_fail_closed_with_verified_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        for omitted in (TARGETED_ACTIVATION_COST_CAPABILITY, 'activation.mana_cost.static_generic', 'variant.monarch.designate'):
            value = deepcopy(raw)
            next(row for row in value['capabilities'] if row['id'] == omitted).update(status='blocked', blockers=['target-price owner omitted'])
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertEqual(omitted == 'variant.monarch.designate',
                compile_oracle_card(query_record(EQUIP), capability_registry=registry).status == 'exact', omitted)


class TargetedActivationCostNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.JobSelectRuntimeTests.session
    add = actions.JobSelectRuntimeTests.add
    prepare = actions.JobSelectRuntimeTests.prepare
    cast = actions.JobSelectRuntimeTests.cast
    checkpoint = actions.JobSelectRuntimeTests.checkpoint
    resolve = actions.JobSelectRuntimeTests.resolve
    replay = actions.JobSelectRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'target-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/target-dependent-activation-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Target-dependent costs', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 24)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def activation(self, session, source, seat='A'):
        actions = session.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']
        return next((row for row in actions if row['id'].startswith('activate:'+source.ref+':')), None)

    def test_actual_champion_offers_free_equip_only_for_its_current_target_and_then_loses_it(self):
        session = self.session(601_602_201)
        champion = self.add(session.engine, 'Fervent Champion', zone='hand')
        collar = self.add(session.engine, 'Basilisk Collar')
        other = self.add(session.engine, 'Memnite')
        bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        humble = self.add(session.engine, 'Humble', seat='B', zone='hand')
        for index in range(2): self.add(session.engine, 'Island', seat='B', ref='target-cost-blue-'+str(index))
        for index in range(2): self.add(session.engine, 'Plains', seat='B', ref='target-cost-white-'+str(index))
        private = self.add(session.engine, 'Twiddle', seat='D', zone='hand')
        self.prepare(session, mana={'R': 1}); self.checkpoint(session)
        self.cast(session, 'A', champion); self.assertIsNone(self.resolve(session))
        offered = self.activation(session, collar); self.assertIsNotNone(offered)
        schema = offered['target_schema']
        self.assertIn(champion.ref, json.dumps(schema)); self.assertNotIn(other.ref, json.dumps(schema))
        command = {'action_id': offered['id'], 'targets': [champion.ref], 'pay': 'auto'}
        unchanged = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertFalse(session.act('pilot:A', {**command, 'targets': [other.ref]}).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        for seat in 'ABC': self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        result = session.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.cast(session, 'B', humble, [session.state.cards[champion.object_id].ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(champion.object_id, session.state.cards[collar.object_id].attached_to)
        for _ in range(8):
            if session.state.pending_decision.actors == ['A']: break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('battlefield', session.state.cards[champion.object_id].zone)
        self.assertIsNone(self.activation(session, session.state.cards[collar.object_id]))
        self.cast(session, 'B', bounce, [session.state.cards[champion.object_id].ref]); self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[champion.object_id].zone)
        # The next priority actor may be B after the response; return to A.
        for _ in range(8):
            if session.state.pending_decision.actors == ['A']: break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertIsNone(self.activation(session, session.state.cards[collar.object_id]))
        self.replay(session, load=True)

    def test_actual_ghostfire_discount_is_colorless_and_belongs_only_to_its_own_equip(self):
        session = self.session(601_602_202)
        blade = self.add(session.engine, 'Ghostfire Blade', zone='hand')
        collar = self.add(session.engine, 'Basilisk Collar')
        body = self.add(session.engine, 'Memnite')
        colored = self.add(session.engine, 'Goblin Piker')
        self.prepare(session, mana={'C': 2}); self.checkpoint(session)
        self.cast(session, 'A', blade); self.assertIsNone(self.resolve(session))
        offered = self.activation(session, blade); self.assertIsNotNone(offered)
        self.assertIn(body.ref, json.dumps(offered['target_schema']))
        self.assertNotIn(colored.ref, json.dumps(offered['target_schema']))
        self.assertIsNone(self.activation(session, collar))
        unchanged = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': offered['id'], 'targets': [colored.ref], 'pay': 'auto'}).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary); self.assertIsNone(self.resolve(session))
        self.assertEqual(body.object_id, session.state.cards[blade.object_id].attached_to)
        self.replay(session, load=True)

    def test_actual_aspirant_keeps_its_spell_discount_and_targeted_equip_price(self):
        session = self.session(601_602_203)
        source = self.add(session.engine, 'Bladegraft Aspirant', zone='hand')
        equipment = self.add(session.engine, 'Cranial Plating', zone='hand')
        self.add(session.engine, 'Memnite')
        self.prepare(session, mana={'C': 3, 'R': 1}); self.checkpoint(session)
        self.cast(session, 'A', source); self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', equipment); self.assertIsNone(self.resolve(session))
        offered = self.activation(session, equipment); self.assertIsNotNone(offered)
        self.assertEqual([source.ref], offered['target_schema']['legal_refs'])
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [source.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary); self.assertIsNone(self.resolve(session))
        self.assertEqual(source.object_id, equipment.attached_to); self.replay(session, load=True)

    def test_target_price_omission_is_killed_by_actual_zero_mana_equip(self):
        original = __import__('quorune.rules.activation.cost_modifiers', fromlist=['current_activation_cost_modifiers']).current_activation_cost_modifiers
        def omitted(*args, **kwargs):
            return tuple(value for value in original(*args, **kwargs) if not isinstance(value, ActivationCostModifierSource))
        with patch('quorune.rules.activation.pricing.current_activation_cost_modifiers', side_effect=omitted), self.assertRaises(AssertionError):
            self.test_actual_champion_offers_free_equip_only_for_its_current_target_and_then_loses_it()

    def test_normal_multi_target_activation_pays_one_tax_for_two_borrowed_merfolk(self):
        session = self.session(601_602_204)
        self.add(session.engine, 'Generic Merfolk Activation Toll')
        first = self.add(session.engine, 'Merfolk of the Pearl Trident')
        first.owner = 'C'  # Initial borrowed-permanent fixture, before checkpoint.
        second = self.add(session.engine, 'Coral Merfolk')
        self.add(session.engine, 'Goblin Piker', seat='D', ref='cost-untaxed-one')
        self.add(session.engine, 'Memnite', seat='B', ref='cost-untaxed-two')
        tapper = self.add(session.engine, 'Generic Target-Cost Tapper', seat='B')
        petal = self.add(session.engine, 'Lotus Petal', seat='B', zone='hand')
        secret = self.add(session.engine, 'Twiddle', seat='D', zone='hand')
        self.prepare(session, actor='B', active='B', mana={'C': 2}); self.checkpoint(session)
        offered = self.activation(session, tapper, 'B'); self.assertIsNotNone(offered)
        self.assertNotIn(first.ref, json.dumps(offered['target_schema']))
        self.assertNotIn(second.ref, json.dumps(offered['target_schema']))
        command = {'action_id': offered['id'], 'targets': [first.ref, second.ref], 'pay': 'auto'}
        unchanged = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', command).ok)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        for seat in 'ABC': self.assertNotIn(secret.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        self.cast(session, 'B', petal); self.assertIsNone(self.resolve(session))
        offered = self.activation(session, tapper, 'B'); self.assertIsNotNone(offered)
        self.assertIn(first.ref, json.dumps(offered['target_schema']))
        accepted = session.act('pilot:B', {**command, 'action_id': offered['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[first.object_id].tapped)
        self.assertTrue(session.state.cards[second.object_id].tapped)
        self.assertEqual('graveyard', session.state.cards[petal.object_id].zone)
        self.replay(session, load=True)

    def test_targeted_hybrid_activation_cannot_borrow_an_untaxed_payment_option(self):
        session = self.session(601_602_205)
        self.add(session.engine, 'Generic Merfolk Activation Toll')
        first = self.add(session.engine, 'Merfolk of the Pearl Trident')
        second = self.add(session.engine, 'Coral Merfolk')
        self.add(session.engine, 'Memnite', seat='B', ref='hybrid-untaxed-one')
        self.add(session.engine, 'Goblin Piker', seat='D', ref='hybrid-untaxed-two')
        source = self.add(session.engine, 'Generic Target-Cost Hybrid Tapper', seat='B')
        self.prepare(session, actor='B', active='B', mana={'C': 2, 'U': 1}); self.checkpoint(session)
        offered = self.activation(session, source, 'B'); self.assertIsNotNone(offered)
        cheap = next(row for row in offered['cost_options']
            if row['requirements']['GENERIC'] == 0 and row['requirements']['U'] == 1)
        taxed = next(row for row in offered['cost_options']
            if row['requirements']['GENERIC'] == 2 and row['requirements']['U'] == 1)
        command = {'action_id': offered['id'], 'targets': [first.ref, second.ref], 'pay': 'auto'}
        unchanged = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {**command, 'cost_option': cheap['id']}).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        accepted = session.act('pilot:B', {**command, 'cost_option': taxed['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(session.state.players['B'].mana_pool.values()))
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[first.object_id].tapped)
        self.assertTrue(session.state.cards[second.object_id].tapped)
        self.replay(session, load=True)


if __name__ == '__main__': unittest.main()
