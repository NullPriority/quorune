"""Fixed damage keeps the ability source and the sealed occurrence recipient."""

from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
import test_qualified_spell_ordinals as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
from quorune.compiler.damage_templates import fixed_damage_effect_template
from quorune.compiler.event_player_results import fixed_event_player_damage_result,EVENT_PLAYER_DAMAGE_CAPABILITY as CAPABILITY
from quorune.rules.event_player_result_shapes import fixed_event_player_result_node_capabilities
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_controller_information import (
    TARGET_CONTROLLER_INFORMATION_CAPABILITY, occurrence_controller_reference,
)
from scripts.build_test_database import build_fixture_database


class EventPlayerFixedDamageCompilerTests(unittest.TestCase):
    def test_zone_damage_preserves_the_kind_of_controller_read(self):
        registry = load_default_capability_registry()
        cases = (
            ('Whenever a land enters, this artifact deals 2 damage to that land\'s controller.',
             'land.enter', occurrence_controller_reference()),
            ('Whenever another creature enters, this artifact deals 1 damage to that creature\'s controller.',
             'creature.enter', occurrence_controller_reference()),
            ('Whenever a land is put into a graveyard from the battlefield, this artifact deals 2 damage to that land\'s controller.',
             'permanent.graveyard', '$context.previous_controller'),
        )
        for text, event, recipient in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(query_record(text), type_line='Artifact'), capability_registry=registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(event, node.event)
                self.assertIsNone(node.target_schema)
                self.assertEqual(recipient, node.effects[0]['target'])
                if isinstance(recipient, dict):
                    self.assertIn(TARGET_CONTROLLER_INFORMATION_CAPABILITY, node.capability_dependencies)
                    for malformed in ({**recipient, 'schema_version': True}, {**recipient, 'extra': 'land'}):
                        self.assertEqual((), fixed_event_player_result_node_capabilities(
                            effects=({**node.effects[0], 'target': malformed},), target_schema=None,
                            mechanic_ids={'fixed-event-player-result', 'fixed-event-player-damage'},
                        ))
        wrong = (
            'Whenever a land enters, this artifact deals 2 damage to that creature\'s controller.',
            'Whenever an artifact is put into a graveyard from the battlefield, this artifact deals 2 damage to that land\'s controller.',
        )
        for text in wrong:
            ir = compile_oracle_card(replace(query_record(text), type_line='Artifact'), capability_registry=registry)
            self.assertNotEqual('exact', ir.status, text)

    def lower(self):
        binding=fixed_counter_trigger_binding('Whenever an opponent draws a card, this enchantment deals 1 damage to that player.',card_name='Generic Observer')
        self.assertIsNotNone(binding)
        def effect(text,*,card_name):
            result=fixed_damage_effect_template(text,card_name=card_name)
            return result.compiled() if result is not None else (None,(),None,())
        return fixed_event_player_damage_result(binding,'This enchantment deals 1 damage to that player.',effect_template=effect,card_name='Generic Observer')

    def test_result_keeps_source_and_event_player_without_a_target_choice(self):
        lowered=self.lower();self.assertIsNotNone(lowered);compiled,_=lowered
        self.assertIsNone(compiled[2]);self.assertEqual(({'op':'damage','source':'$source','target':'$context.player','amount':1},),compiled[1])
        self.assertIn(CAPABILITY,fixed_event_player_result_node_capabilities(effects=compiled[1],target_schema=None,mechanic_ids=compiled[3]))

    def test_malformed_damage_shapes_reject_wrong_source_amount_and_recipient(self):
        compiled,_=self.lower();effect=compiled[1][0]
        for change in ({'source':'$controller'},{'target':'$controller'},{'target':'$context.arbitrary'},{'amount':True},{'amount':0},{'extra':1}):
            self.assertEqual((),fixed_event_player_result_node_capabilities(effects=({**effect,**change},),target_schema=None,mechanic_ids=compiled[3]))

    def test_trigger_controller_recipient_mutant_is_killed(self):
        import quorune.compiler.event_player_results as owner
        def witness():self.assertEqual('$context.player',self.lower()[0][1][0]['target'])
        witness()
        with patch.object(owner,'_event_player',return_value='$controller'),self.assertRaises(AssertionError):witness()

    def test_damage_subject_event_matrix_preserves_each_existing_player_role(self):
        registry=load_default_capability_registry()
        cases=(('Whenever you cast a spell','that player','$context.player','Enchantment'),
            ('Whenever you discard a card','that player','$context.previous_controller','Enchantment'),
            ('Whenever a creature you control enters','that player','$context.controller','Enchantment'),
            ('Whenever a creature dies',"that creature's controller",'$context.previous_controller','Enchantment'),
            ('Whenever this creature attacks','defending player','$context.defending_player','Creature'))
        for header,subject,reference,kind in cases:
            text=header+', this '+kind.casefold()+' deals 1 damage to '+subject+'.'
            ir=compile_oracle_card(replace(query_record(text),type_line=kind),capability_registry=registry)
            self.assertEqual('exact',ir.status,(text,ir.material_residuals));self.assertEqual(reference,ir.faces[0].nodes[0].effects[0]['target'])
        for header,subject in (('Whenever you cast a spell',"that creature's controller"),('Whenever an opponent draws a card','defending player')):
            text=header+', this enchantment deals 1 damage to '+subject+'.'
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Enchantment'),capability_registry=registry).status)

    def test_verified_damage_withdrawal_keeps_existing_event_player_life_result(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        record=replace(query_record('Whenever an opponent draws a card, this enchantment deals 1 damage to that player.'),type_line='Enchantment')
        self.assertEqual('exact',compile_oracle_card(record,capability_registry=load_default_capability_registry()).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['bound damage result withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)
        control=replace(record,oracle_text='Whenever an opponent draws a card, that player loses 1 life.')
        self.assertEqual('exact',compile_oracle_card(control,capability_registry=registry).status)


class EventPlayerFixedDamageNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.QualifiedSpellOrdinalNativeTests.session
    add=native.QualifiedSpellOrdinalNativeTests.add
    prepare=native.QualifiedSpellOrdinalNativeTests.prepare
    cast=native.QualifiedSpellOrdinalNativeTests.cast
    cast_for=native.QualifiedSpellOrdinalNativeTests.cast_for
    action=native.QualifiedSpellOrdinalNativeTests.action
    priority=native.QualifiedSpellOrdinalNativeTests.priority
    checkpoint=native.QualifiedSpellOrdinalNativeTests.checkpoint
    resolve=native.QualifiedSpellOrdinalNativeTests.resolve
    replay=native.QualifiedSpellOrdinalNativeTests.replay
    assert_closed=native.QualifiedSpellOrdinalNativeTests.assert_closed
    until=native.QualifiedSpellOrdinalNativeTests.until
    resolve_top=InheritedNativeFixture.resolve_top

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'event-player-damage.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/event-player-fixed-damage-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Event player damage',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_runtime_bound_damage_dependency_is_inferred_without_capability_or_marker(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603635001);source=self.add(session.engine,'Underworld Dreams');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,coverage=tuple(c for c in program.coverage if c!='fixed-event-player-damage'),
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def test_entry_controller_damage_requires_its_runtime_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime

        s = self.session(60877301)
        source = self.add(s.engine, 'Ankh of Mishra')
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                       if CAPABILITY in p.capability_dependencies)
        self.assertEqual('trusted', program.trust_level)
        for removed in (CAPABILITY, TARGET_CONTROLLER_INFORMATION_CAPABILITY):
            dependencies = tuple(cap for cap in program.capability_dependencies if cap != removed)
            altered = replace(program, capability_dependencies=dependencies,
                              coverage=tuple(value for value in program.coverage if value != 'fixed-event-player-damage'),
                              capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertIn(f'capability:undeclared_runtime_dependency:{removed}', binding['blockers'])
        wrong_event = replace(program, event='spell.cast')
        binding = bind_semantic_program_runtime(wrong_event, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict'])

    def _ankh_after_control_change(self, seed):
        s = self.session(seed)
        source = self.add(s.engine, 'Ankh of Mishra', seat='B')
        land = self.add(s.engine, 'Darksteel Citadel', zone='hand')
        self.add(s.engine, 'Vedalken Orrery', seat='C')
        annex = self.add(s.engine, 'Annex', seat='C', zone='hand')
        blink = self.add(s.engine, 'Planar Incision', seat='B', zone='hand')
        self.assert_closed(s, source)
        self.assert_closed(s, annex)
        self.prepare(s, mana={})
        s.state.players['B'].mana_pool.update({'C': 1, 'U': 1})
        s.state.players['C'].mana_pool.update({'C': 2, 'U': 2})
        self.checkpoint(s)
        command = {'action_id': 'play-land:' + land.ref}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(s.state.stack))
        original = s.state.stack[-1]
        self.assertEqual(source.object_id, original.source_object_id)
        self.assertFalse(original.targets)
        self.assertEqual(land.object_id, original.context['card_object_id'])
        self.cast_for(s, 'C', annex, [land.ref])
        self.resolve_top(s)
        self.assertEqual('C', s.state.cards[land.object_id].controller)
        self.assertEqual([original.ref], [item.ref for item in s.state.stack])
        return s, land, blink, original.ref

    def test_actual_ankh_reads_current_land_controller_after_native_flash_control(self):
        s, _, blink, _ = self._ankh_after_control_change(60877302)
        s = self.replay(s, load=True)
        life = {seat: player.life for seat, player in s.state.players.items()}
        for viewer in 'ACD':
            self.assertNotIn(blink.ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
        self.resolve_top(s)
        self.assertEqual({**life, 'C': life['C'] - 2}, {seat: player.life for seat, player in s.state.players.items()})
        self.replay(s, load=True)

    def test_actual_ankh_keeps_last_controller_after_land_returns(self):
        s, land, blink, original_ref = self._ankh_after_control_change(60877303)
        life = {seat: player.life for seat, player in s.state.players.items()}
        original_identity = land.logical_object_id
        self.cast_for(s, 'B', blink, [land.ref])
        self.resolve_top(s)
        returned = s.state.cards[land.object_id]
        self.assertEqual('A', returned.controller)
        self.assertNotEqual(original_identity, returned.logical_object_id)
        self.assertEqual(2, len(s.state.stack))
        earlier = next(item for item in s.state.stack if item.ref == original_ref)
        lki = earlier.context['occurrence_controller_information_lki']
        self.assertEqual(original_identity, lki['logical_object_id'])
        self.assertEqual('C', lki['information']['controller'])
        s = self.replay(s, load=True)
        self.resolve_top(s)
        self.assertEqual(life['A'] - 2, s.state.players['A'].life)
        self.assertEqual(life['C'], s.state.players['C'].life)
        self.resolve_top(s)
        self.assertEqual({**life, 'A': life['A'] - 2, 'C': life['C'] - 2},
                         {seat: player.life for seat, player in s.state.players.items()})
        self.replay(s, load=True)

    def test_actual_dingus_egg_uses_the_departing_land_controller(self):
        s = self.session(60877304)
        source = self.add(s.engine, 'Dingus Egg', seat='B')
        land = self.add(s.engine, 'Generic Bound Plains', seat='D')
        annex = self.add(s.engine, 'Annex', zone='hand')
        rain = self.add(s.engine, 'Stone Rain', zone='hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 5, 'R': 3, 'U': 2})
        self.checkpoint(s)
        life = {seat: player.life for seat, player in s.state.players.items()}
        self.cast_for(s, 'A', annex, [land.ref])
        self.resolve_top(s)
        self.assertEqual('A', s.state.cards[land.object_id].controller)
        self.cast_for(s, 'A', rain, [land.ref])
        self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[land.object_id].zone)
        self.assertEqual('D', s.state.cards[land.object_id].owner)
        self.assertEqual(1, len(s.state.stack))
        self.resolve_top(s)
        self.assertEqual({**life, 'A': life['A'] - 2}, {seat: player.life for seat, player in s.state.players.items()})
        self.replay(s, load=True)

    def test_actual_magnetic_mine_sees_another_artifact_die_with_it(self):
        s = self.session(60877305)
        source = self.add(s.engine, 'Magnetic Mine', seat='B')
        key = self.add(s.engine, 'Voltaic Key', seat='D')
        theft = self.add(s.engine, 'Steal Artifact', zone='hand')
        storm = self.add(s.engine, 'Shatterstorm', zone='hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 6, 'R': 4, 'U': 3})
        self.checkpoint(s)
        life = {seat: player.life for seat, player in s.state.players.items()}
        self.cast_for(s, 'A', theft, [key.ref])
        self.resolve_top(s)
        self.assertEqual('A', s.state.cards[key.object_id].controller)
        self.cast_for(s, 'A', storm)
        self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[key.object_id].zone)
        self.assertEqual([source.object_id], [item.source_object_id for item in s.state.stack])
        s = self.replay(s, load=True)
        self.resolve_top(s)
        self.assertEqual({**life, 'A': life['A'] - 2}, {seat: player.life for seat, player in s.state.players.items()})
        self.replay(s, load=True)

    def test_using_the_entering_controller_instead_of_current_controller_is_detected(self):
        witness = type(self)('test_actual_ankh_reads_current_land_controller_after_native_flash_control')
        with patch('quorune.target_controller_information.resolve_occurrence_controller_information',
                   side_effect=lambda host, value, item: item.context['controller']):
            with self.assertRaises(AssertionError):
                witness.test_actual_ankh_reads_current_land_controller_after_native_flash_control()

    def test_station_monitor_nested_declaration_runtime_dependency_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603635002);source=self.add(session.engine,'Station Monitor');checked=0;cap='combat.declaration.typed_components'
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if cap not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])
        self.assertGreater(checked,0)

    def test_actual_underworld_draw_damage_survives_source_destruction_and_preserves_private_draws(self):
        session=self.session(603635010);source=self.add(session.engine,'Underworld Dreams',zone='hand')
        draw=self.add(session.engine,'Sign in Blood',seat='B',zone='hand');destroy=self.add(session.engine,'Disenchant',seat='B',zone='hand');self.add(session.engine,'Vedalken Orrery',seat='B')
        self.assert_closed(session,source);self.prepare(session,mana={'B':3});session.state.players['B'].mana_pool.update({'B':2,'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));life={s:session.state.players[s].life for s in 'ABCD'}
        expected=[session.state.cards[oid].ref for oid in session.state.players['B'].zones['library'][-2:]]
        self.priority(session,'B');offered=self.action(session,'B',draw);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'targets':['B'],'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast_for(session,'B',draw,['B'],reserve={'C':1,'W':1})
        self.until(session,lambda s:s.state.cards[draw.object_id].zone=='graveyard' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority')
        self.assertEqual(2,sum(i.source_object_id==source.object_id for i in session.state.stack))
        self.cast(session,'B',destroy,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual(life['B']-4,session.state.players['B'].life)
        for seat in 'ACD':
            self.assertEqual(life[seat],session.state.players[seat].life)
            for ref in expected:self.assertNotIn(ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)
