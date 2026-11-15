"""CR601.2c/608.2b-c: target roles share one simultaneous instruction."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from common import ROOT
import test_recipient_control as recipients
import test_fixed_creature_power_damage as records
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.compiler.compound_target_sets import compound_target_set_effect_template
from quorune.rules.compound_target_sets import compound_target_set_node_capabilities, COMPOUND_TARGET_SET_CAPABILITY as CAPABILITY
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import ReadOnlyHandlerContext,SemanticNodeError
from quorune.semantic_runtime.fixed_target_set_handlers import DestroyPermanentTargetSetHandler
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database,compact_ci_fixture_paths,_card_payload

NAMES=('Spiteful Blow','Stomp and Howl','Churning Eddy','Grip of Desolation','Plague Spores','Fumarole',
    'Peel from Reality','Aether Tradewinds','Boom // Bust','Reign of Chaos','Hull Breach','Decimate','Rite of Undoing','Goblin Grenadiers')


class CompoundTargetCompilerTests(unittest.TestCase):
    def test_roles_keep_independent_constraints_and_one_batch_operation(self):
        result=compound_target_set_effect_template('Destroy target artifact, target creature, target enchantment, and target land.')
        self.assertEqual(4,len(result[2]['groups']));self.assertEqual(1,len(result[1]))
        effect,=result[1];self.assertEqual(('destroy_targets',4,True),(effect['op'],effect['maximum_targets'],effect['target_roles']))
        self.assertIn(CAPABILITY,compound_target_set_node_capabilities(effects=result[1],target_schema=result[2],mechanic_ids=result[3]))
        malformed={'groups':[{**g,'count':True} for g in result[2]['groups']]}
        self.assertEqual((),compound_target_set_node_capabilities(effects=result[1],target_schema=malformed,mechanic_ids=result[3]))
        bounce=compound_target_set_effect_template("Return target creature you control and target creature you don't control to their owners' hands.")
        self.assertEqual(['you','opponent'],[g['controller_relation'] for g in bounce[2]['groups']])
        no_regen=compound_target_set_effect_template("Destroy target nonblack creature and target land. They can't be regenerated.")
        self.assertTrue(no_regen[1][0]['regeneration_prohibited'])
        self.assertEqual(['B'],no_regen[2]['groups'][0]['colors_none'])
        for text in ('Destroy target artifact and sacrifice target creature.',
                "Return target creature card from a graveyard and target creature on the battlefield to their owners' hands.",
                'Destroy target artifact and target enchantment. Draw a card.',
                'Destroy target land and target land of an opponent\'s choice.',
                'Destroy up to one target creature and target land.'):
            self.assertIsNone(compound_target_set_effect_template(text),text)

    def test_role_projection_deduplicates_only_the_new_instruction_and_keeps_flags_closed(self):
        context=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='Compound target contract',
            seats='ABCD',active_seats='ABCD',apnap_order='ABCD')
        handler=DestroyPermanentTargetSetHandler()
        raw={'op':'destroy_targets','cards':['same','same','land'],'maximum_targets':4,'target_roles':True}
        plan=handler.lower(raw,context);self.assertEqual(1,len(plan.intents))
        self.assertEqual(('same','land'),plan.intents[0].object_refs)
        prohibited=handler.lower({**raw,'regeneration_prohibited':True},context)
        self.assertTrue(prohibited.intents[0].regeneration_prohibited)
        with self.assertRaises(SemanticNodeError):handler.lower({k:v for k,v in raw.items() if k!='target_roles'},context)
        for change in ({'target_roles':1},{'maximum_targets':True},{'maximum_targets':5},
                {'announced_target_count':True},{'regeneration_prohibited':False},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(SemanticNodeError):handler.lower({**raw,**change},context)


class CompoundTargetNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        names={*NAMES,'Sliver Hivelord','Generic Bound Plains','Grizzly Bears','Counterspell','Ornithopter','Fervor',
            'Sol Ring','Forest','Plains','Mountain','Unsummon','Boomerang','Island','Regenerate','Darksteel Myr',
            'Liquimetal Coating','Zulaport Cutthroat','Solemn Simulacrum','Dauthi Voidwalker','Casualties of War'}
        paths=(*compact_ci_fixture_paths(root=ROOT),ROOT/'tests/fixtures/bound-effect-program-cards.json')
        documents=[json.loads(p.read_text(encoding='utf-8')) for p in paths]
        cards={r['name']:r for value in documents for r in value['cards'] if r['name'] in names}
        assert set(cards)==names,names-set(cards)
        control=replace(records.card_record('Choose one or both —\n• Destroy target artifact and target enchantment.\n• Return target creature to its owner\'s hand.'),
            name='Compound mode scope control',oracle_id='f016eb7e-5382-4f02-a90d-3c7e0a502035')
        cards[control.name]=_card_payload(control)
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        document=json.loads((ROOT/'tests/fixtures/recipient-control-cards.json').read_text(encoding='utf-8'))
        ids={r['oracle_id'] for r in cards.values()}
        rulings={tuple(sorted(r.items())):r for value in documents for r in value.get('rulings',[]) if r['oracle_id'] in ids}
        document.update(cards=list(cards.values()),rulings=list(rulings.values()))
        fixture=folder/'compound.json';fixture.write_text(json.dumps(document),encoding='utf-8')
        path=folder/'compound.sqlite3';build_fixture_database([fixture],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Compound target roles',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,before):
        s=self.session(2021035+len(name));source=self.add(s.engine,name,zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        extra=before(s,source)
        self.assert_closed(s,source);s.state.config.semantic_policy='trusted_only'
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,private,extra

    def cast_roles(self,s,spell,targets,**fields):
        self.priority(s,'A');row=self.action(s,'A',spell)
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto',**fields}
        self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_complete_originals_bind_and_coverage_independent_role_authority_cannot_be_omitted(self):
        for name in NAMES:
            with self.subTest(name=name):
                card=self.db.lookup(name,fuzzy=False);ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                program=_compile_best_available_card_program_from_ir(self.db,card,ir,semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
                self.assertEqual('capability_closed',bound['trust_basis'])
        s=self.session(202103501);source=self.add(s.engine,'Plague Spores',zone='hand')
        p,=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve' and p.effects]
        unlabelled=replace(p,coverage=[])
        self.assertTrue(bind_semantic_program_runtime(unlabelled,capability_registry=self.registry,profile='commander_review')['strict'])
        for capability in (CAPABILITY,'permanent.destroy.regeneration_prohibition'):
            deps=tuple(c for c in p.capability_dependencies if c!=capability)
            changed=replace(unlabelled,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+capability,bound['blockers'])

    def test_actual_decimate_accepts_one_object_in_two_roles_and_moves_it_once(self):
        def before(s,source):
            return {'both':self.add(s.engine,'Ornithopter',seat='B'),
                'enchantment':self.add(s.engine,'Fervor',seat='D'),'land':self.add(s.engine,'Forest',seat='B')}
        s,spell,private,extra=self.scene('Decimate',before)
        both,ench,land=(extra[k] for k in ('both','enchantment','land'))
        row=self.action(s,'A',spell)
        self.reject(s,'A',{'action_id':row['id'],'targets':[both.ref,ench.ref,land.ref],'pay':'auto'})
        self.cast_roles(s,spell,[both.ref,both.ref,ench.ref,land.ref])
        self.assertTrue(all(s.state.cards[c.object_id].zone=='battlefield' for c in extra.values()))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in extra.values()))
        self.assertEqual(1,s.state.players['B'].zones['graveyard'].count(both.object_id))
        self.finish(s,private)

    def test_actual_grip_keeps_a_legal_role_after_response_and_preserves_the_paid_cast(self):
        for all_invalid in (False,True):
            with self.subTest(all_invalid=all_invalid):
                def before(s,source):
                    for i in range(3):self.add(s.engine,'Island',seat='B',ref='grip-response-blue-'+str(i))
                    return {'creature':self.add(s.engine,'Grizzly Bears',seat='B'),'land':self.add(s.engine,'Forest',seat='D'),
                        'bounce':self.add(s.engine,'Unsummon',seat='B',zone='hand'),'land_bounce':self.add(s.engine,'Boomerang',seat='B',zone='hand')}
                s,spell,private,extra=self.scene('Grip of Desolation',before)
                self.cast_roles(s,spell,[extra['creature'].ref,extra['land'].ref])
                self.cast(s,'B',extra['bounce'],[extra['creature'].ref]);self.resolve_top(s)
                if all_invalid:
                    self.cast(s,'B',extra['land_bounce'],[extra['land'].ref]);self.resolve_top(s)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('hand',s.state.cards[extra['creature'].object_id].zone)
                self.assertEqual('hand' if all_invalid else 'exile',s.state.cards[extra['land'].object_id].zone)
                self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.finish(s,private)

    def test_actual_destination_choice_holds_the_whole_batch_and_uses_departing_sources(self):
        def before(s,source):
            providers=[self.add(s.engine,'Dauthi Voidwalker',ref='compound-void-source-'+str(i)) for i in range(2)]
            return {'providers':providers,'land':self.add(s.engine,'Forest',seat='B')}
        s,spell,private,extra=self.scene('Spiteful Blow',before)
        # Existing fixture registration supplies only its actually trusted
        # static clause. Dauthi's unrepresented activation earns no card claim.
        for source in extra['providers']:
            programs=s.engine.semantics.programs_for_oracle(source.oracle_id)
            self.assertTrue(any(s.engine.semantic_program_is_current_trusted(p)
                and any('replacement' in h.get('handler_id','') for h in p.handlers) for p in programs))
        source=extra['providers'][0];land=extra['land']
        self.cast_roles(s,spell,[source.ref,land.ref])
        self.assertEqual('replacement.order',self.resolve(s).kind)
        self.assertEqual(['pilot:B'],list(s.pending_principals()))
        for c in (source,land):self.assertEqual('battlefield',s.state.cards[c.object_id].zone)
        options=s.packet('pilot:B',full=True)['decision']['ctx']['options'];self.assertEqual(2,len(options))
        command={'action_id':'choose','replacement':options[0]['id']}
        self.reject(s,'A',command);self.reject(s,'B',{'action_id':'choose','replacement':'unregistered'})
        s=self.replay(s,load=True)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(('exile',1),(s.state.cards[land.object_id].zone,s.state.cards[land.object_id].counters.get('void',0)))
        self.finish(s,private)

    def test_actual_return_roles_use_current_controller_predicates_and_physical_owners(self):
        def before(s,source):
            own=self.add(s.engine,'Grizzly Bears');own.owner='D'
            foreign=self.add(s.engine,'Grizzly Bears',seat='B',ref='foreign-controlled-body');foreign.owner='A'
            return {'own':own,'foreign':foreign}
        s,spell,private,extra=self.scene('Peel from Reality',before)
        row=self.action(s,'A',spell)
        self.reject(s,'A',{'action_id':row['id'],'targets':[extra['foreign'].ref,extra['own'].ref],'pay':'auto'})
        self.cast_roles(s,spell,[extra['own'].ref,extra['foreign'].ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertIn(extra['own'].object_id,s.state.players['D'].zones['hand'])
        self.assertIn(extra['foreign'].object_id,s.state.players['A'].zones['hand'])
        self.finish(s,private)

    def test_actual_destruction_distinguishes_regeneration_prohibition_and_indestructible(self):
        for name,indestructible in (('Spiteful Blow',False),('Plague Spores',False),('Plague Spores',True)):
            with self.subTest(name=name,indestructible=indestructible):
                def before(s,source):
                    self.add(s.engine,'Forest',seat='B')
                    self.add(s.engine,'Plains',seat='B',ref='regeneration-generic-input')
                    return {'body':self.add(s.engine,'Darksteel Myr' if indestructible else 'Grizzly Bears',seat='B'),
                        'land':self.add(s.engine,'Plains',seat='D'),'regen':self.add(s.engine,'Regenerate',seat='B',zone='hand')}
                s,spell,private,extra=self.scene(name,before)
                self.cast_roles(s,spell,[extra['body'].ref,extra['land'].ref])
                self.cast(s,'B',extra['regen'],[extra['body'].ref]);self.resolve_top(s)
                self.assertEqual(1,s.state.cards[extra['body'].object_id].regeneration_shields)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                expected='battlefield' if indestructible or name=='Spiteful Blow' else 'graveyard'
                self.assertEqual(expected,s.state.cards[extra['body'].object_id].zone)
                self.assertEqual('graveyard',s.state.cards[extra['land'].object_id].zone)
                self.finish(s,private)

    def test_actual_simultaneous_departure_keeps_the_dying_observer_for_every_creature(self):
        def before(s,source):
            observer=self.add(s.engine,'Zulaport Cutthroat');observer.owner='D'
            victim=self.add(s.engine,'Solemn Simulacrum');victim.owner='B'
            return {'observer':observer,'victim':victim,'coating':self.add(s.engine,'Liquimetal Coating'),
                'enchantment':self.add(s.engine,'Fervor'),'land':self.add(s.engine,'Forest')}
        s,spell,private,extra=self.scene('Decimate',before)
        for c in extra.values():self.assert_closed(s,c)
        self.priority(s,'A')
        coating=extra['coating']
        ability=next(a for a in s.engine._activated_abilities(coating) if not a.mana_ability)
        result=s.act('pilot:A',{'action_id':'activate:'+coating.ref+':'+ability.ability_id,'targets':[extra['observer'].ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        hand=len(s.state.players['A'].zones['hand'])
        self.cast_roles(s,spell,[extra[k].ref for k in ('observer','victim','enchantment','land')])
        for _ in range(16):
            d=self.resolve(s)
            if d is None:break
            if d.kind=='semantic.choice':
                command={'action_id':'choose','choice':'draw'};self.reject(s,'B',command)
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                continue
            self.assertEqual('trigger.order',d.kind)
            refs=[item.ref for batch in s.state.pending_trigger_batches for item in batch.items if item.controller=='A']
            result=s.act('pilot:A',{'action_id':'order','triggers':refs});self.assertTrue(result.ok,result.summary)
        else:self.fail('Compound death triggers did not settle')
        self.assertEqual((42,38,38,38),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertEqual(hand,len(s.state.players['A'].zones['hand']))
        self.assertIn(extra['observer'].object_id,s.state.players['D'].zones['graveyard'])
        self.assertIn(extra['victim'].object_id,s.state.players['B'].zones['graveyard'])
        self.finish(s,private)

    def test_actual_modal_grouped_branch_uses_only_its_selected_roles(self):
        def before(s,source):return {'artifact':self.add(s.engine,'Sol Ring',seat='B'),'enchantment':self.add(s.engine,'Fervor',seat='D')}
        s,spell,private,extra=self.scene('Hull Breach',before)
        self.cast_roles(s,spell,[extra['artifact'].ref,extra['enchantment'].ref],modes=['mode_3'])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in extra.values()));self.finish(s,private)
        def scoped_before(s,source):
            return {'artifact':self.add(s.engine,'Sol Ring',seat='B'),'enchantment':self.add(s.engine,'Fervor',seat='D'),
                'creature':self.add(s.engine,'Grizzly Bears',seat='B')}
        s,spell,private,extra=self.scene('Compound mode scope control',scoped_before)
        self.cast_roles(s,spell,[extra[k].ref for k in ('artifact','enchantment','creature')],modes=['mode_1','mode_2'])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(['graveyard','graveyard','hand'],[s.state.cards[extra[k].object_id].zone for k in ('artifact','enchantment','creature')])
        self.finish(s,private)

    def test_fresh_sequential_batch_and_duplicate_projection_omissions_are_killed(self):
        original=DestroyPermanentTargetSetHandler.lower
        def sequential(handler,effect,context):
            plan=original(handler,effect,context)
            if effect.get('target_roles') and plan.intents:
                intent,=plan.intents
                return replace(plan,intents=tuple(replace(intent,object_refs=(ref,)) for ref in intent.object_refs))
            return plan
        with patch.object(DestroyPermanentTargetSetHandler,'lower',sequential),self.assertRaises(AssertionError):
            type(self)('test_actual_simultaneous_departure_keeps_the_dying_observer_for_every_creature').test_actual_simultaneous_departure_keeps_the_dying_observer_for_every_creature()
        import quorune.semantic_runtime.fixed_target_set_handlers as owner
        project=owner._fixed_target_fields
        def repeated(effect,context,**options):
            values=project(effect,context,**options)
            return (tuple(effect['cards']),*values[1:]) if effect.get('target_roles') else values
        with patch.object(owner,'_fixed_target_fields',repeated):
            result=unittest.TestResult()
            type(self)('test_actual_decimate_accepts_one_object_in_two_roles_and_moves_it_once').run(result)
        self.assertFalse(result.wasSuccessful())


if __name__=='__main__':unittest.main()
