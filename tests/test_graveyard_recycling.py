"""Independent recycling contract; staged outside shared test inventory until routed."""
import unittest
from types import SimpleNamespace
import json,tempfile
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase,CardRecord
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database
from quorune.graveyard_recycling_model import (
    GraveyardRecyclingSpec,
    GRAVEYARD_RECYCLING_OPERATION as OP, GRAVEYARD_RECYCLING_CAPABILITY as CAP,
    RANDOM_GRAVEYARD_BOTTOM_CAPABILITY, RANDOM_GRAVEYARD_BOTTOM_VERSION,
)
from quorune.compiler.graveyard_recycling_templates import graveyard_recycling_template
from quorune.rules.graveyard_recycling import (
    graveyard_recycling_node_capabilities, graveyard_recycling_runtime_dependencies,
    resolve_graveyard_recycling,
)
from quorune.semantic_runtime.graveyard_recycling import GraveyardRecyclingHandler, GraveyardRecyclingIntent
from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticNodeError

RANDOM_BOTTOM_TEXT = (
    'Up to one target player puts all the cards from their graveyard '
    'on the bottom of their library in a random order.'
)
EVOKE_FIXTURE = ROOT / 'tests/fixtures/hand-exile-evoke-cards.json'


class GraveyardRecyclingModelTests(unittest.TestCase):
    def effect(self, scope='controller', player='A'):
        return {'op':OP,'recycling':GraveyardRecyclingSpec(scope).to_dict(),'player':player}

    def test_closed_scope_version_and_immutable_intent_reject_unknown_or_redirected_fields(self):
        for scope in ('controller','target_player','all_players'):
            spec=GraveyardRecyclingSpec(scope);self.assertEqual(spec,GraveyardRecyclingSpec.from_dict(spec.to_dict()))
        for value in ({'scope':'all_players','schema_version':True}, {'scope':'all_players','schema_version':2},
                {'scope':'opponents','schema_version':1}, {'scope':'controller','schema_version':1,'count':2}):
            with self.subTest(value=value),self.assertRaises(ValueError):GraveyardRecyclingSpec.from_dict(value)
        raw={'event':'selection','choice':'keep'}
        intent=GraveyardRecyclingIntent('A','A',GraveyardRecyclingSpec('controller'),'resolution',(raw,))
        raw['choice']='changed';self.assertEqual('keep',intent.replacement_selections[0]['choice'])
        with self.assertRaises(ValueError):GraveyardRecyclingIntent('A','B',GraveyardRecyclingSpec('controller'),'resolution')

    def test_whole_clause_grammar_rejects_selective_cards_extra_zones_and_result_siblings(self):
        texts=('Shuffle your graveyard into your library.', 'Target player shuffles their graveyard into their library.',
            'Each player shuffles their graveyard into their library.')
        for scope,text in zip(('controller','target_player','all_players'),texts):
            template=graveyard_recycling_template(text);self.assertEqual(scope,template.spec.scope)
            _,effects,schema,mechanics=template.compiled()
            deps=graveyard_recycling_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics)
            self.assertIn(CAP,deps);self.assertIn('zone.change.destination_replacement',deps)
            self.assertEqual(scope=='target_player','target.revalidate_resolution' in deps)
        for text in ('Target player shuffles up to two cards from their graveyard into their library.',
                'Shuffle your hand, graveyard, and permanents into your library.', 'Each opponent shuffles their graveyard into their library.',
                'Target player shuffles their graveyard into their library. Draw a card.'):
            with self.subTest(text=text):self.assertIsNone(graveyard_recycling_template(text))
        hand=graveyard_recycling_template('Shuffle your hand and graveyard into your library.')
        self.assertEqual(('hand','graveyard'),hand.spec.origins)
        self.assertEqual(2,hand.spec.schema_version)

    def test_random_bottom_requires_its_optional_target_and_independent_runtime_minimum(self):
        template = graveyard_recycling_template(RANDOM_BOTTOM_TEXT)
        self.assertEqual(RANDOM_GRAVEYARD_BOTTOM_VERSION, template.spec.schema_version)
        _, effects, schema, mechanics = template.compiled()
        program = SimpleNamespace(effects=effects, handlers=(), target_schema=schema, coverage=())
        required = graveyard_recycling_runtime_dependencies(program)
        self.assertIn(RANDOM_GRAVEYARD_BOTTOM_CAPABILITY, required)
        self.assertIn(CAP, required)
        self.assertEqual((0, 1), (schema['groups'][0]['min'], schema['groups'][0]['max']))
        for malformed in (False, 1, 2):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                program.target_schema = {'groups': [{**schema['groups'][0], 'min': malformed}]}
                graveyard_recycling_runtime_dependencies(program)
        for text in (RANDOM_BOTTOM_TEXT.replace('Up to one target', 'Each'),
                     RANDOM_BOTTOM_TEXT.replace('all the cards', 'all creature cards')):
            self.assertIsNone(graveyard_recycling_template(text))

    def test_compiled_minimum_is_payload_driven_and_rejects_forged_scope_or_broad_target(self):
        _,effects,schema,mechanics=graveyard_recycling_template('Target player shuffles their graveyard into their library.').compiled()
        program=SimpleNamespace(effects=effects,handlers=(),target_schema=schema,coverage=())
        self.assertIn(CAP,graveyard_recycling_runtime_dependencies(program))
        for patch in ({'count':2},{'count':True},{'player_relation':'opponent'},{'optional':True},{'unknown':1}):
            with self.subTest(patch=patch):
                program.target_schema={**schema,**patch}
                self.assertEqual((),graveyard_recycling_node_capabilities(effects=effects,target_schema=program.target_schema,mechanic_ids=mechanics))
                with self.assertRaises(ValueError):graveyard_recycling_runtime_dependencies(program)
        program.target_schema=schema;program.effects=({**effects[0],'player':'$controller'},)
        with self.assertRaises(ValueError):graveyard_recycling_runtime_dependencies(program)

    def test_semantic_handler_preserves_actor_target_and_canonical_replacement_selections(self):
        context=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='resolution',seats='ABCD',active_seats='ABCD',apnap_order='ABCD')
        handler=GraveyardRecyclingHandler();effect=self.effect('target_player','C')
        intent,=handler.lower(effect,context).intents
        self.assertEqual(('A','C','target_player'),(intent.actor,intent.player,intent.recycling.scope))
        for changed in ({'player':'X'},{'unknown':True},{'recycling':{'scope':'controller','schema_version':1}}):
            with self.subTest(changed=changed),self.assertRaises(SemanticNodeError):handler.lower({**effect,**changed},context)

    def host(self):
        calls=[];cards={}
        players={p:SimpleNamespace(zones={'graveyard':[]}) for p in 'ABCD'}
        for player in 'AB':
            oid='grave-'+player;players[player].zones['graveyard'].append(oid)
            cards[oid]=SimpleNamespace(ref=oid+'@1',zone='graveyard',owner=player)
        def move(intent):calls.append(('move',intent));return intent.object_refs
        return SimpleNamespace(state=SimpleNamespace(players=players,cards=cards),
            public_zone_move_active_seats=lambda:tuple('ABCD'),public_zone_move_apnap_order=lambda:tuple('BCDA'),
            move_objects_simultaneously_intent=move,shuffle_library=lambda player,**kw:calls.append(('shuffle',player))),calls

    def test_all_player_batch_moves_simultaneously_then_shuffles_each_library_once_even_empty(self):
        host,calls=self.host();result=resolve_graveyard_recycling(host,self.effect('all_players','all'),actor='A',reason='resolution')
        self.assertEqual(('grave-B@1','grave-A@1'),result)
        self.assertEqual(['move','shuffle','shuffle','shuffle','shuffle'],[c[0] for c in calls])
        intent=calls[0][1];self.assertEqual(('graveyard',),intent.expected_zones);self.assertEqual('library',intent.destination)
        self.assertEqual(list('BCDA'),[c[1] for c in calls[1:]])
        host.state.players['A'].zones['graveyard'].clear();calls.clear()
        resolve_graveyard_recycling(host,self.effect(),actor='A',reason='resolution')
        self.assertEqual([('shuffle','A')],calls)

    def test_entire_public_snapshot_validates_before_any_move_or_shuffle(self):
        for defect in ('stale','wrong_owner','duplicate','missing','participant_order'):
            host,calls=self.host()
            if defect=='stale':host.state.cards['grave-B'].zone='hand'
            elif defect=='wrong_owner':host.state.cards['grave-B'].owner='A'
            elif defect=='duplicate':host.state.players['B'].zones['graveyard'].append('grave-B')
            elif defect=='missing':del host.state.cards['grave-B']
            else:host.public_zone_move_apnap_order=lambda:tuple('AACD')
            with self.subTest(defect=defect),self.assertRaises(ValueError):
                resolve_graveyard_recycling(host,self.effect('all_players','all'),actor='A',reason='resolution')
            self.assertEqual([],calls)

    def test_canonical_movement_suspension_cannot_shuffle_before_destination_choices_commit(self):
        class Suspended(Exception):pass
        host,calls=self.host()
        def suspend(intent):calls.append(('pending',intent));raise Suspended()
        host.move_objects_simultaneously_intent=suspend
        with self.assertRaises(Suspended):resolve_graveyard_recycling(host,self.effect(),actor='A',reason='resolution')
        self.assertEqual(['pending'],[c[0] for c in calls])
        calls.clear();host.move_objects_simultaneously_intent=lambda intent:()
        resolve_graveyard_recycling(host,self.effect(),actor='A',reason='resolution')
        self.assertEqual([('shuffle','A')],calls)


ORIGINALS=('Clear the Mind','Cranial Archive',"Feldon's Cane",'Learn from the Past',
    'Mnemonic Nexus','Reminisce','Struggle // Survive','Thran Foundry')


class GraveyardRecyclingCompilerTests(unittest.TestCase):
    def record(self,text):
        return CardRecord(oracle_id='fixture:recycling-contract',name='Recycling contract',mana_cost='{2}{U}',mana_value=3,
            type_line='Sorcery',oracle_text=text,power=None,toughness=None,loyalty=None,defense=None,colors=('U',),
            color_identity=('U',),keywords=(),produced_mana=(),layout='normal',released_at='2000-01-01',
            legalities={'commander':'legal'},faces=(),raw={})

    def compile(self,text):
        return compile_oracle_card(self.record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')

    def test_complete_whole_graveyard_instructions_preserve_draw_siblings_and_actual_source_span(self):
        for text in ('Shuffle your graveyard into your library.', 'Each player shuffles their graveyard into their library.',
                'Target player shuffles their graveyard into their library. Draw a card.'):
            with self.subTest(text=text):
                ir=self.compile(text);self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertTrue(any(OP==e.get('op') for node in ir.faces[0].nodes for e in node.effects))
                for node in ir.faces[0].nodes:self.assertEqual(text[node.span.start:node.span.end],node.text)
        for text in ('Target player shuffles their graveyard and hand into their library.',
                'Target player shuffles three cards from their graveyard into their library.',
                'Target player shuffles their graveyard into the next game.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text).status)

    def test_all_eight_original_cards_use_normal_complete_stored_and_runtime_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'originals.sqlite3';build_fixture_database(ROOT/'tests/fixtures/graveyard-recycling-cards.json',path)
            registry=load_default_capability_registry()
            with CardDatabase(path) as db:
                for name in ORIGINALS:
                    with self.subTest(name=name):
                        card=db.lookup(name);ir=compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review')
                        self.assertEqual('exact',ir.status,ir.material_residuals)
                        p=_compile_best_available_card_program_from_ir(db,card,ir,semantic_registry=SemanticRegistry(),capability_registry=registry,capability_profile='commander_review')
                        self.assertEqual('capability_closed',p.trust_closure['trust_basis'],p.trust_closure['blockers'])
                        b=bind_card_program_runtime(p,capability_registry=registry,profile='commander_review')
                        self.assertTrue(b['strict_capability_ready'],b['blockers'])

    def test_withdrawn_shuffle_authority_does_not_inherit_the_old_single_card_move_owner(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['Withheld complete shuffle owner'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.record('Shuffle your graveyard into your library.'),capability_registry=registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_endurance_closes_current_trigger_without_retaining_its_archived_payload(self):
        from quorune.oracle_ir import register_generated_programs

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'endurance.sqlite3'
            build_fixture_database(EVOKE_FIXTURE, path)
            registry = load_default_capability_registry()
            with CardDatabase(path) as db:
                card = db.lookup('Endurance', fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                trigger, = (node for node in ir.faces[0].nodes if node.event == 'permanent.enter.self')
                self.assertIn(RANDOM_GRAVEYARD_BOTTOM_CAPABILITY, trigger.capability_dependencies)
                self.assertEqual(card.oracle_text.splitlines()[2], card.oracle_text[trigger.span.start:trigger.span.end])
                for builtins in (False, True):
                    semantics = SemanticRegistry(include_builtin_packs=builtins)
                    register_generated_programs(
                        db, semantics, (card,), capability_registry=registry, capability_profile='commander_review',
                        promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                        promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
                    )
                    program = _compile_best_available_card_program_from_ir(
                        db, card, ir, semantic_registry=semantics, capability_registry=registry,
                        capability_profile='commander_review',
                    )
                    binding = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
                    self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
                    self.assertEqual('capability_closed', binding['trust_basis'])
                    programs = [p for p in semantics.programs_for_oracle(card.oracle_id)
                                if p.event == 'permanent.enter.self' and p.target_schema]
                    trigger_program, = programs
                    self.assertEqual(OP, trigger_program.effects[0]['op'])
                    deps = tuple(cap for cap in trigger_program.capability_dependencies
                                 if cap != RANDOM_GRAVEYARD_BOTTOM_CAPABILITY)
                    stripped = replace(trigger_program, coverage=(), capability_dependencies=deps,
                                       capability_closure=registry.closure(deps, profile='commander_review').to_dict())
                    without = bind_semantic_program_runtime(stripped, capability_registry=registry, profile='commander_review')
                    self.assertFalse(without['strict'])
                    self.assertIn('capability:undeclared_runtime_dependency:' + RANDOM_GRAVEYARD_BOTTOM_CAPABILITY,
                                  without['blockers'])
                from quorune.card_programs.alternative_price_overlay import shadowed_reviewed_hand_exile_evoke_keys

                generated = semantics.programs_for_oracle(card.oracle_id)
                archived = SemanticRegistry().programs_for_oracle(card.oracle_id)
                self.assertEqual(
                    {f'{card.oracle_id}:spell:front', f'{card.oracle_id}:trigger:evoke'},
                    shadowed_reviewed_hand_exile_evoke_keys(card, generated, archived),
                )
                for ability, edit in (
                    ('spell:front', {'cost_schema': {'alternate_costs': [{'id': 'evoke', 'kind': 'alternate'}]}}),
                    ('trigger:evoke', {'effects': [{'op': 'sacrifice_if_present', 'card': '$other'}]}),
                ):
                    with self.subTest(archived_ability=ability):
                        changed = tuple(replace(p, **edit) if p.ability_id == ability else p for p in archived)
                        self.assertEqual(set(), shadowed_reviewed_hand_exile_evoke_keys(card, generated, changed))


class GraveyardRecyclingNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=seat_actions.RecipientControlNativeTests.reject
    finish=seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/graveyard-recycling-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Recycling',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def start(self,source_name,seed,*,activated=False):
        s=self.session(seed);source=self.add(s.engine,source_name,zone='battlefield' if activated else 'hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.assert_closed(s,source)
        self.prepare(s,mana={'C':10,'U':5,'G':5});return s,source,private

    def activate(self,s,source,targets=()):
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(a for a in rows if a['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':offered['id'],'targets':list(targets),'pay':'auto'}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_original_clear_mind_shuffles_target_once_before_controller_draw_even_empty(self):
        for empty in (True,False):
            with self.subTest(empty=empty):
                s,source,private=self.start('Clear the Mind',629201+empty)
                old=None if empty else self.add(s.engine,'Grizzly Bears',seat='B',zone='graveyard')
                before={p:s.state.players[p].stats.get('shuffle_count',0) for p in 'ABCD'}
                draws={p:len(s.state.players[p].draw_history) for p in 'ABCD'};self.checkpoint(s)
                self.cast(s,'A',source,['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(before['B']+1,s.state.players['B'].stats.get('shuffle_count',0))
                self.assertEqual(draws['A']+1,len(s.state.players['A'].draw_history))
                self.assertEqual(draws['B'],len(s.state.players['B'].draw_history))
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                if old is not None:self.assertEqual('library',s.state.cards[old.object_id].zone)
                self.finish(s,private)

    def test_original_nexus_moves_current_graveyards_then_shuffles_all_four_libraries_once(self):
        s,source,private=self.start('Mnemonic Nexus',629210)
        old=[self.add(s.engine,'Grizzly Bears',seat=p,zone='graveyard',ref='recycle-graveyard-'+p) for p in 'AB']
        before={p:s.state.players[p].stats.get('shuffle_count',0) for p in 'ABCD'};self.checkpoint(s)
        self.cast(s,'A',source);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        for p in 'ABCD':self.assertEqual(before[p]+1,s.state.players[p].stats.get('shuffle_count',0))
        for card in old:
            current=s.state.cards[card.object_id];self.assertEqual('library',current.zone);self.assertFalse(current.known_to)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_original_cane_and_foundry_pay_actual_tap_exile_cost_before_recycling(self):
        for name in ("Feldon's Cane",'Thran Foundry','Cranial Archive'):
            with self.subTest(name=name):
                s,source,private=self.start(name,629220+len(name),activated=True)
                target='A' if name=="Feldon's Cane" else 'B'
                old=self.add(s.engine,'Grizzly Bears',seat=target,zone='graveyard')
                before=s.state.players[target].stats.get('shuffle_count',0);draws=len(s.state.players['A'].draw_history)
                self.checkpoint(s);self.activate(s,source,() if name=="Feldon's Cane" else ('B',))
                self.assertEqual('exile',s.state.cards[source.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[old.object_id].zone)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('library',s.state.cards[old.object_id].zone)
                self.assertEqual(before+1,s.state.players[target].stats.get('shuffle_count',0))
                self.assertEqual(draws+(name=='Cranial Archive'),len(s.state.players['A'].draw_history));self.finish(s,private)

    def test_actual_commander_destination_choice_suspends_entire_batch_and_shuffle(self):
        for decline in (True,False):
            with self.subTest(decline=decline):
                s,source,private=self.start('Reminisce',629230+decline)
                commander_id=s.state.players['A'].zones['command'][0];commander=s.state.cards[commander_id]
                old=self.add(s.engine,'Grizzly Bears',zone='graveyard')
                s.engine.move_card(commander_id,'graveyard',reason='Fixture commander graveyard boundary')
                s.engine.permissions.invalidate_current();s.state.pending_decision=None;self.assertTrue(s.engine._stabilize())
                kept=s.act('pilot:A',{'a':'choose','choice':'remain'});self.assertTrue(kept.ok,kept.summary)
                self.prepare(s,mana={'C':10,'U':5});before=s.state.players['A'].stats.get('shuffle_count',0);self.checkpoint(s)
                self.cast(s,'A',source,['A']);self.assertEqual('replacement.order',self.resolve(s).kind)
                self.assertEqual(before,s.state.players['A'].stats.get('shuffle_count',0))
                self.assertEqual('graveyard',s.state.cards[old.object_id].zone);s=self.replay(s,load=True)
                options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                chosen=next(o for o in options if bool(o.get('decline'))==decline)
                command={'action_id':'choose','replacement':chosen['id']};self.reject(s,'B',command)
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual('library' if decline else 'command',s.state.cards[commander_id].zone)
                self.assertEqual('library',s.state.cards[old.object_id].zone)
                self.assertEqual(before+1,s.state.players['A'].stats.get('shuffle_count',0));self.finish(s,private)

    def test_recycling_payload_requires_its_minimum_without_a_mechanic_coverage_hint(self):
        s,source,_=self.start('Reminisce',629240)
        p=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op')==OP for e in p.effects))
        self.assertTrue(bind_semantic_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in p.capability_dependencies if c!=CAP)
        stripped=replace(p,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        result=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,result['blockers'])
        bad=replace(p,effects=[{**p.effects[0],'player':'$controller'}])
        self.assertFalse(bind_semantic_program_runtime(bad,capability_registry=self.registry,profile='commander_review')['strict'])

    def test_canonical_shuffle_owner_omission_is_killed_by_fresh_complete_original_actions(self):
        with patch('quorune.rules.graveyard_recycling.resolve_graveyard_recycling',return_value=()):
            with self.assertRaises(AssertionError):self.test_original_nexus_moves_current_graveyards_then_shuffles_all_four_libraries_once()

    def test_player_hexproof_response_skips_recycling_and_draw_without_refunding_archive_cost(self):
        s,source,private=self.start('Cranial Archive',629250,activated=True)
        self.add(s.engine,'Vedalken Orrery',seat='B');aegis=self.add(s.engine,'Aegis of the Gods',seat='B',zone='hand')
        old=self.add(s.engine,'Grizzly Bears',seat='B',zone='graveyard');self.assert_closed(s,aegis)
        s.state.players['B'].mana_pool.update({'C':4,'W':1})
        before=s.state.players['B'].stats.get('shuffle_count',0);draws=len(s.state.players['A'].draw_history)
        mana=sum(s.state.players['A'].mana_pool.values());self.checkpoint(s);self.activate(s,source,('B',))
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.cast(s,'B',aegis);self.resolve_top(s);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(before,s.state.players['B'].stats.get('shuffle_count',0))
        self.assertEqual(draws,len(s.state.players['A'].draw_history));self.assertEqual('graveyard',s.state.cards[old.object_id].zone)
        self.assertEqual(mana-2,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_empty_target_library_and_graveyard_still_shuffle_once_without_a_target_size_requirement(self):
        s,source,private=self.start('Clear the Mind',629260)
        for oid in tuple(s.state.players['B'].zones['library']):
            s.engine.move_card(oid,'exile',reason='Fixture empty target library')
        self.assertFalse(s.state.players['B'].zones['graveyard'])
        before=s.state.players['B'].stats.get('shuffle_count',0);self.checkpoint(s)
        self.cast(s,'A',source,['B']);self.assertIsNone(self.resolve(s))
        self.assertEqual(before+1,s.state.players['B'].stats.get('shuffle_count',0));self.assertFalse(s.state.players['B'].zones['library'])
        self.finish(s,private)


class RandomGraveyardBottomNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'random-bottom.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/graveyard-recycling-cards.json', EVOKE_FIXTURE,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Random graveyard bottom', [
            DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Isamaru, Hound of Konda'])

    def scene(self, seed, *, target='B'):
        session = self.session(seed)
        source = self.add(session.engine, 'Endurance', zone='hand')
        cards = tuple(self.add(session.engine, name, seat=target, zone='graveyard')
                      for name in ('Grizzly Bears', 'Forest'))
        pitch = self.add(session.engine, 'Grizzly Bears', zone='hand', ref='random-bottom-green-pitch')
        wrong = self.add(session.engine, 'Sol Ring', zone='hand', ref='random-bottom-colorless-pitch')
        private = self.add(session.engine, 'Counterspell', seat='D', zone='hand', ref='random-bottom-private')
        self.assert_closed(session, source)
        return session, source, cards, pitch, wrong, private

    def choose_target(self, session, target):
        decision = self.resolve(session)
        self.assertEqual('semantic.target', decision.kind)
        self.assertEqual(['A'], decision.actors)
        self.reject(session, 'B', {'action_id': 'choose', 'targets': ['B']})
        self.reject(session, 'A', {'action_id': 'choose', 'targets': ['A', 'B']})
        result = session.act('pilot:A', {'action_id': 'choose', 'targets': [target] if target else []})
        self.assertTrue(result.ok, result.summary)

    def check_bottom(self, session, target, original_library, expected):
        library = session.state.players[target].zones['library']
        count = len(expected)
        self.assertEqual(list(original_library), library[count:])
        self.assertCountEqual(expected, library[:count])
        for object_id in expected:
            card = session.state.cards[object_id]
            self.assertEqual('library', card.zone)
            if count > 1:
                self.assertEqual([], card.known_to)
                self.assertEqual([], card.revealed_to)

    def test_endurance_normal_flash_targets_or_omits_one_player_and_replays_private_bottom(self):
        for offset, target in enumerate(('B', None)):
            with self.subTest(target=target):
                session, source, cards, _, _, private = self.scene(629300 + offset)
                self.prepare(session, active='C', mana={'C': 1, 'G': 2})
                self.checkpoint(session)
                original = tuple(session.state.players['B'].zones['library'])
                shuffle_before = session.state.players['B'].stats.get('shuffle_count', 0)
                self.cast(session, 'A', source)
                self.assertEqual('C', session.state.active_player)
                self.assertEqual('semantic.target', self.resolve(session).kind)
                session = self.replay(session, load=True)
                self.choose_target(session, target)
                self.assertIsNone(self.resolve(session))
                expected = tuple(card.object_id for card in cards) if target else ()
                self.check_bottom(session, 'B', original, expected)
                self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
                self.assertEqual(shuffle_before, session.state.players['B'].stats.get('shuffle_count', 0))
                events = [event for event in session.state.events if event.code == 'graveyard.bottom']
                self.assertEqual(1 if target else 0, len(events))
                if target:
                    self.assertEqual(2, events[0].details['count'])
                    for card in cards:
                        self.assertNotIn(card.object_id, str(events[0].details))
                else:
                    self.assertEqual([card.object_id for card in cards], session.state.players['B'].zones['graveyard'])
                for viewer in 'ABC':
                    self.assertNotIn(private.ref, str(session.packet('pilot:' + viewer, full=True)))
                restored = self.replay(session, load=True)
                self.assertEqual(session.state.players['B'].zones['library'], restored.state.players['B'].zones['library'])

    def test_endurance_evoke_orders_self_target_before_or_after_sacrifice(self):
        for body_first in (False, True):
            with self.subTest(body_first=body_first):
                session, source, cards, pitch, wrong, _ = self.scene(629310 + body_first, target='A')
                self.prepare(session, active='C', mana={})
                self.checkpoint(session)
                original = tuple(session.state.players['A'].zones['library'])
                self.priority(session, 'A')
                options = self.action(session, 'A', source)['cost_options']
                evoke = next(option for option in options if option['id'] == 'evoke')
                self.assertEqual([pitch.ref], evoke['choice_schema']['exile_cards']['legal_refs'])
                command = {'action_id': 'cast:' + source.ref, 'cost_option': 'evoke', 'pay': 'auto'}
                self.reject(session, 'A', {**command, 'exile_cards': [wrong.ref]})
                result = session.act('pilot:A', {**command, 'exile_cards': [pitch.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual('exile', session.state.cards[pitch.object_id].zone)
                self.assertEqual('trigger.order', self.resolve(session).kind)
                rows = session.packet('pilot:A', full=True)['decision']['ctx']['triggers']
                self.assertEqual(2, len(rows))
                order = sorted(rows, key=lambda row: ('evoke' not in row['label'].lower()) == body_first)
                result = session.act('pilot:A', {'action_id': 'order', 'triggers': [row['id'] for row in order]})
                self.assertTrue(result.ok, result.summary)
                self.choose_target(session, 'A')
                self.resolve_top(session)
                if body_first:
                    self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
                    self.assertTrue(session.state.stack)
                    self.assertEqual('priority', session.state.pending_decision.kind)
                else:
                    self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                self.assertIsNone(self.resolve(session))
                expected = tuple(card.object_id for card in cards)
                if not body_first:
                    expected = (*expected, source.object_id)
                self.check_bottom(session, 'A', original, expected)
                self.assertEqual('graveyard' if body_first else 'library', session.state.cards[source.object_id].zone)
                self.replay(session)

    def test_endurance_replacement_resumes_once_with_actual_arrivals_and_retry(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        from quorune.zone_transitions import ZoneTransitionOwner

        for decline in (False, True):
            with self.subTest(decline=decline):
                session, source, cards, _, _, _ = self.scene(629320 + decline)
                self.prepare(session, mana={'C': 1, 'G': 2})
                commander_id = session.state.players['B'].zones['command'][0]
                session.engine.move_card(commander_id, 'graveyard', reason='Fixture commander graveyard boundary')
                session.engine.permissions.invalidate_current()
                session.state.pending_decision = None
                self.assertTrue(session.engine._stabilize())
                result = session.act('pilot:B', {'a': 'choose', 'choice': 'remain'})
                self.assertTrue(result.ok, result.summary)
                self.prepare(session, mana={'C': 1, 'G': 2})
                self.checkpoint(session)
                original = tuple(session.state.players['B'].zones['library'])
                original_graveyard = tuple(session.state.players['B'].zones['graveyard'])
                self.cast(session, 'A', source)
                self.choose_target(session, 'B')
                self.assertEqual('replacement.order', self.resolve(session).kind)
                self.assertEqual(original, tuple(session.state.players['B'].zones['library']))
                self.assertEqual(original_graveyard, tuple(session.state.players['B'].zones['graveyard']))
                session = self.replay(session, load=True)
                options = session.packet('pilot:B', full=True)['decision']['ctx']['options']
                chosen = next(option for option in options if bool(option.get('decline')) == decline)
                command = {'action_id': 'choose', 'replacement': chosen['id']}
                self.reject(session, 'A', command)
                if not decline:
                    original_commit = ZoneTransitionOwner.move_cards_simultaneously

                    def fail_after_commit(owner, changes, **kwargs):
                        result = original_commit(owner, changes, **kwargs)
                        if kwargs.get('library_order') is not None:
                            raise GameRuleError('injected random-bottom commit fault')
                        return result

                    before = authoritative_state_hash(session.state)
                    with patch.object(ZoneTransitionOwner, 'move_cards_simultaneously', new=fail_after_commit):
                        failed = session.act('pilot:B', command)
                    self.assertFalse(failed.ok)
                    self.assertIn('injected random-bottom commit fault', failed.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    session = self.replay(session, load=True)
                result = session.act('pilot:B', command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('library' if decline else 'command', session.state.cards[commander_id].zone)
                expected = [card.object_id for card in cards] + ([commander_id] if decline else [])
                self.check_bottom(session, 'B', original, expected)
                event, = [event for event in session.state.events if event.code == 'graveyard.bottom']
                self.assertEqual(len(expected), event.details['count'])
                self.replay(session)

    def test_random_bottom_positional_knowledge_mutation_is_killed_by_native_endurance(self):
        from quorune.library_order_model import LibraryArrivalOrder
        from quorune.zone_transitions import ZoneTransitionOwner

        actual = ZoneTransitionOwner.move_cards_simultaneously

        def grants_knowledge(owner, changes, **kwargs):
            order = kwargs.get('library_order')
            if order is not None:
                kwargs['library_order'] = LibraryArrivalOrder(order.chooser, order.position, order.members)
            return actual(owner, changes, **kwargs)

        session, source, cards, _, _, _ = self.scene(629330)
        self.prepare(session, mana={'C': 1, 'G': 2})
        original = tuple(session.state.players['B'].zones['library'])
        self.cast(session, 'A', source)
        self.choose_target(session, 'B')
        with patch.object(ZoneTransitionOwner, 'move_cards_simultaneously', new=grants_knowledge):
            self.assertIsNone(self.resolve(session))
        self.assertEqual([['B'], ['B']], [session.state.cards[card.object_id].known_to for card in cards])
        with self.assertRaises(AssertionError):
            self.check_bottom(session, 'B', original, [card.object_id for card in cards])


if __name__=='__main__':unittest.main(verbosity=2)
