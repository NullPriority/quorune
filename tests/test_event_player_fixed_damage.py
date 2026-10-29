"""Fixed damage keeps the ability source and the sealed occurrence recipient."""

from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
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
from scripts.build_test_database import build_fixture_database


class EventPlayerFixedDamageCompilerTests(unittest.TestCase):
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
