from __future__ import annotations

"""CR608.2h: a tap occurrence player differs from object controller information."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_attached_occurrences as controls


class TapControllerInformationTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachedOccurrenceTests.session
    add=controls.AttachedOccurrenceTests.add
    prepare=controls.AttachedOccurrenceTests.prepare
    checkpoint=controls.AttachedOccurrenceTests.checkpoint
    resolve=controls.AttachedOccurrenceTests.resolve
    replay=controls.AttachedOccurrenceTests.replay
    binding=controls.AttachedOccurrenceTests.binding
    priority=controls.AttachedOccurrenceTests.priority
    cast=controls.AttachedOccurrenceTests.cast
    offered_cast=controls.AttachedOccurrenceTests.offered_cast
    private_replay=controls.AttachedOccurrenceTests.private_replay
    offered=controls.AttachedOccurrenceTests.offered

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'tap-controller-information.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/tap-controller-information-cards.json',ROOT/'tests/fixtures/entry-copy-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Tap controller information',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def original(self,name,seed,payment):
        session=self.session(seed);aura=self.add(session.engine,name,zone='hand')
        land=self.add(session.engine,'Generic Bound Plains',seat='B',ref='tap-timing-land')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'],'Whole original '+name+' must remain admitted')
        self.prepare(session,mana=payment);self.checkpoint(session);self.offered_cast(session,aura,payment,[land.ref]);self.assertIsNone(self.resolve(session))
        self.priority(session,'B');actions=[r for r in self.offered(session,'B') if r.get('card')==land.ref or land.ref in r['id']]
        self.assertEqual(1,len(actions));result=session.act('pilot:B',{'action_id':actions[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,len(session.state.stack));item=session.state.stack[-1]
        self.assertEqual('A',item.controller);self.assertEqual('B',item.context['controller']);self.assertEqual([],item.targets)
        return session,aura,land,private

    def changed_controller_result(self,name,seed,payment,*,mill=False):
        session,aura,land,private=self.original(name,seed,payment)
        before_libraries={seat:len(session.state.players[seat].zones['library']) for seat in 'ABCD'}
        session.engine.change_control(land.object_id,'D');self.assertEqual('D',session.state.cards[land.object_id].controller)
        self.checkpoint(session);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        if mill:
            self.assertEqual(before_libraries['D']-3,len(session.state.players['D'].zones['library']))
            self.assertEqual(before_libraries['B'],len(session.state.players['B'].zones['library']))
        else:self.assertEqual([40,40,40,38],[session.state.players[s].life for s in 'ABCD'])
        self.private_replay(session,private)

    def test_actual_corrupted_roots_reads_current_controller_instead_of_tap_event_player(self):
        self.changed_controller_result('Corrupted Roots',751900,{'B':1})

    def test_actual_chronic_flooding_mills_current_controller_instead_of_tap_event_player(self):
        self.changed_controller_result('Chronic Flooding',751901,{'U':1,'C':1},mill=True)

    def test_actual_psychic_venom_damages_current_controller_instead_of_tap_event_player(self):
        self.changed_controller_result('Psychic Venom',751902,{'U':1,'C':1})

    def test_three_original_results_use_departure_lki_instead_of_returned_incarnation(self):
        for index,(name,payment,mill) in enumerate((('Corrupted Roots',{'B':1},False),
                ('Chronic Flooding',{'U':1,'C':1},True),('Psychic Venom',{'U':1,'C':1},False))):
            session,aura,land,private=self.original(name,751910+index,payment)
            before={seat:len(session.state.players[seat].zones['library']) for seat in 'ABCD'}
            session.engine.change_control(land.object_id,'D');old=session.state.cards[land.object_id].logical_object_id
            session.engine.move_card(land.object_id,'graveyard',log=False)
            self.assertIn('occurrence_controller_information_lki',session.state.stack[-1].context)
            session.engine.move_card(land.object_id,'battlefield',controller='B',log=False)
            self.assertNotEqual(old,session.state.cards[land.object_id].logical_object_id)
            self.checkpoint(session);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            if mill:
                self.assertEqual(before['D']-3,len(session.state.players['D'].zones['library']))
                self.assertEqual(before['B'],len(session.state.players['B'].zones['library']))
            else:self.assertEqual([40,40,40,38],[session.state.players[s].life for s in 'ABCD'])
            self.private_replay(session,private)

    def test_source_departure_keeps_result_and_missing_controller_lki_rolls_back(self):
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        session,aura,land,private=self.original('Corrupted Roots',751913,{'B':1})
        session.engine.change_control(land.object_id,'D');session.engine.move_card(aura.object_id,'graveyard',log=False)
        self.checkpoint(session);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([40,40,40,38],[session.state.players[s].life for s in 'ABCD']);self.private_replay(session,private)
        session,aura,land,private=self.original('Chronic Flooding',751914,{'U':1,'C':1})
        session.engine.change_control(land.object_id,'D');session.engine.move_card(land.object_id,'graveyard',log=False)
        item=session.state.stack[-1];self.assertIn('occurrence_controller_information_lki',item.context)
        item.context.pop('occurrence_controller_information_lki');self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        # The read-only resolver is exact; no event-time or trigger-controller fallback.
        from quorune.target_controller_information import resolve_occurrence_controller_information
        with self.assertRaisesRegex(ValueError,'departure controller information is unavailable'):
            resolve_occurrence_controller_information(session.engine,{'kind':'occurrence_controller_information','schema_version':1},item)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_new_compiler_reference_and_legacy_locked_shapes_are_distinct_and_closed(self):
        from quorune.rules.tap_state_event_capability_shapes import tap_state_event_player_node_capabilities
        from types import SimpleNamespace
        from quorune.rules.occurrence_object_results import occurrence_object_runtime_dependencies
        information={'kind':'occurrence_controller_information','schema_version':1}
        effect={'op':'mill','player':information,'count':3}
        arguments={'target_schema':None,'mechanic_ids':('tap-state-event-player-result','mill')}
        required=tap_state_event_player_node_capabilities(effects=(effect,),**arguments)
        self.assertIn('resolution.target_controller_information',required)
        legacy=tap_state_event_player_node_capabilities(effects=({**effect,'player':'$context.controller'},),**arguments)
        self.assertNotIn('resolution.target_controller_information',legacy)
        for reference in ({**information,'schema_version':True},{**information,'extra':1},'$context.owner'):
            self.assertEqual((),tap_state_event_player_node_capabilities(effects=({**effect,'player':reference},),**arguments))
        for event in ('card.drawn','creature.dies'):
            with self.assertRaises(ValueError):occurrence_object_runtime_dependencies(SimpleNamespace(event=event,
                effects=(effect,),target_schema=None,coverage=('tap-state-event-player-result',)))

    def test_controller_information_owner_is_required_independently_at_runtime(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for index,name in enumerate(('Corrupted Roots','Chronic Flooding','Psychic Venom')):
            session=self.session(751920+index);aura=self.add(session.engine,name,zone='hand')
            programs=[p for p in session.engine.semantics.programs_for_oracle(aura.oracle_id) if p.event in {'permanent.tap','permanent.untap'}]
            self.assertEqual(1,len(programs));program=programs[0];cap='resolution.target_controller_information'
            self.assertIn(cap,program.capability_dependencies)
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            binding=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict'],name);self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])

    def test_verified_controller_information_withdrawal_blocks_corrected_originals(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        changed=deepcopy(raw);cap='resolution.target_controller_information'
        next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn controller information authority'])
        registry=CapabilityRegistry(changed);decl=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Corrupted Roots','Chronic Flooding','Psychic Venom'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers))
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_legacy_compiler_binding_and_missing_runtime_minimum_mutants_are_killed(self):
        from unittest.mock import patch
        import quorune.compiler.tap_state_event_bindings as compiler
        original=compiler.tap_state_event_player_effect_template
        def event_time(*args,**kwargs):
            value=original(*args,**kwargs)
            if value is None:return None
            template,effects,schema,mechanics=value
            field='target' if effects[0]['op']=='damage' else 'player'
            return template,({**effects[0],field:'$context.controller'},),schema,mechanics
        cases=(('quorune.compiler.tap_state_event_bindings.tap_state_event_player_effect_template',event_time,
                'test_actual_corrupted_roots_reads_current_controller_instead_of_tap_event_player'),
            ('quorune.rules.occurrence_object_results.occurrence_object_runtime_dependencies',lambda *a:(),
                'test_controller_information_owner_is_required_independently_at_runtime'))
        for target,replacement,method in cases:
            with patch(target,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
