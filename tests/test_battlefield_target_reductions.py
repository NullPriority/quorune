from __future__ import annotations

import json
import unittest
from dataclasses import replace

from quorune.replacement.immutable import FrozenMap
from quorune.targeted_spell_costs import TargetedCostSubject, TargetedSpellCostSpec
from quorune.compiler.targeted_spell_costs import targeted_spell_cost_template
from pathlib import Path
import tempfile
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_paid_graveyard_casts as controls
from quorune.rules.evidence_storage import read_evidence_document


class BattlefieldTargetReductionModelTests(unittest.TestCase):
    def test_version_three_keeps_caster_relation_and_predicate_distinct_from_old_wire(self):
        legacy = TargetedSpellCostSpec(TargetedCostSubject.SOURCE, 2)
        self.assertEqual({'schema_version': 1, 'subject': 'source', 'generic': 2}, legacy.to_dict())
        source = TargetedSpellCostSpec(TargetedCostSubject.SOURCE, -2, 3, caster_relation='any')
        creature = TargetedSpellCostSpec(TargetedCostSubject.SPELL_TARGET, -2, 3,
            FrozenMap({'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'], 'count': 1}),
            caster_relation='source_controller')
        for spec in (source, creature):
            self.assertEqual(spec, TargetedSpellCostSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            for change in ({'schema_version': True}, {'generic': 0}, {'generic': 2},
                    {'caster_relation': 'target_controller'}, {'future': 1}):
                with self.assertRaises(ValueError):
                    TargetedSpellCostSpec.from_dict({**spec.to_dict(), **change})
        with self.assertRaises(ValueError):
            replace(source, target_schema=creature.target_schema)
        with self.assertRaises(ValueError):
            replace(legacy, caster_relation='any')
        version_two = TargetedSpellCostSpec(TargetedCostSubject.SPELL_TARGET, -2, 2, creature.target_schema)
        self.assertEqual({'schema_version': 2, 'subject': 'spell_target', 'generic': -2,
            'target_schema': {'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'], 'count': 1}}, version_two.to_dict())
        with self.assertRaises(ValueError):
            TargetedSpellCostSpec.from_dict({**creature.to_dict(),
                'target_schema': {**dict(creature.target_schema), 'count': True}})

    def test_pinned_fixed_surfaces_lower_without_admitting_per_target_or_colored_prices(self):
        cases = (
            ('Spells that target this creature cost {2} less to cast.', 'source', 'any'),
            ('Spells you cast that target this creature cost {2} less to cast.', 'source', 'source_controller'),
            ('Spells you cast that target a creature cost {2} less to cast.', 'spell_target', 'source_controller'),
        )
        for text, subject, relation in cases:
            compiled = targeted_spell_cost_template(text, source_name='Generic Target Reducer')
            self.assertIsNotNone(compiled, text)
            spec = TargetedSpellCostSpec.from_dict(compiled[1]['modifier'])
            self.assertEqual((3, -2, subject, relation),
                (spec.schema_version, spec.generic, spec.subject.value, spec.caster_relation))
        promoted = targeted_spell_cost_template(
            'Spells you cast cost {1} less to cast for each target.', source_name='Generic Target Reducer')
        self.assertIsNotNone(promoted)
        modifier = promoted[1]['modifier']
        self.assertEqual((5, 'distinct_targets', -1),
            (modifier['schema_version'], modifier['subject'], modifier['generic']))
        controlled = targeted_spell_cost_template(
            'Spells you cast that target a creature you control cost {2} less to cast.', source_name='Generic Target Reducer')
        self.assertIsNotNone(controlled)
        modifier = controlled[1]['modifier']
        self.assertEqual((4, 'public_target', -2),
            (modifier['schema_version'], modifier['subject'], modifier['generic']))
        self.assertEqual('source_controller', modifier['target_subject']['controller_relation'])
        for text in (
            'Spells you cast cost {1} less to cast for each different creature type among its targets.',
            'Spells you cast that target a creature cost {U} less to cast.',
            'Spells you cast that target a creature cost {X} less to cast.',
            'Spells you cast that target a creature with the chosen name cost {2} less to cast.',
        ):
            self.assertIsNone(targeted_spell_cost_template(text, source_name='Generic Target Reducer'))


class BattlefieldTargetReductionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.PaidGraveyardRuntimeTests.session
    add = controls.PaidGraveyardRuntimeTests.add
    prepare = controls.PaidGraveyardRuntimeTests.prepare
    checkpoint = controls.PaidGraveyardRuntimeTests.checkpoint
    resolve = controls.PaidGraveyardRuntimeTests.resolve
    replay = controls.PaidGraveyardRuntimeTests.replay
    binding = controls.PaidGraveyardRuntimeTests.binding
    actions = controls.PaidGraveyardRuntimeTests.actions
    priority = controls.PaidGraveyardRuntimeTests.priority
    private_replay = controls.PaidGraveyardRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); path = Path(cls.temp.name)/'battlefield-target-reductions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/battlefield-target-reduction-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Battlefield target reduction', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()

    def offer(self, s, card, seat='A'):
        rows = s.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']
        row = next((r for r in rows if r.get('id') == 'cast:'+card.ref), None)
        self.assertIsNotNone(row, card.printed_name+' must retain an affordable legal target choice')
        return row

    def reject(self, s, command, seat='A'):
        before = authoritative_state_hash(s.state); result = s.act('pilot:'+seat, command)
        self.assertFalse(result.ok, result.summary); self.assertEqual(before, authoritative_state_hash(s.state))

    def cast(self, s, card, targets, *, seat='A', **extra):
        self.priority(s, seat); row = self.offer(s, card, seat)
        command = {'action_id': row['id'], 'targets': targets, 'pay': 'auto', **extra}
        self.reject(s, command, 'D' if seat != 'D' else 'C')
        result = s.act('pilot:'+seat, command); self.assertTrue(result.ok, result.summary)

    def test_three_originals_have_complete_normal_admission_and_independent_reduction_minimum(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import compile_oracle_card
        cap = 'casting.cost.battlefield_target_generic_reduction'
        for name in ('Spellwild Ouphe', 'Killian, Ink Duelist', 'Elderwood Scion'):
            card = self.db.lookup(name); ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            p = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
                capability_registry=self.registry, capability_profile='commander_review')
            b = bind_card_program_runtime(p, capability_registry=self.registry, profile='commander_review')
            self.assertTrue(b['strict_capability_ready'], b['blockers'])
            programs = [a for a in p.abilities if cap in a.capability_dependencies]; self.assertEqual(1, len(programs))
            ability = programs[0]; deps = tuple(c for c in ability.capability_dependencies if c != cap)
            missing = replace(ability, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            rejected = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(rejected['strict']); self.assertIn('capability:undeclared_runtime_dependency:'+cap, rejected['blockers'])

    def test_actual_ouphe_reduces_any_caster_once_and_never_reduces_colored_mana(self):
        for i, caster in enumerate(('A', 'B')):
            s = self.session(785000+i); source = self.add(s.engine, 'Spellwild Ouphe')
            spell = self.add(s.engine, 'Generic Target Discount Pair', seat=caster, zone='hand')
            other = self.add(s.engine, 'Generic Bound Body', seat='C')
            private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
            self.prepare(s, actor=caster, active=caster, mana={'C': 1, 'U': 1}); self.checkpoint(s)
            row = self.offer(s, spell, caster)
            self.reject(s, {'action_id': row['id'], 'targets': [other.ref], 'pay': 'auto'}, caster)
            self.cast(s, spell, [source.ref, other.ref], seat=caster)
            self.assertEqual(0, sum(s.state.players[caster].mana_pool.values()))
            s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
            self.assertEqual(('hand', 'hand'), (s.state.cards[source.object_id].zone, s.state.cards[other.object_id].zone))
            self.private_replay(s, private)

    def test_actual_killian_uses_creature_targets_and_its_current_controller_only(self):
        s = self.session(785010); source = self.add(s.engine, 'Killian, Ink Duelist')
        creature = self.add(s.engine, 'Generic Bound Body', seat='B'); artifact = self.add(s.engine, 'Fist of Suns')
        spell = self.add(s.engine, 'Generic Target Discount Mixed', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 1, 'U': 1}); self.checkpoint(s)
        self.assertLessEqual({'lifelink', 'menace'}, set(s.engine._combat_keywords(source)))
        row = self.offer(s, spell)
        self.reject(s, {'action_id': row['id'], 'targets': [artifact.ref], 'pay': 'auto'})
        self.cast(s, spell, [artifact.ref, creature.ref])
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(('hand', 'hand'), (s.state.cards[artifact.object_id].zone, s.state.cards[creature.object_id].zone))
        self.private_replay(s, private)
        other = self.session(785011); self.add(other.engine, 'Killian, Ink Duelist')
        victim = self.add(other.engine, 'Generic Bound Body'); murder = self.add(other.engine, 'Murder', seat='B', zone='hand')
        self.prepare(other, actor='B', active='B', mana={'B': 2}); self.checkpoint(other)
        self.assertFalse(any(r.get('id') == 'cast:'+murder.ref for r in other.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']))
        self.reject(other, {'action_id': 'cast:'+murder.ref, 'targets': [victim.ref], 'pay': 'auto'}, 'B'); self.replay(other)

    def test_actual_elderwood_preserves_opponent_tax_and_own_reduction_with_keywords(self):
        for i, caster in enumerate(('A', 'B')):
            s = self.session(785020+i); source = self.add(s.engine, 'Elderwood Scion')
            spell = self.add(s.engine, 'Generic Target Discount Pair', seat=caster, zone='hand')
            self.prepare(s, actor=caster, active=caster, mana={'C': 1 if caster == 'A' else 5, 'U': 1}); self.checkpoint(s)
            self.assertLessEqual({'trample', 'lifelink'}, set(s.engine._combat_keywords(source)))
            self.cast(s, spell, [source.ref], seat=caster)
            self.assertEqual(0, sum(s.state.players[caster].mana_pool.values()))
            s = self.replay(s, load=True); self.assertIsNone(self.resolve(s)); self.assertEqual('hand', s.state.cards[source.object_id].zone)
            self.replay(s)

    def test_independent_sources_stack_but_multiple_creature_targets_do_not_multiply_one_source(self):
        for count in (1, 2):
            s = self.session(785030+count)
            self.add(s.engine, 'Killian, Ink Duelist')
            one = self.add(s.engine, 'Spellwild Ouphe' if count == 2 else 'Generic Bound Body', seat='B', ref='stack-first-target')
            two = self.add(s.engine, 'Generic Bound Body', seat='C', ref='stack-second-target')
            spell = self.add(s.engine, 'Generic Target Discount Pair', zone='hand')
            self.prepare(s, mana={'C': 1 if count == 1 else 0, 'U': 1}); self.checkpoint(s)
            self.cast(s, spell, [one.ref, two.ref]); self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
            s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
            self.assertEqual(('hand', 'hand'), (s.state.cards[one.object_id].zone, s.state.cards[two.object_id].zone)); self.replay(s)

    def test_same_source_in_separate_target_groups_and_zero_generic_floor_apply_once(self):
        s = self.session(785040); source = self.add(s.engine, 'Spellwild Ouphe')
        spell = self.add(s.engine, 'Bounty of Might', zone='hand')
        self.prepare(s, mana={'C': 2, 'G': 2}); self.checkpoint(s)
        self.cast(s, spell, [source.ref, source.ref, source.ref]); self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual((10, 12), tuple(s.engine._numeric_stat(source.object_id, k) for k in ('power', 'toughness'))); self.replay(s)

    def test_source_ability_loss_and_control_change_rebind_the_current_reduction(self):
        for i, name in enumerate(('Humble', 'Control Magic')):
            s = self.session(785050+i); source = self.add(s.engine, 'Killian, Ink Duelist')
            removal = self.add(s.engine, name, seat='B', zone='hand')
            own = self.add(s.engine, 'Go for the Throat', zone='hand', ref='owner-destroy'); enemy = self.add(s.engine, 'Go for the Throat', seat='B', zone='hand', ref='new-controller-destroy')
            victim = self.add(s.engine, 'Generic Bound Body', seat='C')
            s.state.players['A'].mana_pool.update({'B': 1})
            self.prepare(s, actor='B', active='B', mana={'C': 2 if name == 'Control Magic' else 1, 'U': 2 if name == 'Control Magic' else 0, 'W': 1 if name == 'Humble' else 0, 'B': 1})
            self.checkpoint(s); self.cast(s, removal, [source.ref], seat='B'); self.assertIsNone(self.resolve(s))
            from quorune.rules.casting.proposal import build_cast_offer
            self.assertNotEqual('payable', build_cast_offer(s.engine, 'A', s.state.cards[own.object_id]).status)
            if name == 'Control Magic':
                self.priority(s, 'B'); self.cast(s, enemy, [victim.ref], seat='B')
                s = self.replay(s, load=True); self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
            else:
                self.assertNotIn('lifelink', s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.replay(s)

    def test_per_target_count_companions_stay_residual_and_do_not_receive_a_fixed_discount(self):
        s = self.session(785060)
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import compile_oracle_card
        # Preserve this evidence ID's residual control with an explicitly
        # unsupported count; the original distinct-target cards now close.
        for index, name in enumerate(('Battlefield Thaumaturge', 'Hinata, Dawn-Crowned')):
            card = self.add(s.engine, name)
            self.assertTrue(self.binding(s, card)['strict_capability_ready'])
            record = self.db.lookup(name)
            text = record.oracle_text.replace(
                'for each creature it targets', 'for each different creature type among its targets',
            ).replace('for each target', 'for each different creature type among its targets')
            self.assertNotEqual(record.oracle_text, text)
            boundary = replace(record,
                oracle_id=f'00000000-0000-4000-8000-{785060001 + index:012d}',
                name='Generic Unsupported Per-Target Companion', oracle_text=text +
                '\nSpells you cast that target a creature cost {2} less to cast.')
            ir = compile_oracle_card(boundary, capability_registry=self.registry,
                capability_profile='commander_review')
            self.assertTrue(ir.material_residuals)
            self.assertTrue(any('casting.cost.targeted_fixed_generic' in node.capability_dependencies
                for face in ir.faces for node in face.nodes))
            program = compile_best_available_card_program(self.db, boundary,
                semantic_registry=SemanticRegistry(), capability_registry=self.registry,
                capability_profile='commander_review')
            binding = bind_card_program_runtime(program, capability_registry=self.registry,
                profile='commander_review')
            self.assertFalse(binding['strict_capability_ready'])
            self.assertFalse(binding['compatible_ready'])
            self.assertIn('trust_basis:unresolved', binding['blockers'])
        self.prepare(s); self.checkpoint(s); self.replay(s)

    def test_announced_x_keeps_its_value_and_colored_mana_after_one_fixed_reduction(self):
        s = self.session(785070); self.add(s.engine, 'Killian, Ink Duelist')
        first = self.add(s.engine, 'Generic Bound Body', seat='B', ref='x-first')
        second = self.add(s.engine, 'Generic Bound Body', seat='C', ref='x-second')
        artifact = self.add(s.engine, 'Fist of Suns', ref='x-noncreature')
        spell = self.add(s.engine, 'Gridlock', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'U': 1}); self.checkpoint(s)
        row = self.offer(s, spell)
        self.reject(s, {'action_id': row['id'], 'x': 1, 'targets': [artifact.ref], 'pay': 'auto'})
        self.reject(s, {'action_id': row['id'], 'x': 3, 'targets': [first.ref, second.ref, artifact.ref], 'pay': 'auto'})
        self.cast(s, spell, [first.ref, second.ref], x=2)
        self.assertEqual(2, s.state.stack[-1].x_value)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in (first, second)))
        self.assertFalse(s.state.cards[artifact.object_id].tapped); self.private_replay(s, private)

    def test_generic_floor_still_requires_colored_mana_and_aura_cast_targets_qualify(self):
        s = self.session(785080); self.add(s.engine, 'Killian, Ink Duelist')
        target = self.add(s.engine, 'Generic Bound Body', seat='B')
        shock = self.add(s.engine, 'Shock', zone='hand')
        aura = self.add(s.engine, "Raffine's Guidance", zone='hand')
        self.prepare(s, mana={'W': 1, 'C': 4}); self.checkpoint(s)
        self.assertFalse(any(r['id'] == 'cast:'+shock.ref for r in self.actions(s)))
        self.reject(s, {'action_id': 'cast:'+shock.ref, 'targets': [target.ref], 'pay': 'auto'})
        self.cast(s, aura, [target.ref])
        self.assertEqual(4, s.state.players['A'].mana_pool['C']); self.assertEqual(0, s.state.players['A'].mana_pool['W'])
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(target.object_id, s.state.cards[aura.object_id].attached_to)
        self.assertEqual((3, 7), tuple(s.engine._numeric_stat(target.object_id, k) for k in ('power', 'toughness'))); self.replay(s)

    def test_increases_precede_reductions_and_paid_cost_does_not_change_after_source_departure(self):
        s = self.session(785090); source = self.add(s.engine, 'Spellwild Ouphe')
        self.add(s.engine, 'Sphere of Resistance', seat='C')
        target = self.add(s.engine, 'Generic Bound Body', seat='C')
        spell = self.add(s.engine, 'Generic Target Discount Pair', zone='hand')
        response = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool.update({'U': 1})
        self.prepare(s, mana={'C': 2, 'U': 1}); self.checkpoint(s)
        self.cast(s, spell, [source.ref, target.ref])
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.cast(s, response, [source.ref], seat='B')
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(('hand', 'hand'), (s.state.cards[source.object_id].zone, s.state.cards[target.object_id].zone))
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values())); self.private_replay(s, private)

    def test_fresh_originals_kill_the_reduction_producer_and_target_cost_collection_omissions(self):
        method = 'test_three_originals_have_complete_normal_admission_and_independent_reduction_minimum'
        getattr(type(self)(method), method)()
        original = targeted_spell_cost_template
        def omit(text, *, source_name):
            return None if 'less to cast' in text else original(text, source_name=source_name)
        with patch('quorune.compiler.targeted_spell_costs.targeted_spell_cost_template', side_effect=omit):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()
        method = 'test_actual_ouphe_reduces_any_caster_once_and_never_reduces_colored_mana'
        with patch('quorune.rules.casting.targeted_costs.targeted_spell_cost_sources', return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()

    def test_verified_withdrawal_and_closed_runtime_activity_keep_the_new_owner_required(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantics import SemanticRegistry
        cap = 'casting.cost.battlefield_target_generic_reduction'
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        value = deepcopy(raw); next(c for c in value['capabilities'] if c['id'] == cap).update(status='blocked', blockers=['withdrawn battlefield target reduction'])
        registry = CapabilityRegistry(value)
        index = build_index(registry_value=value, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in ('Spellwild Ouphe', 'Killian, Ink Duelist', 'Elderwood Scion'):
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
            p = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
                capability_registry=self.registry, capability_profile='commander_review')
            ability = next(a for a in p.abilities if cap in a.capability_dependencies)
            changed = replace(ability, active_zone='all')
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertTrue(any('Self target reduction' in b or 'Battlefield target reduction' in b for b in binding['blockers']), binding['blockers'])

    def test_component_rejects_predicate_scope_and_caster_field_drift(self):
        from copy import deepcopy
        from quorune.semantic_runtime.targeted_spell_costs import TargetedSpellCostHandler
        descriptor = targeted_spell_cost_template('Spells you cast that target a creature cost {2} less to cast.', source_name='Generic Reducer')[1]
        for patch in ({'schema_version': True}, {'event': 'resolve'}, {'future': 1}):
            with self.assertRaises(ValueError):TargetedSpellCostHandler().validate({**descriptor, **patch})
        for field, value in (('zones', ['hand']), ('categories', ['player']), ('count', True), ('types_any', ['artifact']), ('future', True)):
            changed = deepcopy(descriptor); changed['modifier']['target_schema'][field] = value
            with self.assertRaises(ValueError):TargetedSpellCostHandler().validate(changed)
