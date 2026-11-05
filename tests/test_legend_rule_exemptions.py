from dataclasses import replace
from itertools import permutations
import unittest

from quorune.compiler.legend_rule_templates import legend_rule_exemption_spec
from quorune.legend_rule_model import LegendRuleError, LegendRuleExemptionSpec
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.rules.legend_rule import LegendRulePermanentSnapshot, detect_legend_conflicts


def permanent(key, *, controller='A', name='Shared Legend', owner=None, token=False,
              types=('creature',), subtypes=(), legendary=True, phased_out=False, exemptions=()):
    row = ObjectQueryResult(
        object_id=key, ref=key, printed_name='Printed ' + key, owner=owner or controller,
        controller=controller, zone='battlefield', types=types, subtypes=subtypes,
        supertypes=('legendary',) if legendary else (), token=token, phased_out=phased_out,
    )
    return LegendRulePermanentSnapshot(row, name, exemptions)


class LegendRuleExemptionModelTests(unittest.TestCase):
    def test_closed_grammar_retains_global_token_creature_and_subtype_meanings(self):
        for subject, fields in (
            ('permanents you control', {}),
            ('creatures you control', {'types_all': ('creature',)}),
            ('tokens you control', {'token': True}),
            ('creature tokens you control', {'types_all': ('creature',), 'token': True}),
            ('nontoken permanents you control', {'token': False}),
            ('nontoken creatures you control', {'types_all': ('creature',), 'token': False}),
            ('Slivers you control', {'subtypes_all': ('sliver',)}),
            ('Spiders you control', {'subtypes_all': ('spider',)}),
        ):
            with self.subTest(subject=subject):
                spec = legend_rule_exemption_spec('The "legend rule" doesn\'t apply to ' + subject + '.')
                self.assertEqual(LegendRuleExemptionSpec('controller', ObjectQuerySpec(
                    zones=('battlefield',), **fields)), spec)
                self.assertEqual(spec, LegendRuleExemptionSpec.from_dict(spec.to_dict()))
        self.assertEqual('global', legend_rule_exemption_spec('The “legend rule” doesn’t apply.').scope)

    def test_conditional_owned_opponent_and_unknown_subjects_remain_unrepresented(self):
        for text in (
            'If there are exactly two creatures named Example, the "legend rule" doesn\'t apply to them.',
            'The "legend rule" doesn\'t apply to creatures you own.',
            'The "legend rule" doesn\'t apply to creatures your opponents control.',
            'The "legend rule" doesn\'t apply to creatures you control if it is your turn.',
            'The "legend rule" doesn\'t apply to Mysteryfolk you control.',
            'The "legend rule" doesn\'t apply. Draw a card.',
        ):
            with self.subTest(text=text):
                self.assertIsNone(legend_rule_exemption_spec(text))

    def test_descriptor_and_query_authority_are_closed(self):
        spec = legend_rule_exemption_spec('The "legend rule" doesn\'t apply.')
        for value in ({**spec.to_dict(), 'extra': True}, {**spec.to_dict(), 'schema_version': True}):
            with self.assertRaises(ValueError):
                LegendRuleExemptionSpec.from_dict(value)
        for query in (
            ObjectQuerySpec(zones=('graveyard',)),
            ObjectQuerySpec(zones=('battlefield',), controller='A'),
            ObjectQuerySpec(zones=('battlefield',), include_phased_out=True),
            ObjectQuerySpec(zones=('battlefield',), types_all=('artifact',)),
        ):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    LegendRuleExemptionSpec('controller', query)


class LegendRuleGroupingOwnerTests(unittest.TestCase):
    def groups(self, rows, order=('A', 'B', 'C', 'D')):
        return detect_legend_conflicts(rows, player_order=order)

    def test_exempt_token_and_one_nontoken_can_coexist(self):
        rule = legend_rule_exemption_spec('The "legend rule" doesn\'t apply to tokens you control.')
        source = permanent('source', name='Cadric', exemptions=(rule,))
        self.assertEqual((), self.groups((source, permanent('original'), permanent('token', token=True))))

    def test_exempt_token_never_enters_the_nonexempt_keep_group(self):
        rule = legend_rule_exemption_spec('The "legend rule" doesn\'t apply to tokens you control.')
        rows = (permanent('source', name='Cadric', exemptions=(rule,)),
                permanent('original-1'), permanent('original-2'), permanent('token', token=True))
        expected = self.groups(rows)
        self.assertEqual(('original-1', 'original-2'), expected[0].object_ids)
        for variant in permutations(rows):
            self.assertEqual(expected, self.groups(variant))

    def test_subtype_exemption_uses_current_type_and_controller_instead_of_owner(self):
        rule = legend_rule_exemption_spec('The "legend rule" doesn\'t apply to Spiders you control.')
        source = permanent('source', name='Source', exemptions=(rule,))
        spider = permanent('spider', owner='B', subtypes=('spider',))
        ordinary = permanent('ordinary')
        self.assertEqual((), self.groups((source, spider, ordinary)))
        changed = replace(spider, card=replace(spider.card, subtypes=()))
        self.assertEqual(('ordinary', 'spider'), self.groups((source, changed, ordinary))[0].object_ids)
        foreign = replace(source, card=replace(source.card, controller='B'))
        self.assertEqual(('ordinary', 'spider'), self.groups((foreign, spider, ordinary))[0].object_ids)

    def test_source_departure_ability_removal_and_phasing_restore_the_conflict(self):
        rule = legend_rule_exemption_spec('The "legend rule" doesn\'t apply.')
        source = permanent('source', name='Gallery', types=('artifact',), legendary=False, exemptions=(rule,))
        pair = (permanent('one'), permanent('two'))
        self.assertEqual((), self.groups((source, *pair)))
        for rows in (pair, (replace(source, exemptions=()), *pair),
                     (replace(source, card=replace(source.card, phased_out=True)), *pair)):
            with self.subTest(rows=rows):
                self.assertEqual(('one', 'two'), self.groups(rows)[0].object_ids)

    def test_current_names_live_player_order_and_distinct_controllers_are_retained(self):
        rows = (permanent('a1'), permanent('a2'),
                permanent('c1', controller='C'), permanent('c2', controller='C'))
        self.assertEqual(('C', 'A'), tuple(g.controller for g in self.groups(rows, ('C', 'D', 'A'))))
        self.assertEqual((), self.groups((permanent('a'), permanent('b', controller='B'))))
        self.assertEqual((), self.groups((permanent('a'), permanent('b', name='Other Legend'))))
        self.assertEqual((), self.groups((permanent('a'), permanent('b', legendary=False))))
        self.assertEqual((), self.groups((permanent('a'), permanent('b', phased_out=True))))
        self.assertEqual((), self.groups((), ()))

    def test_repeated_objects_unknown_controllers_and_player_orders_reject(self):
        one = permanent('one')
        for rows, order in (((one, one), ('A',)), ((one,), ('B',)), ((), ('A', 'A'))):
            with self.subTest(rows=rows, order=order):
                with self.assertRaises(LegendRuleError):
                    self.groups(rows, order)


class LegendRuleCurrentSnapshotTests(unittest.TestCase):
    def test_current_source_controller_and_phasing_recompute_qualified_groups(self):
        from quorune.rules.legend_rule import current_legend_conflicts

        host = self.host()
        source = host.state.cards['gallery']
        host.data['gallery']['ability_fragments'] = (legend_rule_exemption_spec(
            'The "legend rule" doesn\'t apply to creatures you control.'),)
        self.assertEqual(('C',), tuple(group.controller for group in current_legend_conflicts(host)))
        host.state.players['A'].zones['battlefield'].remove(source.object_id)
        host.state.players['C'].zones['battlefield'].append(source.object_id)
        source.controller = 'C'
        self.assertEqual('A', source.owner)
        self.assertEqual(('A',), tuple(group.controller for group in current_legend_conflicts(host)))
        source.phased_out = True
        self.assertEqual(('C', 'A'), tuple(group.controller for group in current_legend_conflicts(host)))
        source.phased_out = False
        self.assertEqual(('A',), tuple(group.controller for group in current_legend_conflicts(host)))

    def test_omitting_current_exemption_collection_mutant_is_killed(self):
        from dataclasses import replace
        from unittest.mock import patch
        import quorune.rules.legend_rule as owner

        host = self.host()
        capture = owner.capture_legend_rule_permanents
        def witness():
            self.assertEqual((), owner.current_legend_conflicts(host))
        witness()
        def omitted(current):
            return tuple(replace(value, exemptions=()) for value in capture(current))
        with patch.object(owner, 'capture_legend_rule_permanents', side_effect=omitted):
            with self.assertRaises(AssertionError):
                witness()

    def test_current_choice_owner_uses_exemption_filtered_groups(self):
        from quorune.state_based_legend_choices import _current_groups

        host = self.host()
        self.assertEqual((), _current_groups(host))
        host.data['gallery']['ability_fragments'] = ()
        groups = _current_groups(host)
        self.assertEqual(('C', 'A'), tuple(group.controller for group in groups))
        self.assertEqual(('c1', 'c2'), tuple(c.object_id for c in groups[0].candidates))
        self.assertEqual(('a1', 'a2'), tuple(c.object_id for c in groups[1].candidates))

    def test_no_possible_conflict_skips_the_typed_exemption_snapshot(self):
        from unittest.mock import patch
        from quorune.state_based_legend_choices import _current_groups

        host = self.host()
        host.data['a2']['name'] = 'Different A'
        host.data['c2']['name'] = 'Different C'
        with patch('quorune.rules.legend_rule.capture_legend_rule_permanents') as capture:
            self.assertEqual((), _current_groups(host))
        capture.assert_not_called()

    def host(self):
        from collections import Counter
        from types import SimpleNamespace
        from quorune.characteristic_evaluation import type_parts
        from quorune.engine import CommanderEngine
        from quorune.model import CardInstance, PlayerState

        class Host:
            active_seats = ('A', 'C', 'D')
            _type_parts = staticmethod(type_parts)
            _legend_groups = CommanderEngine._legend_groups

            def apnap_order(self):
                self.order_reads += 1
                return ('C', 'D', 'A')

            def _effective_card_data(self, card):
                self.reads[card.object_id] += 1
                return dict(self.data[card.object_id])

        host = Host()
        host.state = SimpleNamespace(cards={}, players={seat: PlayerState(seat, seat) for seat in 'ABCD'})
        host.data, host.reads, host.order_reads = {}, Counter(), 0
        for key, seat in (('a1', 'A'), ('a2', 'A'), ('c1', 'C'), ('c2', 'C'), ('gallery', 'A')):
            card = CardInstance(object_id=key, ref=key, oracle_id='owner-fixture',
                printed_name='Printed ' + key, owner=seat, controller=seat, zone='battlefield')
            host.state.cards[key] = card
            host.state.players[seat].zones['battlefield'].append(key)
            host.data[key] = {'name': 'Shared Legend', 'type_line': 'Legendary Creature — Human',
                'colors': (), 'keywords': (), 'ability_fragments': ()}
        host.data['gallery'] = {'name': 'Gallery', 'type_line': 'Artifact', 'colors': (),
            'keywords': (), 'ability_fragments': (legend_rule_exemption_spec(
                'The "legend rule" doesn\'t apply.'),),
            'executable_oracle_text': 'The "legend rule" doesn\'t apply.'}
        return host

    def test_current_adapter_refreshes_ability_presence_without_using_display_text(self):
        from quorune.rules.legend_rule import current_legend_conflicts

        host = self.host()
        self.assertEqual((), current_legend_conflicts(host))
        self.assertTrue(all(count == 1 for count in host.reads.values()))
        self.assertEqual(1, host.order_reads)
        host.data['gallery']['ability_fragments'] = ()
        groups = current_legend_conflicts(host)
        self.assertEqual(('C', 'A'), tuple(group.controller for group in groups))
        self.assertEqual(('c1', 'c2'), groups[0].object_ids)
        self.assertEqual(('a1', 'a2'), groups[1].object_ids)
        self.assertTrue(all(count == 2 for count in host.reads.values()))
        host.data['c2']['name'] = ''
        self.assertEqual(('A',), tuple(group.controller for group in current_legend_conflicts(host)))

    def test_source_zone_phasing_and_departure_remove_the_current_exemption(self):
        from quorune.rules.legend_rule import current_legend_conflicts

        for mode in ('phased', 'graveyard', 'departed'):
            with self.subTest(mode=mode):
                host = self.host()
                source = host.state.cards['gallery']
                if mode == 'phased':
                    source.phased_out = True
                elif mode == 'graveyard':
                    source.zone = 'graveyard'
                else:
                    host.state.players['A'].zones['battlefield'].remove('gallery')
                    del host.state.cards['gallery']
                self.assertEqual(('C', 'A'), tuple(group.controller for group in current_legend_conflicts(host)))
                self.assertNotIn('gallery', host.reads)


class LegendRuleExemptionCompilerTests(unittest.TestCase):
    def record(self, text, *, type_line='Artifact'):
        from quorune.carddb import CardRecord

        return CardRecord(
            oracle_id='00000000-0000-4000-8000-000000006860',
            name='Generic Legend Rule Control', mana_cost='{2}', mana_value=2.0,
            type_line=type_line, oracle_text=text, power=None, toughness=None,
            loyalty=None, defense=None, colors=(), color_identity=(), keywords=(),
            produced_mana=(), layout='normal', released_at='2026-08-07',
            legalities={'commander': 'legal'}, faces=(), raw={},
        )

    def test_source_spanned_node_keeps_scope_and_withdrawn_authority_fails_closed(self):
        import json
        from common import ROOT
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, ability_fragment_from_dict
        from quorune.legend_rule_model import LEGEND_RULE_CAPABILITY, LEGEND_RULE_HANDLER
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        text = 'The "legend rule" doesn\'t apply to tokens you control.'
        registry = load_default_capability_registry()
        ir = compile_oracle_card(self.record(text), capability_registry=registry,
                                 capability_profile='commander_review')
        node = ir.faces[0].nodes[0]
        self.assertEqual('static-legend-rule-exemption-v1', node.template_id)
        self.assertEqual((0, len(text), 1), (node.span.start, node.span.end, node.span.line))
        self.assertTrue(node.lowerable)
        self.assertTrue(node.exact, ir.material_residuals)
        self.assertFalse(ir.material_residuals)
        self.assertIn(LEGEND_RULE_CAPABILITY, node.capability_dependencies)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, node.runtime_coverage)
        self.assertEqual(LEGEND_RULE_HANDLER, node.handlers[0]['handler_id'])
        self.assertEqual(legend_rule_exemption_spec(text),
                         ability_fragment_from_dict(node.handlers[0]['fragment']))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        next(row for row in raw['capabilities'] if row['id'] == LEGEND_RULE_CAPABILITY).update(
            status='blocked', blockers=['Current legend authority withdrawn'])
        blocked = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        blocked.mark_evidence_verified(validate_capability_evidence_index(index, registry=blocked))
        rejected = compile_oracle_card(self.record(text), capability_registry=blocked,
                                      capability_profile='commander_review')
        self.assertTrue(rejected.material_residuals)
        self.assertFalse(rejected.faces[0].nodes[0].exact)

    def test_conditional_riders_and_nonpermanent_context_never_borrow_static_admission(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry

        registry = load_default_capability_registry()
        for text, type_line in (
            ('The "legend rule" doesn\'t apply if it is your turn.', 'Artifact'),
            ('The "legend rule" doesn\'t apply. Draw a card.', 'Artifact'),
            ('The "legend rule" doesn\'t apply.', 'Instant'),
        ):
            with self.subTest(text=text, type_line=type_line):
                ir = compile_oracle_card(self.record(text, type_line=type_line),
                    capability_registry=registry, capability_profile='commander_review')
                self.assertTrue(ir.material_residuals)
                self.assertFalse(any(node.template_id == 'static-legend-rule-exemption-v1'
                                     for node in ir.faces[0].nodes))

    def test_registered_component_and_fragment_codec_reject_malformed_rule_data(self):
        from quorune.ability_fragments import ability_fragment_to_dict, canonical_ability_fragments
        from quorune.compiler.legend_rule_templates import static_legend_rule_exemption_handler
        from quorune.legend_rule_model import LEGEND_RULE_CAPABILITY
        from quorune.semantic_runtime.ability_fragments import LegendRuleExemptionAbilityFragmentHandler

        text = 'The "legend rule" doesn\'t apply to Spiders you control.'
        _template, descriptor, _capability = static_legend_rule_exemption_handler(text)
        handler = LegendRuleExemptionAbilityFragmentHandler()
        spec = legend_rule_exemption_spec(text)
        self.assertEqual((LEGEND_RULE_CAPABILITY,), handler.capability_dependencies)
        self.assertEqual((spec,), handler.lower(descriptor, None))
        self.assertEqual((spec,), canonical_ability_fragments((ability_fragment_to_dict(spec),)))
        for malformed in (
            {**descriptor, 'event': 'resolve'},
            {**descriptor, 'extra': True},
            {**descriptor, 'fragment': {'kind': 'legend_rule_exemption',
                'value': {**spec.to_dict(), 'scope': 'opponents'}}},
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    handler.validate(malformed)

    def test_nested_fragment_keeps_its_own_runtime_authority(self):
        from quorune.ability_fragments import ability_fragment_to_dict
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.legend_rule_model import LEGEND_RULE_CAPABILITY

        fragment = ability_fragment_to_dict(legend_rule_exemption_spec(
            'The "legend rule" doesn\'t apply to creatures you control.'))
        payload = {'op': 'grant_ability_fragment', 'fragment': fragment}
        self.assertIn(LEGEND_RULE_CAPABILITY, typed_result_amount_runtime_dependencies(payload))
        malformed = {'op': 'grant_ability_fragment', 'fragment': {
            'kind': 'legend_rule_exemption', 'value': {**fragment['value'], 'scope': 'opponents'},
        }}
        with self.assertRaises(ValueError):
            typed_result_amount_runtime_dependencies(malformed)


if __name__ == '__main__':
    unittest.main()
