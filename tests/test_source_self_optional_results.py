from __future__ import annotations

"""CR 603.5: source-self optional results choose targets before resolution."""

import unittest
import json
from pathlib import Path
import tempfile
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class SourceSelfOptionalCompilerTests(unittest.TestCase):

    def test_equipment_self_entry_attachment_uses_printed_source_context(self):
        text = 'When this Equipment enters, attach it to target creature you control.'
        ir = compile_oracle_card(record(text, 'Artifact — Equipment'),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual('permanent.enter.self', node.event)
        self.assertEqual('attach', node.effects[0]['op'])
        self.assertEqual('$source.zone_object', node.effects[0]['source'])
        self.assertEqual('$target.0', node.effects[0]['target'])
        self.assertEqual('equipment', node.effects[0]['attachment_kind'])
        self.assertIsNotNone(node.target_schema)


    def test_restricted_equipment_entry_attachment_uses_shared_target_predicates(self):
        for subject, field, values in (('Knight', 'subtypes_any', ['knight']),
                                       ('Pirate', 'subtypes_any', ['pirate']),
                                       ('non-Human creature', 'subtypes_none', ['human'])):
            text = 'When this Equipment enters, attach it to target ' + subject + ' you control.'
            with self.subTest(subject=subject):
                ir = compile_oracle_card(record(text, 'Artifact — Equipment'),
                    capability_registry=load_default_capability_registry(), capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(values, node.target_schema[field])
                self.assertEqual('you', node.target_schema['controller_relation'])
                self.assertEqual('$source.zone_object', node.effects[0]['source'])
                self.assertIn('target.permanent.characteristic_predicate', node.capability_dependencies)

    def test_restricted_attachment_shape_and_compiler_omission_fail_closed(self):
        from copy import deepcopy
        from unittest.mock import patch
        from quorune.attachment_references import AttachmentReferenceKind
        from quorune.compiler.fixed_attachment_templates import fixed_source_attachment_effect_template
        from quorune.rules.attachment_action_capability_shapes import fixed_attachment_action_node_capabilities
        leaf = fixed_source_attachment_effect_template('Attach it to target Knight you control.',
            source_attachment_relation=AttachmentReferenceKind.EQUIPPED)
        self.assertIsNotNone(leaf)
        _, effects, schema, mechanics = leaf.compiled()
        self.assertIn('target.permanent.characteristic_predicate', fixed_attachment_action_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for field, value in (('controller_relation', 'any'), ('types_all', ['artifact']),
                             ('count', True), ('count', 2), ('unknown', True)):
            changed = deepcopy(schema);changed[field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual((), fixed_attachment_action_node_capabilities(
                    effects=effects, target_schema=changed, mechanic_ids=mechanics))
        text = 'When this Equipment enters, attach it to target Knight you control.'
        with patch('quorune.compiler.resolution_effect_templates.fixed_source_attachment_effect_template', return_value=None):
            ir = compile_oracle_card(record(text, 'Artifact — Equipment'),
                capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)

    def test_fixed_block_pair_damage_retains_distinct_source_and_counterpart_without_targeting(self):
        text = ('Whenever this creature blocks or becomes blocked by a creature, '
                'this creature deals 3 damage to that creature.')
        ir = compile_oracle_card(record(text, 'Creature — Elemental'),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIsNone(node.target_schema)
        self.assertEqual(({'op': 'damage', 'source': '$source',
                           'target': '$event.block_counterpart', 'amount': 3},), node.effects)
        self.assertIn('damage.source.stack_current_or_lki', node.capability_dependencies)
        self.assertIn('trigger.event.block_pair_counterpart', node.capability_dependencies)

    def test_counterpart_runtime_admission_infers_owner_and_rejects_widened_subscriptions(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from quorune.compiler.fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
        from quorune.rules.occurrence_object_results import occurrence_object_runtime_dependencies
        from quorune.rules.event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition
        template = FixedSourceCharacteristicsTemplate(source_kind='block-counterpart', keywords=('Wither',))
        condition = fixed_event_subscription_condition((
            FixedEventSubscription('creature.blocks', {'all': [
                {'field': 'card', 'op': 'eq', 'value': '$source.ref'},
                {'field': 'blocked_attacker', 'op': 'truthy', 'value': True}]}),
            FixedEventSubscription('creature.becomes_blocked_by', {'field': 'card', 'op': 'eq', 'value': '$source.ref'}),
        ))
        program = SimpleNamespace(event='creature.blocks', active_zone='battlefield',
            event_condition=condition, target_schema=None, coverage=template.mechanics,
            effects=({**template.effects[0], 'card': '$event.block_counterpart'},))
        self.assertIn('trigger.event.block_pair_counterpart', occurrence_object_runtime_dependencies(program))
        for change in ({'event': 'step.begin'}, {'active_zone': 'hand'},
                       {'target_schema': {'count': 1}}, {'event_condition': None}):
            changed = SimpleNamespace(**{**vars(program), **change})
            with self.subTest(change=change), self.assertRaises(ValueError):
                occurrence_object_runtime_dependencies(changed)
        for index in (0, 1):
            widened = deepcopy(condition)
            widened['event_subscriptions'][index]['condition'] = None
            with self.subTest(index=index), self.assertRaises(ValueError):
                occurrence_object_runtime_dependencies(SimpleNamespace(**{**vars(program), 'event_condition': widened}))
        aggregate = deepcopy(condition)
        aggregate['event_subscriptions'][1]['event'] = 'creature.becomes_blocked'
        with self.assertRaises(ValueError):
            occurrence_object_runtime_dependencies(SimpleNamespace(**{**vars(program), 'event_condition': aggregate}))

    def test_fixed_counterpart_damage_shape_and_producer_omission_fail_closed(self):
        from unittest.mock import patch
        from quorune.rules.damage_capability_shapes import fixed_damage_node_capabilities
        effect = {'op': 'damage', 'source': '$source', 'target': '$event.block_counterpart', 'amount': 3}
        self.assertIn('trigger.event.block_pair_counterpart', fixed_damage_node_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=('cr-120-damage',)))
        for change in ({'source': '$event.block_counterpart'}, {'target': '$source'},
                       {'amount': True}, {'amount': 0}, {'amount': -1}, {'unknown': True}):
            with self.subTest(change=change):
                self.assertEqual((), fixed_damage_node_capabilities(
                    effects=({**effect, **change},), target_schema=None, mechanic_ids=('cr-120-damage',)))
        self.assertEqual((), fixed_damage_node_capabilities(
            effects=(effect,), target_schema={'count': 1}, mechanic_ids=('cr-120-damage',)))
        text = ('Whenever this creature blocks or becomes blocked by a creature, '
                'this creature deals 3 damage to that creature.')
        with patch('quorune.compiler.damage_templates.source_pronoun_damage_effect_template', return_value=None):
            ir = compile_oracle_card(record(text, 'Creature — Elemental'),
                capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)

    def test_block_counterpart_reference_keeps_pair_direction_and_incarnation(self):
        from types import SimpleNamespace
        from quorune.block_transitions import BlockTransitionParticipant, build_block_transition
        from quorune.event_counter_reference import (
            BLOCK_COUNTERPART_CONTEXT, BLOCK_COUNTERPART_OBJECT,
            event_counter_recipient_context, resolve_block_counterpart_recipient,
        )
        participants = {name: BlockTransitionParticipant(name, name + '@1', name + '-ref', controller)
                        for name, controller in (('attack-a', 'A'), ('attack-b', 'A'), ('block-a', 'B'), ('block-b', 'B'))}
        assignments = {'attack-a': ('block-a', 'block-b'), 'attack-b': ('block-a',)}
        query = SimpleNamespace(turn_sequence=lambda: 1, priority_epoch=lambda: 1,
            active_player=lambda: 'A', attacker_object_ids=lambda: tuple(assignments),
            blocker_object_ids=lambda object_id: assignments[object_id],
            participant=lambda object_id: participants[object_id])
        transition = build_block_transition(query)
        program = SimpleNamespace(effects=({'op': 'apply_source_characteristics_until_end_of_turn',
                                           'card': BLOCK_COUNTERPART_OBJECT},))
        cells = []
        for event, actor, other, field in (
            ('creature.blocks', 'block-a', 'attack-a', 'blocked_attacker'),
            ('creature.blocks', 'block-a', 'attack-b', 'blocked_attacker'),
            ('creature.becomes_blocked_by', 'attack-a', 'block-a', 'blocking_creature'),
            ('creature.becomes_blocked_by', 'attack-a', 'block-b', 'blocking_creature'),
        ):
            context = {'card': participants[actor].reference, field: participants[other].reference,
                       'block_transition': transition.to_dict()}
            sealed = event_counter_recipient_context(program, event, context)
            self.assertEqual(participants[other].reference, sealed[BLOCK_COUNTERPART_CONTEXT]['reference'])
            self.assertEqual(participants[other].logical_object_id, sealed[BLOCK_COUNTERPART_CONTEXT]['logical_object_id'])
            cells.append(sealed)
        self.assertEqual(4, len(cells))
        current = SimpleNamespace(ref=participants['block-a'].reference, zone='battlefield',
                                  phased_out=False, logical_object_id='block-a@1')
        host = SimpleNamespace(state=SimpleNamespace(cards={'block-a': current}))
        item = SimpleNamespace(context=cells[2])
        self.assertEqual(current.ref, resolve_block_counterpart_recipient(host, item))
        current.logical_object_id = 'block-a@2'
        self.assertIsNone(resolve_block_counterpart_recipient(host, item))
        for context, event in (
            ({'card': participants['attack-b'].reference, 'blocking_creature': participants['block-b'].reference,
              'block_transition': transition.to_dict()}, 'creature.becomes_blocked_by'),
            ({'card': participants['attack-a'].reference, 'blocking_creature': participants['block-a'].reference,
              'block_transition': transition.to_dict()}, 'creature.becomes_blocked'),
        ):
            with self.subTest(context=context, event=event), self.assertRaises(ValueError):
                event_counter_recipient_context(program, event, context)

    def test_block_pair_counterpart_keyword_grant_keeps_per_creature_subscriptions(self):
        text = ('Whenever this creature blocks or becomes blocked by a creature, '
                'that creature gains wither until end of turn.')
        ir = compile_oracle_card(record(text, 'Creature — Wurm'),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIsNone(node.target_schema)
        subscriptions = node.event_condition['event_subscriptions']
        self.assertEqual({'creature.blocks', 'creature.becomes_blocked_by'},
                         {subscription['event'] for subscription in subscriptions})
        self.assertEqual(1, len(node.effects))
        self.assertNotEqual('$source.zone_object', node.effects[0].get('card'))

    def test_block_counterpart_grant_shape_and_authority_withdrawal_fail_closed(self):
        from quorune.compiler.fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
        from quorune.rules.node_capability_shapes import fixed_source_characteristics_node_capabilities
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        template = FixedSourceCharacteristicsTemplate(source_kind='block-counterpart', keywords=('Wither',))
        effect = {**template.effects[0], 'card': '$event.block_counterpart'}
        self.assertIn('trigger.event.block_pair_counterpart', fixed_source_characteristics_node_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=template.mechanics))
        for change in ({'keywords': ['Wither', 'Flying']}, {'power': 3}, {'set_colors': ['R']},
                       {'unknown': True}, {'card': '$event.unknown_counterpart'}):
            with self.subTest(change=change):
                self.assertEqual((), fixed_source_characteristics_node_capabilities(
                    effects=({**effect, **change},), target_schema=None, mechanic_ids=template.mechanics))
        self.assertEqual((), fixed_source_characteristics_node_capabilities(
            effects=(effect,), target_schema={'count': 1}, mechanic_ids=template.mechanics))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'trigger.event.block_pair_counterpart').update(
            status='blocked', blockers=['Counterpart authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        text = ('Whenever this creature blocks or becomes blocked by a creature, '
                'that creature gains wither until end of turn.')
        ir = compile_oracle_card(record(text, 'Creature — Wurm'), capability_registry=registry,
                                 capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(any('trigger.event.block_pair_counterpart' in str(row.blockers) for row in ir.material_residuals))

    def test_optional_cycled_source_damage_keeps_card_source_and_target_timing(self):
        registry = load_default_capability_registry()
        for body in ('you may have it deal 2 damage to target creature.',
                     'you may have it deal 1 damage to any target.',
                     'you may have it deal 1 damage to each creature.'):
            text = 'Draw a card.\nCycling {2}\nWhen you cycle this card, ' + body
            with self.subTest(body=body):
                ir = compile_oracle_card(record(text, 'Sorcery'), capability_registry=registry,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = next(node for face in ir.faces for node in face.nodes if node.kind == 'triggered_ability')
                self.assertEqual('card.cycled.self', node.event)
                self.assertEqual('hand', node.active_zone)
                effect, = node.effects
                self.assertEqual('offer_optional_effect', effect['op'])
                self.assertEqual('$controller', effect['player'])
                self.assertEqual('$source', effect['effects'][0]['source'])
                self.assertEqual('each creature' not in body, node.target_schema is not None)
                self.assertIn('effect.choice.optional_fixed', node.capability_dependencies)

    def test_optional_cycled_damage_rejects_other_card_pronouns_and_variable_amounts(self):
        registry = load_default_capability_registry()
        for text in (
            'Whenever you cycle a card, you may have it deal 2 damage to target creature.',
            'Whenever you cycle another card, you may have it deal 2 damage to target creature.',
            'When you cycle this card, you may have it deal X damage to target creature.',
            'When you cycle this card, you may have it deal 2 damage to target creature. Draw a card.',
            'When you cycle this card, you may have it deal 2 damage to an unrepresented target.',
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Creature — Human'), capability_registry=registry,
                                         capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)

    def test_cycled_damage_source_rebase_is_opt_in_and_requires_committed_identity(self):
        from types import SimpleNamespace
        from quorune.damage_source import DamageError
        from quorune.rules.source_references import CYCLED_SOURCE_DAMAGE_COVERAGE
        from quorune.rules.stack_damage_sources import cycled_damage_trigger_source
        source = SimpleNamespace(object_id='card', ref='cycled-card', logical_object_id='hand-object')
        before = {'type_line': 'Sorcery'}
        legacy = SimpleNamespace(coverage=())
        self.assertEqual((source, 'hand', before), cycled_damage_trigger_source(
            SimpleNamespace(), source, legacy, 'card.cycled', 'hand', before))
        program = SimpleNamespace(coverage=(CYCLED_SOURCE_DAMAGE_COVERAGE,),
            event='card.cycled.self', active_zone='hand', effects=({'op': 'damage', 'source': '$source'},))
        current = SimpleNamespace(object_id='card', ref='cycled-card',
            logical_object_id='discarded-object', zone='graveyard')
        after = {'type_line': 'Sorcery', 'mana_value': 5}
        host = SimpleNamespace(state=SimpleNamespace(cards={'card': current}),
                               _effective_card_data=lambda card: after)
        self.assertEqual((current, 'graveyard', after), cycled_damage_trigger_source(
            host, source, program, 'card.cycled', 'hand', before))
        for event, active in (('card.discarded', 'hand'), ('card.cycled', 'battlefield')):
            with self.subTest(event=event, active=active), self.assertRaises(DamageError):
                cycled_damage_trigger_source(host, source, program, event, active, before)
        current.logical_object_id = source.logical_object_id
        with self.assertRaises(DamageError):
            cycled_damage_trigger_source(host, source, program, 'card.cycled', 'hand', before)
        host.state.cards.clear()
        with self.assertRaises(DamageError):
            cycled_damage_trigger_source(host, source, program, 'card.cycled', 'hand', before)

    def test_cycled_source_damage_parser_omission_is_residual(self):
        from unittest.mock import patch
        text = 'Draw a card.\nCycling {2}\nWhen you cycle this card, you may have it deal 2 damage to target creature.'
        with patch('quorune.compiler.source_self_effect_templates.source_cycled_optional_damage_result', return_value=None):
            ir = compile_oracle_card(record(text, 'Sorcery'),
                capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)

    def test_optional_source_self_result_keeps_target_timing_and_complete_span(self):
        registry=load_default_capability_registry()
        for event in ('enters','dies'):
            text=f'When this creature {event}, you may have it deal 2 damage to any target.'
            with self.subTest(event=event):
                ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0]
                self.assertEqual(text,node.text)
                self.assertIsNotNone(node.target_schema)
                self.assertEqual('offer_optional_effect',node.effects[0]['op'])
                self.assertEqual('$source',node.effects[0]['effects'][0]['source'])
                self.assertIn('effect.choice.optional_fixed',node.capability_dependencies)
        for result in ('get +1/+1','gain flying'):
            text=f'When this creature enters, you may have it {result} until end of turn.'
            with self.subTest(result=result):
                ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertEqual('$source.zone_object',ir.faces[0].nodes[0].effects[0]['effects'][0]['card'])

    def test_unestablished_or_new_object_pronouns_are_not_rebound_to_source(self):
        registry=load_default_capability_registry()
        for text in ('Whenever another creature enters, you may have it deal 2 damage to any target.',
                     'When this creature enters, create a 1/1 green Elf creature token. You may have it deal 2 damage to any target.',
                     'You may have it deal 2 damage to any target.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)


class SourceSelfOptionalRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    import test_live_battlefield_quantities as _combat_actions
    import test_counter_placement_events as _counter_actions
    attack = _combat_actions.LiveBattlefieldQuantityActionTests.attack
    order_triggers = _counter_actions.CounterPlacementEventRuntimeTests.order_triggers

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name).resolve()/'source-self-optional.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/fixed-damage-source-lki-cards.json',
            ROOT/'tests/fixtures/source-self-optional-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source optional witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def choose(self,session,actor='A',**response):
        result=session.act('pilot:'+actor,{'action_id':'choose',**response})
        self.assertTrue(result.ok,result.summary)

    def test_original_witherscale_grants_each_blocker_wither_without_granting_itself(self):
        from quorune.event_counter_reference import BLOCK_COUNTERPART_CONTEXT
        session = self.session(402060)
        engine = session.engine
        source = self.add(engine, 'Witherscale Wurm', ref='actual-block-pair-wurm')
        first = self.add(engine, 'Generic Optional Ground', seat='B', ref='pair-first-blocker')
        second = self.add(engine, 'Generic Optional Ground', seat='B', ref='pair-second-blocker')
        blink = self.add(engine, 'Cloudshift', seat='B', zone='hand')
        self.add(engine, 'Generic Bound Plains', seat='B', ref='pair-response-plains')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='pair-private')
        self.assert_closed(session, source)
        self.attack(session, [source])
        for _ in range(12):
            if session.state.pending_decision.kind == 'combat.blockers':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:C', {'action_id': 'block', 'blocks': {first.ref: source.ref}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {
            first.ref: source.ref, second.ref: source.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.order_triggers(session)
        triggers = [item for item in session.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(2, len(triggers))
        self.assertEqual({first.ref, second.ref}, {item.context[BLOCK_COUNTERPART_CONTEXT]['reference'] for item in triggers})
        self.assertNotIn('wither', engine._combat_keywords(session.state.cards[source.object_id]))
        old_first = session.state.cards[first.object_id].logical_object_id
        session = self.replay(session, load=True)
        self.cast(session, 'B', blink, [first.ref])
        self.resolve(session)
        self.assertNotEqual(old_first, session.state.cards[first.object_id].logical_object_id)
        self.assertNotIn('wither', session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.assertIn('wither', session.engine._combat_keywords(session.state.cards[second.object_id]))
        self.assertNotIn('wither', session.engine._combat_keywords(session.state.cards[source.object_id]))
        for _ in range(24):
            if session.state.pending_decision.kind == 'combat.damage':
                accepted = session.act(session.pending_principals()[0], {'action_id': 'assign_damage',
                    'assignments': [{'source': source.ref, 'target': second.ref, 'amount': 9}]})
            else:
                accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
            if session.state.cards[source.object_id].counters.get('-1/-1') == 2:
                break
        else:
            self.fail('Granted wither did not produce its combat counter consequence')
        self.assertEqual(0, session.state.cards[source.object_id].marked_damage)
        combat = next(event for event in reversed(session.state.events) if event.code == 'combat.damage')
        self.assertIn({'source': source.ref, 'target': second.ref, 'amount': 9}, combat.details['assignments'])
        self.assertEqual(0, session.state.cards[second.object_id].marked_damage)
        self.assertEqual('graveyard', session.state.cards[second.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[first.object_id].zone)
        self.assertEqual(40, session.state.players['B'].life)
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_original_shining_armor_restricted_entry_choices_and_source_departure_keep_attachment_identity(self):
        for bounce_source in (False, True):
            with self.subTest(bounce_source=bounce_source):
                session = self.session(402100 + bounce_source)
                engine = session.engine
                source = self.add(engine, 'Shining Armor', zone='hand', ref='actual-restricted-entry-armor')
                knight = self.add(engine, 'Generic Attachment Knight', ref='legal-entry-knight')
                human = self.add(engine, 'Generic Attachment Human', ref='illegal-entry-human')
                kindred = self.add(engine, 'Generic Attachment Noncreature Knight', ref='illegal-entry-kindred')
                enemy = self.add(engine, 'Generic Attachment Knight', seat='B', ref='illegal-foreign-knight')
                bounce = self.add(engine, 'Generic Optional Bounce', zone='hand')
                self.add(engine, 'Generic Bound Plains', ref='restricted-attachment-payment')
                private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='entry-attachment-private')
                self.assert_closed(session, source)
                self.prepare(session, mana={'W': 12, 'U': 1})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.assertEqual('semantic.target', self.resolve(session).kind)
                for principal, target in (('pilot:A', human.ref), ('pilot:A', kindred.ref),
                                          ('pilot:A', enemy.ref), ('pilot:B', knight.ref)):
                    before = authoritative_state_hash(session.state)
                    rejected = session.act(principal, {'action_id': 'choose', 'targets': [target]})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                self.choose(session, targets=[knight.ref])
                if bounce_source:
                    self.cast(session, 'A', bounce, [source.ref])
                    self.resolve(session)
                    self.assertEqual('hand', session.state.cards[source.object_id].zone)
                    self.assertIsNone(session.state.cards[source.object_id].attached_to)
                    self.assertNotIn(source.object_id, session.state.cards[knight.object_id].attachments)
                else:
                    self.resolve(session)
                    current_source = session.state.cards[source.object_id]
                    current_knight = session.state.cards[knight.object_id]
                    self.assertEqual(knight.object_id, current_source.attached_to)
                    self.assertIn(source.object_id, current_knight.attachments)
                    self.assertEqual(5, session.engine._numeric_stat(knight.object_id, 'toughness'))
                    self.assertIn('vigilance', session.engine._combat_keywords(current_knight))
                for viewer in 'ABC':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_original_restricted_entry_attachment_handles_no_target_and_departed_nonhuman_target(self):
        for scenario in ('no_target', 'nonhuman', 'departed_target'):
            with self.subTest(scenario=scenario):
                session = self.session(402110)
                engine = session.engine
                name = 'Shining Armor' if scenario == 'no_target' else 'Rosethorn Halberd'
                source = self.add(engine, name, zone='hand', ref='restricted-entry-source')
                human = self.add(engine, 'Generic Attachment Human', ref='restricted-human')
                kindred = self.add(engine, 'Generic Attachment Noncreature Knight', ref='restricted-kindred')
                spirit = None if scenario == 'no_target' else self.add(
                    engine, 'Generic Optional Spirit', ref='restricted-nonhuman')
                bounce = self.add(engine, 'Generic Optional Bounce', zone='hand')
                self.assert_closed(session, source)
                self.prepare(session, mana={'W': 2, 'G': 1, 'U': 1})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                decision = self.resolve(session)
                if scenario == 'no_target':
                    self.assertIsNone(decision)
                    self.assertFalse(session.state.stack)
                else:
                    self.assertEqual('semantic.target', decision.kind)
                    for target in (human, kindred):
                        before = authoritative_state_hash(session.state)
                        rejected = session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]})
                        self.assertFalse(rejected.ok)
                        self.assertEqual(before, authoritative_state_hash(session.state))
                    self.choose(session, targets=[spirit.ref])
                    session = self.replay(session, load=True)
                    if scenario == 'departed_target':
                        self.cast(session, 'A', bounce, [spirit.ref])
                    self.resolve(session)
                if scenario == 'nonhuman':
                    self.assertEqual(spirit.object_id, session.state.cards[source.object_id].attached_to)
                    self.assertIn(source.object_id, session.state.cards[spirit.object_id].attachments)
                    self.assertEqual(4, session.engine._numeric_stat(spirit.object_id, 'power'))
                    self.assertEqual(7, session.engine._numeric_stat(spirit.object_id, 'toughness'))
                else:
                    self.assertIsNone(session.state.cards[source.object_id].attached_to)
                    if spirit is not None:
                        self.assertEqual('hand', session.state.cards[spirit.object_id].zone)
                        self.assertNotIn(source.object_id, session.state.cards[spirit.object_id].attachments)
                self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
                self.replay(session, load=True)

    def test_original_restricted_entry_equipment_carriers_have_complete_bindings(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        for name in ('Shining Armor', 'Rosethorn Halberd', "Pirate's Cutlass"):
            with self.subTest(name=name):
                source = self.db.lookup(name)
                register_generated_programs(self.db, semantics, (source,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, source, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])





    def test_block_counterpart_resolver_omission_is_killed_by_native_grant(self):
        from unittest.mock import patch
        with patch('quorune.event_counter_reference.resolve_block_counterpart_recipient', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_original_witherscale_grants_each_blocker_wither_without_granting_itself()

    def test_original_witherscale_blocks_two_attackers_and_requires_current_ability(self):
        from quorune.event_counter_reference import BLOCK_COUNTERPART_CONTEXT
        for remove_ability in (False, True):
            with self.subTest(remove_ability=remove_ability):
                session = self.session(402070 + remove_ability)
                engine = session.engine
                source = self.add(engine, 'Witherscale Wurm', seat='B', ref='actual-pair-blocking-wurm')
                capacity = self.add(engine, 'High Ground', seat='B', zone='hand')
                attackers = [self.add(engine, 'Generic Optional Ground', ref='pair-attacker-' + str(i)) for i in range(2)]
                humble = self.add(engine, 'Humble', zone='hand')
                self.assert_closed(session, source)
                self.assert_closed(session, capacity)
                self.prepare(session, mana={'W': 2})
                session.state.players['B'].mana_pool['W'] = 1
                if remove_ability:
                    self.cast(session, 'A', humble, [source.ref])
                    self.resolve(session)
                self.prepare(session, actor='B', active='B', mana={'W': 1})
                self.cast(session, 'B', capacity)
                self.resolve(session)
                self.attack(session, attackers)
                for _ in range(12):
                    if session.state.pending_decision.kind == 'combat.blockers':
                        break
                    accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
                    self.assertTrue(accepted.ok, accepted.summary)
                accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {
                    source.ref: [card.ref for card in attackers]}})
                self.assertTrue(accepted.ok, accepted.summary)
                self.order_triggers(session)
                triggers = [item for item in session.state.stack if item.source_object_id == source.object_id]
                self.assertEqual(0 if remove_ability else 2, len(triggers))
                if not remove_ability:
                    self.assertEqual({'B'}, {item.controller for item in triggers})
                    self.assertEqual({card.ref for card in attackers},
                        {item.context[BLOCK_COUNTERPART_CONTEXT]['reference'] for item in triggers})
                session = self.replay(session, load=True)
                self.resolve(session)
                for card in attackers:
                    keywords = session.engine._combat_keywords(session.state.cards[card.object_id])
                    self.assertEqual(not remove_ability, 'wither' in keywords)
                self.assertNotIn('wither', session.engine._combat_keywords(session.state.cards[source.object_id]))
                self.replay(session, load=True)

    def test_original_fixed_cycling_damage_carriers_bind_without_admitting_variable_damage(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        for name in ('Deem Worthy', 'Solar Blast', 'Slice and Dice', 'Gempalm Incinerator'):
            with self.subTest(name=name):
                source = self.db.lookup(name)
                register_generated_programs(self.db, semantics, (source,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, source, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(name != 'Gempalm Incinerator', binding['strict_capability_ready'], binding['blockers'])

    def test_original_witherscale_whole_card_composes_pair_grant_and_named_counter_removal(self):
        session = self.session(402080)
        source = self.add(session.engine, 'Witherscale Wurm')
        self.assert_closed(session, source)
        ir = compile_oracle_card(self.db.lookup('Witherscale Wurm'),
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertEqual(2, len(ir.faces[0].nodes))
        self.assertTrue(any('trigger.event.block_pair_counterpart' in node.capability_dependencies for node in ir.faces[0].nodes))
        self.assertTrue(any('counter.removal.all_named_source_effect' in node.capability_dependencies for node in ir.faces[0].nodes))

    def test_original_inferno_pair_damage_keeps_source_lki_and_excludes_returned_recipient(self):
        from quorune.event_counter_reference import BLOCK_COUNTERPART_CONTEXT
        for blink_recipient in (False, True):
            with self.subTest(blink_recipient=blink_recipient):
                session = self.session(402090 + blink_recipient)
                engine = session.engine
                source = self.add(engine, 'Inferno Elemental', ref='actual-pair-damage-source')
                blockers = [self.add(engine, 'Generic Optional Ground', seat='B', ref='damage-pair-blocker-' + str(i)) for i in range(2)]
                actor = 'B' if blink_recipient else 'A'
                blink = self.add(engine, 'Cloudshift', seat=actor, zone='hand')
                self.add(engine, 'Generic Bound Plains', seat=actor, ref='damage-pair-plains')
                private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='damage-pair-private')
                self.assert_closed(session, source)
                self.attack(session, [source])
                for _ in range(12):
                    if session.state.pending_decision.kind == 'combat.blockers':break
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {card.ref: source.ref for card in blockers}})
                self.assertTrue(accepted.ok, accepted.summary)
                self.order_triggers(session)
                self.assertEqual(2, len(session.state.stack))
                self.assertEqual({card.ref for card in blockers},
                    {item.context[BLOCK_COUNTERPART_CONTEXT]['reference'] for item in session.state.stack})
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:C', {'action_id': 'pass'}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                old_identity = session.state.cards[blockers[0].object_id if blink_recipient else source.object_id].logical_object_id
                session = self.replay(session, load=True)
                self.cast(session, actor, blink, [blockers[0].ref if blink_recipient else source.ref])
                self.resolve(session)
                changed = session.state.cards[blockers[0].object_id if blink_recipient else source.object_id]
                self.assertNotEqual(old_identity, changed.logical_object_id)
                self.assertEqual(0 if blink_recipient else 3, session.state.cards[blockers[0].object_id].marked_damage)
                self.assertEqual(3, session.state.cards[blockers[1].object_id].marked_damage)
                self.assertEqual(0, session.state.cards[source.object_id].marked_damage)
                for viewer in 'ABC':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_original_fixed_counterpart_damage_carriers_have_complete_bindings(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        for name in ('Inferno Elemental', 'Skewer Slinger', 'Ornery Goblin', 'Ashmouth Hound'):
            with self.subTest(name=name):
                source = self.db.lookup(name)
                register_generated_programs(self.db, semantics, (source,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, source, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
                self.assertEqual('exact', compile_oracle_card(source, capability_registry=self.registry,
                    capability_profile='commander_review').status)

    def test_cycled_source_damage_rebase_omission_is_killed_by_native_source_snapshot(self):
        from unittest.mock import patch
        def omit_rebase(host, source, program, event, active_zone, characteristics):
            return source, active_zone, characteristics
        with patch('quorune.rules.stack_damage_sources.cycled_damage_trigger_source', side_effect=omit_rebase):
            with self.assertRaises(AssertionError):
                self.run_original_deem_worthy_cycle_scenario(True)

    def test_original_deem_worthy_cycle_retains_new_zone_source_before_damage_and_private_draw(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.run_original_deem_worthy_cycle_scenario(apply)

    def run_original_deem_worthy_cycle_scenario(self, apply):
        from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT
        session = self.session(402030 + apply)
        engine = session.engine
        source = self.add(engine, 'Deem Worthy', zone='hand', ref='actual-cycled-damage')
        target = self.add(engine, 'Generic Optional Ground', seat='B', ref='cycle-damage-target')
        exile = self.add(engine, 'Generic Cycled Source Exile', zone='hand')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='cycle-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'R': 4})
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in actions if row['id'].startswith('activate:' + source.ref + ':')]
        before_draws = len(session.state.players['A'].draw_history)
        before_hand = set(session.state.players['A'].zones['hand'])
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(before_draws, len(session.state.players['A'].draw_history))
        self.assertEqual('semantic.target', self.resolve(session).kind)
        trigger = session.state.stack[-1]
        self.assertEqual('card.cycled', trigger.context['event'])
        self.assertEqual('graveyard', trigger.context[SOURCE_DAMAGE_CONTEXT]['zone'])
        self.assertEqual(session.state.cards[source.object_id].logical_object_id,
                         trigger.context[SOURCE_DAMAGE_CONTEXT]['logical_object_id'])
        self.assertEqual(source.object_id, trigger.source_object_id)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': 'choose', 'targets': ['B']}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, targets=[target.ref])
        self.cast(session, 'A', exile, [source.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        trigger = session.state.stack[-1]
        self.assertEqual('graveyard', trigger.context[SOURCE_DAMAGE_CONTEXT]['zone'])
        self.assertNotEqual(session.state.cards[source.object_id].logical_object_id,
                            trigger.context[SOURCE_DAMAGE_CONTEXT]['logical_object_id'])
        self.assertEqual(before_draws, len(session.state.players['A'].draw_history))
        self.assertEqual(0, session.state.cards[target.object_id].marked_damage)
        session = self.replay(session, load=True)
        self.choose(session, choice='apply' if apply else 'decline')
        self.resolve(session)
        self.assertEqual(2 if apply else 0, session.state.cards[target.object_id].marked_damage)
        self.assertEqual(before_draws + 1, len(session.state.players['A'].draw_history))
        drawn, = set(session.state.players['A'].zones['hand']) - before_hand
        drawn_ref = session.state.cards[drawn].ref
        for viewer in 'BCD':
            self.assertNotIn(drawn_ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_original_slice_and_dice_cycles_through_mass_damage_before_its_draw(self):
        session = self.session(402040)
        engine = session.engine
        source = self.add(engine, 'Slice and Dice', zone='hand', ref='actual-mass-cycle')
        first = self.add(engine, 'Generic Optional Ground', seat='B', ref='first-mass-cycle-target')
        second = self.add(engine, 'Generic Optional Flyer', seat='C', ref='second-mass-cycle-target')
        victim = self.add(engine, 'Goblin Arsonist', seat='D', ref='mass-cycle-lethal-target')
        self.assert_closed(session, source)
        self.prepare(session, mana={'R': 3})
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in actions if row['id'].startswith('activate:' + source.ref + ':')]
        draws = len(session.state.players['A'].draw_history)
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertIsNone(engine.semantics.get(session.state.stack[-1].semantic_key).target_schema)
        self.assertEqual(draws, len(session.state.players['A'].draw_history))
        session = self.replay(session, load=True)
        self.choose(session, choice='apply')
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.assertEqual([1, 1], [session.state.cards[card.object_id].marked_damage for card in (first, second)])
        self.assertEqual(draws, len(session.state.players['A'].draw_history))
        self.assertEqual('D', session.state.stack[-1].controller)
        self.choose(session, 'D', targets=['B'])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.choose(session, 'D', choice='decline')
        self.resolve(session)
        self.assertEqual(draws + 1, len(session.state.players['A'].draw_history))
        self.assertEqual({seat: 40 for seat in 'ABCD'}, {seat: player.life for seat, player in session.state.players.items()})
        self.replay(session, load=True)

    def test_original_cycling_damage_uses_exile_source_after_discard_replacement(self):
        from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT
        session = self.session(402050)
        engine = session.engine
        source = self.add(engine, 'Deem Worthy', zone='hand', ref='replaced-cycle-damage')
        target = self.add(engine, 'Generic Optional Ground', seat='C', ref='replaced-cycle-target')
        replacement = self.add(engine, 'Generic Cycling Destination Replacement', seat='B')
        self.assert_closed(session, replacement)
        self.assert_closed(session, source)
        self.prepare(session, mana={'R': 4})
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered, = [row for row in actions if row['id'].startswith('activate:' + source.ref + ':')]
        draws = len(session.state.players['A'].draw_history)
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        trigger = session.state.stack[-1]
        self.assertEqual('exile', trigger.context[SOURCE_DAMAGE_CONTEXT]['zone'])
        self.assertEqual(session.state.cards[source.object_id].logical_object_id,
                         trigger.context[SOURCE_DAMAGE_CONTEXT]['logical_object_id'])
        self.assertEqual(draws, len(session.state.players['A'].draw_history))
        session = self.replay(session, load=True)
        self.choose(session, targets=[target.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.choose(session, choice='apply')
        self.resolve(session)
        self.assertEqual(2, session.state.cards[target.object_id].marked_damage)
        self.assertEqual(draws + 1, len(session.state.players['A'].draw_history))
        self.replay(session, load=True)


    def test_original_rig_selects_a_flying_target_before_apply_or_decline(self):
        for apply in (False,True):
            with self.subTest(apply=apply):
                session=self.session(402001+apply);engine=session.engine
                source=self.add(engine,"Geistcatcher's Rig",zone='hand',ref='actual-optional-rig')
                flyer=self.add(engine,'Generic Optional Flyer',seat='B',ref='rig-flying-target')
                ground=self.add(engine,'Generic Optional Ground',seat='B',ref='rig-illegal-ground-target')
                private=self.add(engine,'Generic Bound Growth',seat='D',zone='hand',ref='rig-private')
                self.assert_closed(session,source);self.prepare(session,mana={'C':6});self.checkpoint(session)
                self.cast(session,'A',source)
                self.assertEqual('semantic.target',self.resolve(session).kind)
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:A',{'action_id':'choose','targets':[ground.ref]}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                session=self.replay(session,load=True);self.choose(session,targets=[flyer.ref])
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                self.assertEqual(0,session.state.cards[flyer.object_id].marked_damage)
                session=self.replay(session,load=True)
                self.choose(session,choice='apply' if apply else 'decline');self.resolve(session)
                self.assertEqual(4 if apply else 0,session.state.cards[flyer.object_id].marked_damage)
                self.assertEqual(0,session.state.cards[ground.object_id].marked_damage)
                for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
                self.replay(session,load=True)

    def test_original_arsonist_optional_death_damage_keeps_lki_and_trigger_controller(self):
        for apply in (False,True):
            with self.subTest(apply=apply):
                session=self.session(402010+apply);engine=session.engine
                source=self.add(engine,'Goblin Arsonist',zone='hand',ref='actual-optional-arsonist')
                grant=self.add(engine,'Generic Damage Source Lifelink',zone='hand',ref='optional-death-lifelink')
                control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='optional-death-control')
                kill=self.add(engine,'Generic Damage Source Kill',zone='hand',ref='optional-death-kill')
                self.assert_closed(session,source);self.prepare(session,mana={'R':1});engine.state.players['D'].mana_pool['W']=1
                self.checkpoint(session);self.cast(session,'A',source);self.resolve(session)
                self.cast(session,'A',grant,[source.ref]);self.cast(session,'D',control,[source.ref]);self.resolve(session)
                self.cast(session,'A',kill,[source.ref]);self.assertEqual('semantic.target',self.resolve(session).kind)
                self.assertEqual('D',session.state.stack[-1].controller)
                session=self.replay(session,load=True);self.choose(session,'D',targets=['B'])
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                self.assertEqual(40,session.state.players['B'].life)
                session=self.replay(session,load=True)
                self.choose(session,'D',choice='apply' if apply else 'decline');self.resolve(session)
                self.assertEqual({'A':40,'B':39 if apply else 40,'C':40,'D':41 if apply else 40},
                    {seat:player.life for seat,player in session.state.players.items()})
                self.replay(session,load=True)

    def test_original_clash_grants_to_entering_recipients_and_pending_trigger_survives_grant_departure(self):
        session=self.session(402020);engine=session.engine
        clash=self.add(engine,'Clash of Realities',zone='hand',ref='actual-source-optional-clash')
        spirit_target=self.add(engine,'Generic Optional Spirit',seat='C',ref='clash-spirit-target')
        other_target=self.add(engine,'Generic Optional Ground',seat='C',ref='clash-nonspirit-target')
        spirit=self.add(engine,'Generic Optional Spirit',seat='B',zone='hand',ref='clash-entering-spirit')
        other=self.add(engine,'Generic Optional Nonspirit',seat='D',zone='hand',ref='clash-entering-nonspirit')
        bounce=self.add(engine,'Generic Optional Bounce',zone='hand',ref='clash-real-source-departure')
        self.add(engine,'Island',ref='clash-bounce-mana');self.add(engine,'Mountain',ref='clash-recast-mana')
        self.assert_closed(session,clash);self.prepare(session,mana={'C':8,'R':3});self.checkpoint(session)
        self.cast(session,'A',clash);self.resolve(session);self.assertFalse(session.state.stack)
        self.cast(session,'B',spirit);self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertEqual('B',session.state.stack[-1].controller)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','targets':[spirit_target.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.choose(session,'B',targets=[other_target.ref])
        self.cast(session,'A',bounce,[clash.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual('hand',session.state.cards[clash.object_id].zone)
        session=self.replay(session,load=True);self.choose(session,'B',choice='apply');self.resolve(session)
        self.assertEqual(3,session.state.cards[other_target.object_id].marked_damage)
        self.cast(session,'A',clash);self.resolve(session);self.assertFalse(session.state.stack)
        self.cast(session,'D',other);self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertEqual('D',session.state.stack[-1].controller)
        self.choose(session,'D',targets=[spirit_target.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        session=self.replay(session,load=True);self.choose(session,'D',choice='apply');self.resolve(session)
        self.assertEqual(3,session.state.cards[spirit_target.object_id].marked_damage)
        self.replay(session,load=True)
