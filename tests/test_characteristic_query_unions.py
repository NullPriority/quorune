from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import (
    CharacteristicFragmentError, CharacteristicQuantityScope as Scope,
    CharacteristicQuantitySpec,
)
from quorune.compiler.query_characteristic_templates import query_characteristic_quantity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQueryError, ObjectQuerySpec
from quorune.object_query import ObjectQueryResult, query_objects
from quorune.object_query_union import RECEIVER_UNION_CAPABILITY as UNION_CAP
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_land_search_view_equivalence as native
from quorune.rules.evidence_storage import read_evidence_document


KATILDA = "Katilda, Dawnhart Martyr // Katilda's Rising Dawn"
NAMES = (KATILDA, 'Bearded Axe', 'Maraxus of Keld', 'Luxknight Breacher')
ATTACHED_CAP = 'continuous.attached.fixed_characteristics'
DEFINITION_CAP = 'continuous.characteristics.query_power_toughness_definition'
ENTRY_CAP = 'counter.producer.dynamic_self_entry'


class CharacteristicQueryUnionTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.LandSearchViewEquivalenceTests.session
    add = native.LandSearchViewEquivalenceTests.add
    prepare = native.LandSearchViewEquivalenceTests.prepare
    checkpoint = native.LandSearchViewEquivalenceTests.checkpoint
    resolve = native.LandSearchViewEquivalenceTests.resolve
    replay = native.LandSearchViewEquivalenceTests.replay
    private_replay = native.LandSearchViewEquivalenceTests.private_replay
    priority = native.LandSearchViewEquivalenceTests.priority
    actions = native.LandSearchViewEquivalenceTests.actions
    cast = native.LandSearchViewEquivalenceTests.cast
    reject = native.LandSearchViewEquivalenceTests.reject
    binding = native.LandSearchViewEquivalenceTests.binding
    activate = native.LandSearchViewEquivalenceTests.activate
    activation_command = native.LandSearchViewEquivalenceTests.activation_command
    resume = native.LandSearchViewEquivalenceTests.resume

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'characteristic-query-unions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/characteristic-query-union-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public characteristic query unions',
            [DeckEntry('Generic Bound Commander', 1, 'commander'),
             DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def stats(self, s, card):
        return tuple(s.engine._numeric_stat(card.object_id, field) for field in ('power', 'toughness'))

    def quantity(self, text):
        return query_characteristic_quantity(text, source_name='Generic Query Source', definition_extensions=True)

    def mana(self, s, card, *, seat='A'):
        self.priority(s, seat)
        row = next(r for r in self.actions(s, seat) if r['id'].startswith('activate:'+card.ref+':'))
        command = {'action_id': row['id'], 'pay': 'auto'}
        self.reject(s, command, seat='D')
        result = s.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_closed_quantity_grammar_preserves_union_domains_and_old_descriptors(self):
        mixed = self.quantity('Dwarf, Equipment, and/or Vehicle you control')
        self.assertIsNotNone(mixed)
        self.assertEqual(Scope.CONTROLLER_ZONE, mixed.scope)
        self.assertEqual(3, len(mixed.query.any_of))
        self.assertEqual(('dwarf',), mixed.query.any_of[0].subtypes_all)
        relative = self.quantity('permanents you control that are Spirits and/or enchantments')
        self.assertEqual(2, len(relative.query.any_of))
        creatures = self.quantity('creatures you control that are Spirits and/or enchantments')
        self.assertTrue(all('creature' in leaf.types_all for leaf in creatures.query.any_of))
        tapped = self.quantity('untapped artifacts, creatures, and lands you control')
        self.assertEqual(('artifact', 'creature', 'land'), tapped.query.types_any)
        self.assertIs(tapped.query.tapped, False)
        self.assertFalse(tapped.query.any_of)
        record = replace(self.db.lookup('Shambling Suit'),
            name='Generic Mixed Query Source', oracle_id='fixture:closed-mixed-query-source',
            oracle_text='This creature gets +1/+0 for each Dwarf, Equipment, and/or Vehicle you control.',
            power='0', toughness='1')
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual((0, len(record.oracle_text)), (node.span.start, node.span.end))
        self.assertTrue({UNION_CAP, 'continuous.characteristics.query_count_modifier'} <= set(node.capability_dependencies))
        for text, query in (
                ('artifact and/or enchantment you control', ObjectQuerySpec(zones=('battlefield',), types_any=('artifact', 'enchantment'))),
                ('Crabs, Oozes, and/or Horrors you control', ObjectQuerySpec(zones=('battlefield',), subtypes_any=('crab', 'ooze', 'horror')))):
            self.assertEqual(CharacteristicQuantitySpec(Scope.CONTROLLER_ZONE, query=query).to_dict(), self.quantity(text).to_dict())

    def test_union_quantities_reject_hidden_nested_unresolved_and_ambiguous_queries(self):
        query = self.quantity('Dwarf, Equipment, and/or Vehicle you control').query
        for version in (1, 2):
            quantity = CharacteristicQuantitySpec(Scope.CONTROLLER_ZONE, query=query, schema_version=version)
            self.assertEqual(quantity.to_dict(), CharacteristicQuantitySpec.from_dict(quantity.to_dict()).to_dict())
        unresolved = replace(query, any_of=(replace(query.any_of[0], exclude_ref='$source'), *query.any_of[1:]))
        with self.assertRaises(CharacteristicFragmentError):
            CharacteristicQuantitySpec(Scope.CONTROLLER_ZONE, query=unresolved)
        for change in ({'any_of': [query.to_dict(), query.to_dict()]},
                {'any_of': True}, {'zones': ['hand']}, {'include_phased_out': True}):
            with self.assertRaises((ObjectQueryError, CharacteristicFragmentError)):
                CharacteristicQuantitySpec.from_dict({**self.quantity('Spirits and/or enchantments you control').to_dict(),
                    'query': {**query.to_dict(), **change}})
        for text in ('Dwarf, Equipment you control', 'Dwarf and/or Dwarf you control',
                'Dwarf and/or creature with flying you control', 'untapped Dwarves and/or Equipment you control',
                'Dwarf and/or Equipment in your hand', 'Dwarf and/or Equipment in your graveyard',
                'Dwarf and/or Equipment and/or Vehicle and/or artifact and/or land you control',
                'Dwarf and/or Equipment your library contains', 'Dwarf and/or Equipment named Example you control'):
            self.assertIsNone(self.quantity(text), text)

    def test_overlapping_public_objects_are_counted_once_under_the_shared_controller(self):
        query = replace(self.quantity('Dwarf, Equipment, and/or Vehicle you control').query, controller='A')
        def row(ref, types, subtypes, controller='A'):
            return ObjectQueryResult(object_id=ref, ref=ref, printed_name='Public union object',
                owner='B', controller=controller, zone='battlefield', types=types,
                subtypes=subtypes, supertypes=(), colors=(), keywords=())
        rows = (row('dwarf', ('creature',), ('dwarf',)),
            row('equipment', ('artifact',), ('equipment',)),
            row('vehicle', ('artifact',), ('vehicle',)),
            row('all-three', ('artifact', 'creature'), ('dwarf', 'equipment', 'vehicle')),
            row('unmatched', ('creature',), ('elf',)),
            row('foreign', ('artifact', 'creature'), ('dwarf', 'equipment', 'vehicle'), 'B'))
        self.assertEqual(('dwarf', 'equipment', 'vehicle', 'all-three'), tuple(r.ref for r in query_objects(rows, query)))

    def test_originals_have_complete_normal_binding_and_exact_capability_minima(self):
        s = self.session(839000)
        for name in NAMES:
            card = self.add(s.engine, name, zone='hand')
            bound = self.binding(s, card)
            self.assertTrue(bound['strict_capability_ready'], (name, bound['blockers']))
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            nodes = [node for face in ir.faces for node in face.nodes]
            wanted = ({DEFINITION_CAP} if name == 'Maraxus of Keld' else
                {ENTRY_CAP} if name == 'Luxknight Breacher' else {UNION_CAP, ATTACHED_CAP})
            self.assertTrue(wanted <= {cap for node in nodes for cap in node.capability_dependencies})
            programs = s.engine.semantics.programs_for_oracle(card.oracle_id)
            relevant = [p for p in programs if wanted.intersection(p.capability_dependencies)]
            self.assertTrue(relevant)
            self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in relevant))
            if name == KATILDA:
                self.assertEqual(6, len(programs))
                self.assertTrue(all(p.trust_level == 'trusted' for p in programs))
            if name == 'Bearded Axe':
                self.assertTrue(any(a.builtin_semantic_key == 'builtin:equip' for a in s.engine._activated_abilities(card)))

    def test_verified_each_owner_withdrawal_blocks_only_dependent_compositions(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        mixed = replace(self.db.lookup('Shambling Suit'),
            name='Generic Withdrawn Mixed Query', oracle_id='fixture:withdrawn-mixed-query',
            oracle_text='This creature gets +1/+0 for each Dwarf, Equipment, and/or Vehicle you control.',
            power='0', toughness='1')
        cases = ((UNION_CAP, self.db.lookup('Bearded Axe')), (ATTACHED_CAP, self.db.lookup(KATILDA)),
            (DEFINITION_CAP, self.db.lookup('Maraxus of Keld')),
            ('continuous.characteristics.query_count_modifier', mixed),
            ('protection.typed.debt', self.db.lookup(KATILDA)),
            (ENTRY_CAP, self.db.lookup('Luxknight Breacher')))
        for cap, record in cases:
            changed = deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id'] == cap).update(
                status='blocked', blockers=['withdrawn characteristic query owner'], dependency_fail_closed_status='not_run')
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed,
                declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
                discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            ir = compile_oracle_card(record, capability_registry=registry, capability_profile='commander_review')
            blockers = [b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers), blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers), blockers)

    def test_current_face_protection_label_requires_that_faces_printed_declaration(self):
        from quorune.rules.current_face_keywords import current_printed_face_keywords
        s = self.session(839005)
        source = self.add(s.engine, KATILDA)
        record = self.db.lookup(KATILDA)
        base = {'keywords': list(record.keywords)}
        front = replace(source, active_face='Katilda, Dawnhart Martyr')
        back = replace(source, active_face="Katilda's Rising Dawn")
        current = lambda card: {str(k).casefold() for k in current_printed_face_keywords(s.engine, card, record, base)}
        self.assertTrue({'flying', 'lifelink', 'protection'} <= current(front))
        self.assertFalse({'flying', 'lifelink', 'protection'} & current(back))
        self.assertEqual(['Protection'], current_printed_face_keywords(s.engine, source,
            replace(record, faces=()), {'keywords': ['Protection']}))
        copied = replace(back, annotations={**back.annotations, 'copy_overrides': {'name': 'Copied object'}})
        self.assertEqual(base['keywords'], current_printed_face_keywords(None, copied, record, base))

    def test_simultaneous_luxknight_entries_count_only_the_pre_batch_battlefield(self):
        from quorune.record import authoritative_state_hash
        from quorune.semantic_runtime import prepare_zone_change_replacement_batch
        s = self.session(839006)
        self.add(s.engine, 'Memnite', ref='union-prior-entry-member')
        self.add(s.engine, 'Memnite', seat='B', ref='union-foreign-entry-member')
        first = self.add(s.engine, 'Luxknight Breacher', zone='hand', ref='union-entry-first')
        second = self.add(s.engine, 'Luxknight Breacher', zone='graveyard', seat='B', ref='union-entry-second')
        before = authoritative_state_hash(s.state)
        results = prepare_zone_change_replacement_batch(s.engine,
            ((first.object_id, 'battlefield'), (second.object_id, 'battlefield')),
            destination_controllers={first.object_id: 'A', second.object_id: 'A'})
        self.assertEqual({first.object_id, second.object_id}, set(results))
        for result in results.values():
            counter_events = [e for e in result.event.children if e.kind == 'counter.place']
            self.assertEqual(1, len(counter_events))
            self.assertEqual(('+1/+1', 1), (counter_events[0].payload['counter_name'], counter_events[0].payload['amount']))
        self.assertEqual(before, authoritative_state_hash(s.state))

    def test_real_luxknight_entry_reads_pending_battlefield_once_and_retains_counters(self):
        s = self.session(839007)
        source = self.add(s.engine, 'Luxknight Breacher', zone='hand')
        self.add(s.engine, 'Memnite', ref='union-entry-artifact-creature')
        boots = self.add(s.engine, 'Swiftfoot Boots', ref='union-entry-departing-artifact')
        body = self.add(s.engine, 'Generic Bound Body', ref='union-entry-creature')
        self.add(s.engine, 'Ornithopter', seat='B', ref='union-entry-foreign')
        destroy = self.add(s.engine, 'Disenchant', seat='B', zone='hand')
        bounce = self.add(s.engine, 'Boomerang', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool.update({'C': 1, 'W': 1})
        self.prepare(s, mana={'C': 3, 'W': 1, 'U': 2})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 3, 'W': 1})
        self.cast(s, destroy, [boots.ref], seat='B', payment={'C': 1, 'W': 1})
        for _ in range(20):
            if s.state.cards[boots.object_id].zone == 'graveyard':
                break
            self.assertEqual('priority', s.state.pending_decision.kind)
            result = s.act(s.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', s.state.cards[boots.object_id].zone)
        self.assertEqual('stack', s.state.cards[source.object_id].zone)
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('+1/+1'))
        self.assertEqual((4, 4), self.stats(s, source))
        self.cast(s, bounce, [body.ref], payment={'U': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[body.object_id].zone)
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('+1/+1'))
        self.assertEqual((4, 4), self.stats(s, source))
        self.private_replay(s, private)

    def test_real_front_death_disturb_and_aura_departure_share_one_replay_prefix(self):
        s = self.session(839008)
        source = self.add(s.engine, KATILDA, zone='hand')
        recipient = self.add(s.engine, 'Universal Automaton', ref='union-joint-recipient')
        murder = self.add(s.engine, 'Murder', seat='B', zone='hand')
        destroy = self.add(s.engine, 'Disenchant', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool.update({'C': 2, 'B': 2, 'W': 1})
        self.prepare(s, mana={'C': 4, 'W': 4})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 1, 'W': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2, 2), self.stats(s, source))
        self.cast(s, murder, [source.ref], seat='B', payment={'C': 1, 'B': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual((1, 1), self.stats(s, source))
        s = self.resume(s)
        self.priority(s, 'A')
        current = s.state.cards[source.object_id]
        back = "Katilda's Rising Dawn"
        command = {'action_id': 'cast:'+current.ref+':face:'+back, 'face': back,
            'cost_option': 'disturb', 'targets': [recipient.ref], 'pay': 'manual',
            'payment': {'C': 3, 'W': 2}}
        self.assertIn(command['action_id'], [r['id'] for r in self.actions(s)])
        self.reject(s, command, seat='D')
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(back, s.state.cards[source.object_id].active_face)
        self.assertEqual((3, 3), self.stats(s, recipient))
        self.cast(s, destroy, [s.state.cards[source.object_id].ref], seat='B', payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.assertEqual((1, 1), self.stats(s, recipient))
        self.private_replay(s, private)

    def test_real_paid_maraxus_counts_dual_types_once_and_rechecks_tap_and_ability_loss(self):
        s = self.session(839001)
        source = self.add(s.engine, 'Maraxus of Keld', zone='hand')
        elf = self.add(s.engine, 'Llanowar Elves', ref='union-own-elf')
        elf.acquired_control_turn_count = -1
        self.add(s.engine, 'Ornithopter', ref='union-dual-type')
        forest = self.add(s.engine, 'Forest', ref='union-counted-land')
        self.add(s.engine, 'Island', ref='union-other-land')
        self.add(s.engine, 'Memnite', seat='B', ref='union-foreign-artifact-creature')
        humble = self.add(s.engine, 'Humble', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'C': 6, 'R': 2, 'W': 1})
        self.checkpoint(s)
        self.assertEqual((4, 4), self.stats(s, source))
        self.cast(s, source, payment={'C': 4, 'R': 2})
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((5, 5), self.stats(s, source))
        self.mana(s, elf)
        self.assertEqual((4, 4), self.stats(s, source))
        self.mana(s, forest)
        self.assertEqual((3, 3), self.stats(s, source))
        self.cast(s, humble, [source.ref], payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((0, 1), self.stats(s, source))
        self.private_replay(s, private)

    def test_real_paid_axe_equip_uses_equipment_controller_after_recipient_control_changes(self):
        s = self.session(839002)
        source = self.add(s.engine, 'Bearded Axe', zone='hand')
        recipient = self.add(s.engine, 'Universal Automaton', ref='union-equipped-dwarf')
        self.add(s.engine, "Cultivator's Caravan", ref='union-vehicle')
        self.add(s.engine, 'Swiftfoot Boots', ref='union-other-equipment')
        self.add(s.engine, 'Universal Automaton', seat='B', ref='union-foreign-dwarf')
        control = self.add(s.engine, 'Control Magic', seat='B', zone='hand')
        self.add(s.engine, 'Vedalken Orrery', seat='B', ref='union-axe-control-timing')
        destroy = self.add(s.engine, 'Disenchant', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool.update({'C': 2, 'U': 2})
        self.prepare(s, mana={'C': 6, 'R': 1, 'W': 1})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 2, 'R': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual((1, 1), self.stats(s, recipient))
        equip_command = self.activation_command(s, source, targets=[recipient.ref])
        self.activate(s, source, targets=[recipient.ref])
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((5, 5), self.stats(s, recipient))
        self.assertEqual(recipient.object_id, s.state.cards[source.object_id].attached_to)
        self.cast(s, control, [recipient.ref], seat='B', payment={'C': 2, 'U': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('A', 'B'), (s.state.cards[recipient.object_id].owner, s.state.cards[recipient.object_id].controller))
        self.assertEqual((4, 4), self.stats(s, recipient))
        self.priority(s, 'A')
        self.assertNotIn(equip_command['action_id'], [r['id'] for r in self.actions(s)])
        self.reject(s, equip_command, seat='A')
        self.cast(s, destroy, [source.ref], payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertIsNone(s.state.cards[source.object_id].attached_to)
        self.assertNotIn(source.object_id, s.state.cards[recipient.object_id].attachments)
        self.assertEqual((1, 1), self.stats(s, recipient))
        self.private_replay(s, private)

    def test_real_paid_katilda_counts_enchantment_spirit_once_and_current_controller(self):
        s = self.session(839003)
        source = self.add(s.engine, KATILDA, zone='hand')
        self.add(s.engine, 'Eidolon of Rhetoric', ref='union-overlap-spirit-enchantment')
        self.add(s.engine, 'Universal Automaton', ref='union-changeling-spirit')
        self.add(s.engine, 'Memnite', seat='B', ref='union-foreign-nonmatch')
        control = self.add(s.engine, 'Control Magic', seat='B', zone='hand')
        self.add(s.engine, 'Vedalken Orrery', seat='B', ref='union-katilda-control-timing')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool.update({'C': 2, 'U': 2})
        self.prepare(s, mana={'C': 1, 'W': 2})
        self.checkpoint(s)
        self.cast(s, source, payment={'C': 1, 'W': 2})
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((3, 3), self.stats(s, source))
        self.assertTrue({'flying', 'lifelink'} <= s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.cast(s, control, [source.ref], seat='B', payment={'C': 2, 'U': 2})
        self.assertIsNone(self.resolve(s))
        self.assertEqual('B', s.state.cards[source.object_id].controller)
        self.assertEqual((2, 2), self.stats(s, source))
        self.private_replay(s, private)

    def test_real_disturb_aura_keeps_union_keywords_protection_and_exile_after_departure(self):
        s = self.session(839004)
        source = self.add(s.engine, KATILDA, zone='graveyard')
        recipient = self.add(s.engine, 'Universal Automaton', ref='union-disturb-recipient')
        self.add(s.engine, 'Eidolon of Rhetoric', ref='union-disturb-overlap')
        vampire = self.add(s.engine, 'Vampire Hexmage', seat='B')
        destroy = self.add(s.engine, 'Disenchant', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool.update({'C': 1, 'W': 1})
        self.prepare(s, mana={'C': 3, 'W': 2})
        self.checkpoint(s)
        back = "Katilda's Rising Dawn"
        action_id = 'cast:'+source.ref+':face:'+back
        self.assertIn(action_id, [r['id'] for r in self.actions(s)])
        command = {'action_id': action_id, 'face': back, 'cost_option': 'disturb',
            'targets': [recipient.ref], 'pay': 'manual', 'payment': {'C': 3, 'W': 2}}
        self.reject(s, {**command, 'cost_option': 'normal'}, seat='A')
        self.reject(s, command, seat='D')
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('battlefield', back), (s.state.cards[source.object_id].zone, s.state.cards[source.object_id].active_face))
        self.assertEqual((4, 4), self.stats(s, recipient))
        self.assertTrue({'flying', 'lifelink'} <= s.engine._combat_keywords(s.state.cards[recipient.object_id]))
        blocked = self.activation_command(s, vampire, seat='B', targets=[recipient.ref])
        self.reject(s, blocked, seat='B')
        self.assertEqual('battlefield', s.state.cards[vampire.object_id].zone)
        self.cast(s, destroy, [source.ref], seat='B', payment={'C': 1, 'W': 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.assertEqual((1, 1), self.stats(s, recipient))
        self.assertFalse({'flying', 'lifelink'} & s.engine._combat_keywords(s.state.cards[recipient.object_id]))
        self.activate(s, vampire, seat='B', targets=[recipient.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[vampire.object_id].zone)
        self.private_replay(s, private)

    def test_fresh_union_production_and_protection_list_omissions_are_detected(self):
        method = 'test_closed_quantity_grammar_preserves_union_domains_and_old_descriptors'
        with patch('quorune.compiler.receiver_union_queries.characteristic_quantity_union', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
        from quorune.compiler.attached_ability_templates import _attached_granted_abilities
        def assert_mixed_protection():
            abilities, fragments = _attached_granted_abilities('flying, lifelink, and protection from Vampires') or ((), ())
            self.assertEqual(('Flying', 'Lifelink', 'Protection'), abilities)
            self.assertTrue(fragments)
        assert_mixed_protection()
        with patch('quorune.compiler.attached_ability_templates.parse_protection_line', return_value=None):
            with self.assertRaises(AssertionError):
                assert_mixed_protection()
        method = 'test_current_face_protection_label_requires_that_faces_printed_declaration'
        with patch('quorune.rules.current_face_keywords.current_printed_face_keywords',
                side_effect=lambda host, card, record, base: base['keywords']):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
