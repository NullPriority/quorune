"""Pinned qualified death-cast permissions use departure facts, never current zones."""
from dataclasses import FrozenInstanceError,replace
import unittest
from quorune.death_cast_permission_model import DeathCastPermissionSpec


class DeathCastPermissionModelTests(unittest.TestCase):
    def test_name_exclusion_uses_any_last_battlefield_name_and_preserves_nameless_objects(self):
        spec=DeathCastPermissionSpec(excluded_name='Generic Dragon')
        self.assertTrue(spec.matches_departure(was_creature=True,rules_names=('Different Name',)))
        self.assertFalse(spec.matches_departure(was_creature=True,rules_names=('Generic Dragon',)))
        self.assertFalse(spec.matches_departure(was_creature=True,rules_names=('Another Name','Generic Dragon')))
        self.assertTrue(spec.matches_departure(was_creature=True,rules_names=()))
        self.assertFalse(spec.matches_departure(was_creature=False,rules_names=('Different Name',)))

    def test_subtype_exclusion_uses_lki_and_missing_facts_never_become_a_permission(self):
        spec=DeathCastPermissionSpec(excluded_subtype='zombie',entry_counters=1)
        self.assertTrue(spec.matches_departure(was_creature=True,creature_subtypes=('elf',)))
        self.assertFalse(spec.matches_departure(was_creature=True,creature_subtypes=('elf','zombie')))
        self.assertTrue(spec.matches_departure(was_creature=True,creature_subtypes=()))
        self.assertIsNone(spec.matches_departure(was_creature=True))
        self.assertIsNone(DeathCastPermissionSpec(excluded_name='Generic Dragon').matches_departure(was_creature=True))
        self.assertFalse(spec.matches_departure(was_creature=False))

    def test_closed_codec_versions_exclusions_and_entry_rider_are_immutable(self):
        value=DeathCastPermissionSpec(excluded_subtype='zombie',entry_counters=1)
        self.assertEqual(value,DeathCastPermissionSpec.from_dict(value.to_dict()))
        for changes in ({'schema_version':True},{'schema_version':2},{'excluded_name':'Name'},
                {'excluded_subtype':None},{'excluded_subtype':'unrepresented-type'},
                {'entry_counters':True},{'entry_counters':2}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(value,**changes)
        with self.assertRaises(ValueError):DeathCastPermissionSpec.from_dict({**value.to_dict(),'source_zone':'hand'})
        with self.assertRaises(FrozenInstanceError):value.entry_counters=0

    def test_departure_facts_reject_mutable_duplicate_and_reordered_values(self):
        spec=DeathCastPermissionSpec(excluded_name='Name')
        for names in (['Name'],('Name','Name'),('Z','A'),('',),(True,)):
            with self.subTest(names=names),self.assertRaises(ValueError):spec.matches_departure(was_creature=True,rules_names=names)
        with self.assertRaises(ValueError):spec.matches_departure(was_creature=1,rules_names=())


    def test_fresh_inverted_exclusion_mutant_is_killed_by_positive_and_negative_subtype_facts(self):
        from unittest.mock import patch
        original=DeathCastPermissionSpec.matches_departure
        def inverted(spec,**kwargs):
            result=original(spec,**kwargs)
            return None if result is None else not result
        result=unittest.TestResult()
        witness=type(self)('test_subtype_exclusion_uses_lki_and_missing_facts_never_become_a_permission')
        with patch.object(DeathCastPermissionSpec,'matches_departure',inverted):witness.run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))



class DeathNameHistoryCodecTests(unittest.TestCase):
    def event(self, names=None):
        from quorune.model import TurnHistoryEvent
        return TurnHistoryEvent('permanent_died',actor='B',object_incarnation='creature@2',
            types=('creature',),creature_subtypes=('zombie',),token=True,rules_names=names)

    def test_legacy_facts_and_absent_marker_roundtrip_without_asserting_known_names(self):
        from quorune.model import TurnHistory,TurnHistoryEvent
        old={'kind':'permanent_died','actor':'B','object_incarnation':'creature@2','target':None,
            'target_kind':None,'target_object_incarnation':None,'types':['creature'],'amount':0,
            'creature_subtypes':['zombie'],'token':True}
        event=TurnHistoryEvent.from_dict(old);self.assertIsNone(event.rules_names);self.assertEqual(old,event.to_dict())
        history=TurnHistory(turn_sequence=3,permanent_death_history_version=1,events=[event])
        self.assertNotIn('death_name_history_version',history.to_dict())
        self.assertEqual(history.to_dict(),TurnHistory.from_dict(history.to_dict()).to_dict())

    def test_explicit_known_names_and_unnamed_facts_require_journal_marker_and_survive_turn_reset(self):
        from quorune.model import TurnHistory
        from quorune.turn_history import reset_turn_history
        events=[self.event(('Generic Dragon',)),self.event(())]
        history=TurnHistory(turn_sequence=3,permanent_death_history_version=1,death_name_history_version=1,events=events)
        restored=TurnHistory.from_dict(history.to_dict())
        self.assertEqual(('Generic Dragon',),restored.events[0].rules_names);self.assertEqual((),restored.events[1].rules_names)
        reset=reset_turn_history(restored,4)
        self.assertEqual(1,reset.death_name_history_version);self.assertEqual([],reset.events)
        with self.assertRaises(ValueError):TurnHistory(permanent_death_history_version=1,events=events)
        with self.assertRaises(ValueError):TurnHistory(permanent_death_history_version=1,death_name_history_version=1,events=[self.event()])

    def test_new_name_facts_reject_null_mutable_or_noncanonical_names_and_missing_death_authority(self):
        from quorune.model import TurnHistory,TurnHistoryEvent
        event=self.event(('Generic Dragon',))
        for names in (None,True,'Generic Dragon'):
            with self.subTest(names=names),self.assertRaises(ValueError):
                TurnHistoryEvent.from_dict({**event.to_dict(),'rules_names':names})
        for names in (['Name'],('Name','Name'),('Z','A'),('',)):
            with self.subTest(names=names),self.assertRaises(ValueError):self.event(names)
        for version in (True,0,2):
            with self.subTest(version=version),self.assertRaises(ValueError):TurnHistory(permanent_death_history_version=1,death_name_history_version=version)
        with self.assertRaises(ValueError):TurnHistory(death_name_history_version=1)
        with self.assertRaises(ValueError):TurnHistory.from_dict({'death_name_history_version':None})

    def test_record_provenance_distinguishes_legacy_absence_from_new_complete_name_history(self):
        from test_spell_resolution_names import SpellResolutionNameStateCodecTests
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        state=SpellResolutionNameStateCodecTests.state()
        legacy={'format':format_state_versions(state)};validate_state_versions(legacy,state)
        self.assertNotIn('death_name_history_version',legacy['format'])
        state.turn_history.permanent_death_history_version=1;state.turn_history.death_name_history_version=1
        current={'format':format_state_versions(state)};self.assertEqual(1,current['format']['death_name_history_version'])
        validate_state_versions(current,state)
        with self.assertRaises(ValueError):validate_state_versions(legacy,state)
        state.turn_history.death_name_history_version=None
        with self.assertRaises(ValueError):validate_state_versions(current,state)



class QualifiedDeathHistoryQueryTests(unittest.TestCase):
    def state(self, events, *, names=True):
        from types import SimpleNamespace
        from quorune.model import TurnHistory
        return SimpleNamespace(turn_sequence=2,players={'A':SimpleNamespace(in_game=True),'B':SimpleNamespace(in_game=True)},
            turn_history=TurnHistory(turn_sequence=2,permanent_death_history_version=1,
                death_name_history_version=1 if names else None,events=events))

    def event(self, *, names=('Different Creature',), subtypes=('elf',), token=False, actor='B'):
        from quorune.model import TurnHistoryEvent
        return TurnHistoryEvent('permanent_died',actor=actor,object_incarnation='dead@1',types=('creature',),
            creature_subtypes=subtypes,token=token,rules_names=names)

    def query(self, **kwargs):
        from quorune.public_history_query_model import PublicHistoryQuerySpec
        return PublicHistoryQuerySpec('permanent_died',relation='any_player',types_all=('creature',),schema_version=5,**kwargs)

    def test_name_exclusion_uses_departure_names_across_players_tokens_and_current_zones(self):
        from quorune.public_history_query_model import public_history_query_value
        query=self.query(excluded_name='Named Dragon')
        events=[self.event(names=('Named Dragon',)),self.event(names=('Alias','Named Dragon')),
            self.event(names=(),token=True),self.event(names=('Different Creature',),actor='A')]
        state=self.state(events)
        self.assertEqual(2,public_history_query_value(state,'A',query))
        state.turn_sequence=3;self.assertIsNone(public_history_query_value(state,'A',query))

    def test_non_subtype_predicate_reads_old_sealed_subtypes_without_inventing_names(self):
        from quorune.public_history_query_model import PublicHistoryQuerySpec,public_history_query_value
        events=[self.event(names=None,subtypes=('zombie',)),self.event(names=None,subtypes=('elf','zombie')),
            self.event(names=None,subtypes=('elf',),token=True)]
        state=self.state(events,names=False)
        self.assertEqual(1,public_history_query_value(state,'A',self.query(excluded_subtype='zombie')))
        self.assertIsNone(public_history_query_value(state,'A',self.query(excluded_name='Named Dragon')))
        old=PublicHistoryQuerySpec('permanent_died',relation='any_player',schema_version=3,creature_subtype='zombie')
        self.assertEqual(2,public_history_query_value(state,'A',old))

    def test_v5_wire_rejects_old_version_reinterpretation_and_conflicting_predicates(self):
        from quorune.public_history_query_model import PublicHistoryQuerySpec
        query=self.query(excluded_name='Named Dragon');self.assertEqual(query,PublicHistoryQuerySpec.from_dict(query.to_dict()))
        for changes in ({'schema_version':1},{'schema_version':3},{'schema_version':4},{'schema_version':True},
                {'excluded_subtype':'zombie'},{'exclude_source':True},{'event':'spell_cast'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):PublicHistoryQuerySpec.from_dict({**query.to_dict(),**changes})
        with self.assertRaises(ValueError):self.query(excluded_subtype='unrepresented-type')
        with self.assertRaises(ValueError):PublicHistoryQuerySpec('permanent_died',schema_version=5,excluded_name='Name')



class DeathNameCaptureTests(unittest.TestCase):
    def test_capture_uses_current_public_copy_name_and_legacy_mode_adds_nothing(self):
        from types import SimpleNamespace
        from quorune.death_history_names import capture_departure_rules_names,occurrence_death_name_fields,DEATH_RULES_NAMES_FIELD
        history=SimpleNamespace(death_name_history_version=1)
        host=SimpleNamespace(state=SimpleNamespace(turn_history=history),card_record=lambda c:None)
        card=SimpleNamespace(zone='battlefield',face_down=False,phased_out=False,annotations={},printed_name='Wrong Printed Name')
        values={'name':'Actual Copied Name','type_line':'Creature - Dragon'}
        captured=capture_departure_rules_names(host,card,values)
        self.assertEqual(('Actual Copied Name',),captured[DEATH_RULES_NAMES_FIELD])
        self.assertNotIn(DEATH_RULES_NAMES_FIELD,values)
        self.assertEqual({'rules_names':('Actual Copied Name',)},occurrence_death_name_fields(history,captured))
        card.face_down=True
        self.assertEqual((),capture_departure_rules_names(host,card,values)[DEATH_RULES_NAMES_FIELD])
        history.death_name_history_version=None
        self.assertIs(values,capture_departure_rules_names(host,card,values))
        self.assertEqual({},occurrence_death_name_fields(history,values))

    def test_enabled_occurrence_cannot_substitute_missing_names_with_an_empty_tuple(self):
        from types import SimpleNamespace
        from quorune.death_history_names import occurrence_death_name_fields
        with self.assertRaises(ValueError):occurrence_death_name_fields(SimpleNamespace(death_name_history_version=1),{})



from inherited_composition_fixture import InheritedNativeFixture


class DeathCastPermissionFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        import json,tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();root=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=root/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/death-cast-permission-cards.json'],root/'death-cast.sqlite3')
        cls.db=CardDatabase(root/'death-cast.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified death casting',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])


class DeathNameHistoryNativeTests(DeathCastPermissionFixture,unittest.TestCase):
    def test_actual_named_creature_death_records_departure_names_and_replays_exactly(self):
        from quorune.public_history_query_model import PublicHistoryQuerySpec,public_history_query_value
        s=self.session(400721001);creature=self.add(s.engine,'Grizzly Bears',seat='B')
        removal=self.add(s.engine,'Lightning Bolt',zone='hand')
        self.prepare(s,mana={'R':1});self.checkpoint(s);self.cast_for(s,'A',removal,[creature.ref])
        self.assertIsNone(self.resolve(s))
        deaths=[e for e in s.state.turn_history.events if e.kind=='permanent_died']
        self.assertEqual(1,len(deaths));self.assertEqual(('Grizzly Bears',),deaths[0].rules_names)
        query=PublicHistoryQuerySpec('permanent_died','any_player',types_all=('creature',),schema_version=5,excluded_name='Different Name')
        self.assertEqual(1,public_history_query_value(s.state,'A',query));self.replay(s,load=True)

    def test_actual_legacy_mode_does_not_add_names_or_a_new_hashed_marker(self):
        s=self.session(400721002);s.state.turn_history.death_name_history_version=None
        creature=self.add(s.engine,'Grizzly Bears',seat='B');removal=self.add(s.engine,'Lightning Bolt',zone='hand')
        self.prepare(s,mana={'R':1});self.checkpoint(s);self.cast_for(s,'A',removal,[creature.ref]);self.assertIsNone(self.resolve(s))
        death=next(e for e in s.state.turn_history.events if e.kind=='permanent_died')
        self.assertIsNone(death.rules_names);self.assertNotIn('rules_names',death.to_dict())
        self.assertNotIn('death_name_history_version',s.state.turn_history.to_dict());self.replay(s,load=True)

    def test_actual_sacrificed_cast_cost_seals_names_before_its_deferred_event(self):
        s=self.session(400721003);creature=self.add(s.engine,'Grizzly Bears');spell=self.add(s.engine,'Village Rites',zone='hand')
        self.assert_closed(s,spell);self.prepare(s,mana={'B':1});self.checkpoint(s)
        self.priority(s,'A');action=self.action(s,'A',spell)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto','sacrifice_cards':[creature.ref]})
        self.assertTrue(result.ok,result.summary);self.assertEqual('stack',s.state.cards[spell.object_id].zone)
        death=next(e for e in s.state.turn_history.events if e.kind=='permanent_died')
        self.assertEqual(('Grizzly Bears',),death.rules_names)
        self.assertIsNone(self.resolve(s));self.replay(s,load=True)



class QualifiedDeathPermissionCompilerTests(unittest.TestCase):
    def test_existing_public_state_permission_carries_literal_english_name_and_negative_subtype(self):
        from quorune.compiler.casting_activation_metadata_templates import static_self_zone_cast_permission_handler
        from quorune.semantic_runtime.casting_activation_metadata import PublicStateSelfZoneCastPermissionHandler
        for clause,name,subtype in (('a creature not named Ebondeath, Dracolich died this turn','Ebondeath, Dracolich',None),
                ('a non-Zombie creature died this turn',None,'zombie')):
            with self.subTest(clause=clause):
                compiled=static_self_zone_cast_permission_handler('You may cast this card from your graveyard if '+clause+'.',source_name='Generic Source')
                self.assertIsNotNone(compiled)
                permission=PublicStateSelfZoneCastPermissionHandler().validate(compiled[1]);query=permission.source_condition.history_query
                self.assertEqual(5,query.schema_version);self.assertEqual(name,query.excluded_name);self.assertEqual(subtype,query.excluded_subtype)
        self.assertIsNone(static_self_zone_cast_permission_handler('You may cast this card from your graveyard if an unrepresented event occurred.',source_name='Generic Source'))
        from quorune.compiler.public_history_conditions import qualified_creature_death_exclusion
        self.assertIsNone(qualified_creature_death_exclusion('a creature not named Dragon died this turn or a Zombie died this turn'))

    def test_fresh_current_name_instead_of_sealed_name_mutant_is_killed_by_independent_history(self):
        from unittest.mock import patch
        import quorune.death_cast_permission_model as owner
        original=owner.DeathCastPermissionSpec.matches_departure
        def wrong(spec,**kwargs):
            if spec.excluded_name is not None:kwargs['rules_names']=('Current Wrong Name',)
            return original(spec,**kwargs)
        result=unittest.TestResult()
        witness=QualifiedDeathHistoryQueryTests('test_name_exclusion_uses_departure_names_across_players_tokens_and_current_zones')
        with patch.object(owner.DeathCastPermissionSpec,'matches_departure',wrong):witness.run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))



class QualifiedGraveyardCastNativeTests(DeathCastPermissionFixture,unittest.TestCase):
    def test_actual_ebondeath_casts_from_graveyard_after_other_creature_death_with_flash_and_tapped_entry(self):
        from quorune.record import authoritative_state_hash
        s=self.session(400721100);source=self.add(s.engine,'Ebondeath, Dracolich',zone='graveyard')
        body=self.add(s.engine,'Grizzly Bears',seat='B');removal=self.add(s.engine,'Lightning Bolt',zone='hand')
        self.assert_closed(s,source)
        self.prepare(s,actor='A',active='B',mana={'R':1,'C':2,'B':2});self.checkpoint(s)
        def offered():
            self.priority(s,'A')
            return [r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r.get('card')==source.ref]
        self.assertEqual([],offered())
        self.cast_for(s,'A',removal,[body.ref],reserve={'C':2,'B':2});self.assertIsNone(self.resolve(s))
        rows=offered();self.assertEqual(1,len(rows));command={'action_id':rows[0]['id'],'pay':'auto'}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('stack',s.state.cards[source.object_id].zone);self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id];self.assertEqual('battlefield',current.zone);self.assertTrue(current.tapped)
        self.assertIn('flying',s.engine._combat_keywords(current));self.replay(s,load=True)



class QualifiedDeathChosenPermissionTests(unittest.TestCase):
    def test_choice_binds_the_actual_intrinsic_permission_and_keeps_entry_rider_distinct(self):
        from quorune.death_cast_permission_model import death_cast_choice,validate_death_cast_choice
        rider=DeathCastPermissionSpec(excluded_subtype='zombie',entry_counters=1)
        ordinary=DeathCastPermissionSpec(excluded_name='Generic Dragon')
        self.assertEqual(rider,validate_death_cast_choice(death_cast_choice(rider)))
        self.assertEqual(ordinary,validate_death_cast_choice(death_cast_choice(ordinary)))
        self.assertNotEqual(death_cast_choice(rider),death_cast_choice(ordinary))
        self.assertNotIn('cast_origin',death_cast_choice(rider))

    def test_other_graveyard_permission_or_malformed_choice_cannot_acquire_the_rider(self):
        from quorune.death_cast_permission_model import death_cast_choice,validate_death_cast_choice
        choice=death_cast_choice(DeathCastPermissionSpec(excluded_subtype='zombie',entry_counters=1))
        for changes in ({'schema_version':True},{'schema_version':2},{'source_zone':'hand'}, {'source_zone':'exile'}, {'permission':{}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate_death_cast_choice({**choice,**changes})
        with self.assertRaises(ValueError):validate_death_cast_choice({'cast_origin':'graveyard'})
        with self.assertRaises(ValueError):validate_death_cast_choice({**choice,'entry_counters':1})



class QualifiedDeathRiderOwnerTests(unittest.TestCase):
    def spec(self):return DeathCastPermissionSpec(excluded_subtype='zombie',entry_counters=1)

    def test_complete_two_sentence_compiler_descriptor_is_closed_and_requires_whole_card(self):
        from quorune.compiler.death_cast_permissions import qualified_death_cast_template
        from quorune.semantic_runtime.death_cast_permissions import QualifiedDeathCastPermissionHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        text='You may cast this card from your graveyard if a non-Zombie creature died this turn. If you do, this creature enters with a +1/+1 counter on it.'
        template=qualified_death_cast_template(text);self.assertIsNotNone(template)
        descriptor=template[1];self.assertEqual(self.spec(),QualifiedDeathCastPermissionHandler().validate(descriptor))
        for changes in ({'requires_complete_card_program':False},{'schema_version':True},{'source_zone':'hand'}):
            with self.subTest(changes=changes),self.assertRaises(SemanticNodeError):QualifiedDeathCastPermissionHandler().validate({**descriptor,**changes})
        for bad in (text+' Draw a card.',text.replace('+1/+1','+2/+2'),text.replace('graveyard','exile')):
            self.assertIsNone(qualified_death_cast_template(bad))

    def test_selected_authority_decorates_price_and_preserves_an_independent_permission_choice(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.rules.death_cast_permissions import with_qualified_death_choices,underlying_qualified_death_option,qualified_death_stack_context
        from quorune.death_cast_permission_model import DEATH_CAST_CHOICE_FIELD
        card=SimpleNamespace(zone='graveyard');base={'id':'normal','kind':'mana','requirements':{'B':1,'R':1}}
        for independent in (False,True):
            with patch('quorune.rules.death_cast_permissions.compiled_qualified_death_specs',return_value=(self.spec(),)), \
                    patch('quorune.compiled_flashback.compiled_ordinary_zone_cast_permission',return_value=independent):
                options=with_qualified_death_choices(object(),'A',card,[base])
            self.assertEqual(2 if independent else 1,len(options));marked=next(o for o in options if DEATH_CAST_CHOICE_FIELD in o)
            self.assertEqual('normal',underlying_qualified_death_option(marked,marked['id']))
            self.assertEqual(base['requirements'],marked['requirements'])
            self.assertIn(DEATH_CAST_CHOICE_FIELD,qualified_death_stack_context(marked))
            self.assertEqual({},qualified_death_stack_context(base))
            with self.assertRaises(ValueError):underlying_qualified_death_option(marked,'normal')
        self.assertNotIn(DEATH_CAST_CHOICE_FIELD,base)

    def test_entry_effect_requires_actual_chosen_permission_and_is_not_copied_with_uncast_spell(self):
        from types import SimpleNamespace
        from quorune.rules.death_cast_permissions import qualified_death_entry_counters
        from quorune.death_cast_permission_model import death_cast_choice,DEATH_CAST_CHOICE_FIELD
        card=SimpleNamespace(zone='stack',ref='O1');host=SimpleNamespace(state=SimpleNamespace(cards={'object':card}))
        item=SimpleNamespace(kind='spell',controller='B',card_object_id='object',context={})
        self.assertEqual((),qualified_death_entry_counters(host,item))
        item.context={'cast_origin':'graveyard',DEATH_CAST_CHOICE_FIELD:death_cast_choice(self.spec())}
        counters=qualified_death_entry_counters(host,item)
        self.assertEqual(1,len(counters));self.assertEqual(1,counters[0].amount)
        self.assertEqual('B',counters[0].placing_player);self.assertEqual('+1/+1',counters[0].counter_name)
        item.kind='spell_copy';self.assertEqual((),qualified_death_entry_counters(host,item))
        item.kind='spell';item.context['cast_origin']='hand'
        with self.assertRaises(ValueError):qualified_death_entry_counters(host,item)

    def test_fresh_origin_only_rider_mutant_is_killed_by_the_unmarked_permission_contrast(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import quorune.rules.death_cast_permissions as owner
        result=unittest.TestResult();witness=type(self)('test_entry_effect_requires_actual_chosen_permission_and_is_not_copied_with_uncast_spell')
        with patch.object(owner,'qualified_death_entry_counters',return_value=(SimpleNamespace(amount=1),)):witness.run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


class QualifiedDeathRiderNativeTests(DeathCastPermissionFixture,unittest.TestCase):
    def test_both_complete_originals_bind_normally_and_reject_undeclared_runtime_owners(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantics import SemanticRegistry
        for name in ('Ebondeath, Dracolich','Undead Sprinter'):
            card=self.db.lookup(name)
            ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
                capability_registry=self.registry,capability_profile='commander_review')
            binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
            cap='casting.permission.self_graveyard_qualified_death' if name=='Undead Sprinter' else 'condition.history.qualified_death_exclusion'
            ability=next(a for a in program.abilities if cap in a.capability_dependencies)
            for dep in ability.capability_dependencies:
                deps=tuple(c for c in ability.capability_dependencies if c!=dep)
                changed=replace(ability,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'],(name,dep))
                self.assertIn('capability:undeclared_runtime_dependency:'+dep,bound['blockers'])

    def test_actual_sprinter_requires_non_zombie_death_pays_printed_cost_and_enters_with_one_counter(self):
        from quorune.record import authoritative_state_hash
        from quorune.death_cast_permission_model import DEATH_CAST_CHOICE_FIELD
        s=self.session(400721200);source=self.add(s.engine,'Undead Sprinter',zone='graveyard')
        body=self.add(s.engine,'Grizzly Bears',seat='B');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-death-private-c')
        self.assert_closed(s,source)
        self.prepare(s,mana={'B':1,'R':2});self.checkpoint(s)
        def offered():
            self.priority(s,'A')
            return next((a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref),None)
        self.assertIsNone(offered())
        self.cast_for(s,'A',bolt,[body.ref],reserve={'B':1,'R':1});self.assertIsNone(self.resolve(s))
        action=offered();self.assertIsNotNone(action)
        self.assertEqual(1,len(action['cost_options']));option=action['cost_options'][0]
        self.assertTrue(option['id'].startswith('qualified-death:'))
        command={'action_id':action['id'],'cost_option':option['id'],'pay':'auto'}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertFalse(s.act('pilot:A',{**command,'cost_option':'normal'}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIn(DEATH_CAST_CHOICE_FIELD,s.state.stack[-1].context)
        self.assertEqual(0,s.state.players['A'].mana_pool.get('B',0))
        self.assertEqual(0,s.state.players['A'].mana_pool.get('R',0))
        self.assertIsNone(self.resolve(s));current=s.state.cards[source.object_id]
        self.assertEqual('battlefield',current.zone);self.assertEqual(1,current.counters.get('+1/+1',0))
        self.assertTrue({'trample','haste'}<=set(s.engine._combat_keywords(current)))
        import json
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

class QualifiedDeathRiderInteractionTests(DeathCastPermissionFixture,unittest.TestCase):
    def offered(self,s,source):
        self.priority(s,'A')
        return next((a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref),None)

    def kill_bear(self,s,*,mana=None):
        body=self.add(s.engine,'Grizzly Bears',seat='B');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        self.prepare(s,mana={'B':1,'R':2} if mana is None else mana);self.checkpoint(s)
        self.cast_for(s,'A',bolt,[body.ref],reserve={'B':1,'R':1});self.assertIsNone(self.resolve(s))

    def use(self,s,action,option):
        result=s.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_actual_intrinsic_and_gisa_permissions_choose_different_entry_results_and_quota_use(self):
        from quorune.death_cast_permission_model import DEATH_CAST_CHOICE_FIELD
        from quorune.rules.turn_cast_grant_model import TURN_CAST_GRANT_FIELD
        for intrinsic in (True,False):
            with self.subTest(intrinsic=intrinsic):
                s=self.session(400721210+intrinsic);source=self.add(s.engine,'Undead Sprinter',zone='graveyard')
                giver=self.add(s.engine,'Gisa and Geralf');self.assert_closed(s,giver)
                remaining=self.add(s.engine,'Undead Sprinter',zone='graveyard',ref='remaining-sprinter')
                self.kill_bear(s,mana={'B':2,'R':3})
                action=self.offered(s,source);self.assertIsNotNone(action)
                options=action['cost_options'];self.assertEqual(2,len(options),options)
                option=next(o for o in options if o['id'].startswith('qualified-death:')==intrinsic)
                self.assertEqual(not intrinsic,option['id'].startswith('turn-cast-grant:'))
                self.use(s,action,option)
                self.assertEqual(intrinsic,DEATH_CAST_CHOICE_FIELD in s.state.stack[-1].context)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                current=s.state.cards[source.object_id]
                self.assertEqual('battlefield',current.zone);self.assertEqual(int(intrinsic),current.counters.get('+1/+1',0))
                further=self.offered(s,s.state.cards[remaining.object_id]);self.assertIsNotNone(further)
                self.assertEqual(intrinsic,any(o['id'].startswith('turn-cast-grant:') for o in further['cost_options']))
                self.replay(s)

    def test_actual_hand_cast_has_no_rider_and_zombie_or_changeling_deaths_do_not_enable_graveyard_cast(self):
        from quorune.death_cast_permission_model import DEATH_CAST_CHOICE_FIELD
        s=self.session(400721220);source=self.add(s.engine,'Undead Sprinter',zone='hand')
        self.prepare(s,mana={'B':1,'R':1});self.checkpoint(s)
        action=self.offered(s,source);self.assertIsNotNone(action);self.use(s,action,action['cost_options'][0])
        self.assertNotIn(DEATH_CAST_CHOICE_FIELD,s.state.stack[-1].context);self.assertIsNone(self.resolve(s))
        self.assertEqual(0,s.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(s)
        for index,name in enumerate(('Undead Sprinter','Universal Automaton')):
            with self.subTest(dead=name):
                s=self.session(400721221+index);source=self.add(s.engine,'Undead Sprinter',zone='graveyard',ref='retained-sprinter')
                body=self.add(s.engine,name,seat='B',ref='excluded-dead-creature');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
                self.prepare(s,mana={'B':1,'R':2});self.checkpoint(s)
                self.cast_for(s,'A',bolt,[body.ref],reserve={'B':1,'R':1});self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[body.object_id].zone);self.assertIsNone(self.offered(s,source))
                self.replay(s)

    def test_actual_entry_quantity_replacement_and_prohibition_modify_the_selected_effect(self):
        for index,(name,expected) in enumerate((('Hardened Scales',2),('Solemnity',0))):
            with self.subTest(replacement=name):
                s=self.session(400721230+index);source=self.add(s.engine,'Undead Sprinter',zone='graveyard')
                replacement=self.add(s.engine,name);self.assert_closed(s,replacement);self.kill_bear(s)
                action=self.offered(s,source);self.assertIsNotNone(action);self.use(s,action,action['cost_options'][0])
                self.assertIsNone(self.resolve(s));current=s.state.cards[source.object_id]
                self.assertEqual('battlefield',current.zone);self.assertEqual(expected,current.counters.get('+1/+1',0))
                self.replay(s,load=True)


class QualifiedDeathPermissionWithdrawalTests(DeathCastPermissionFixture,unittest.TestCase):
    def test_genuinely_verified_history_and_entry_owners_block_only_their_complete_originals(self):
        import json
        from copy import deepcopy
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values());names={'Ebondeath, Dracolich','Undead Sprinter'}
        cases={'condition.history.qualified_death_exclusion':names,
            'zone.turn_history.permanent_death':names,
            'casting.permission.self_graveyard_qualified_death':{'Undead Sprinter'},
            'counter.producer.effect_entry':{'Undead Sprinter'}}
        for cap,affected in cases.items():
            value=deepcopy(raw);next(c for c in value['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn independent qualified death owner'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in sorted(names|{'Grizzly Bears'}):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                if name not in affected:
                    self.assertEqual('exact',ir.status,(cap,name,ir.material_residuals));continue
                self.assertNotEqual('exact',ir.status,(cap,name))
                blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)


class QualifiedDeathRiderLifecycleTests(DeathCastPermissionFixture,unittest.TestCase):
    offered=QualifiedDeathRiderInteractionTests.offered
    kill_bear=QualifiedDeathRiderInteractionTests.kill_bear
    use=QualifiedDeathRiderInteractionTests.use

    def test_actual_countered_permission_cast_keeps_price_and_recast_uses_new_incarnation(self):
        s=self.session(400721240);source=self.add(s.engine,'Undead Sprinter',zone='graveyard')
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand')
        s.state.players['B'].mana_pool.update({'U':2});self.kill_bear(s,mana={'B':2,'R':3})
        action=self.offered(s,source);self.use(s,action,action['cost_options'][0])
        first=s.state.cards[source.object_id].logical_object_id;stack_ref=s.state.stack[-1].ref
        self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        response=next(a for a in rows if a['id']=='cast:'+counter.ref)
        result=s.act('pilot:B',{'action_id':response['id'],'targets':[stack_ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id];self.assertEqual('graveyard',current.zone)
        self.assertNotEqual(first,current.logical_object_id);self.assertFalse(current.counters)
        action=self.offered(s,current);self.assertIsNotNone(action);self.use(s,action,action['cost_options'][0])
        self.assertIsNone(self.resolve(s));current=s.state.cards[source.object_id]
        self.assertEqual('battlefield',current.zone);self.assertEqual(1,current.counters.get('+1/+1',0))
        self.assertFalse(any(s.state.players['A'].mana_pool.values()));self.replay(s,load=True)

    def test_post_entry_fault_rolls_back_counter_zone_and_events_then_retries_same_command(self):
        from unittest.mock import patch
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.session(400721241);source=self.add(s.engine,'Undead Sprinter',zone='graveyard');self.kill_bear(s)
        action=self.offered(s,source);self.use(s,action,action['cost_options'][0])
        for _ in range(3):
            self.assertEqual('priority',s.state.pending_decision.kind)
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        principal=s.pending_principals()[0];command={'action_id':'pass'}
        before=authoritative_state_hash(s.state);observed=[];original=ZoneTransitionOwner.move_card
        def fault(owner,object_id,destination,*args,**kwargs):
            result=original(owner,object_id,destination,*args,**kwargs)
            if object_id==source.object_id and destination=='battlefield':
                observed.append((s.state.cards[source.object_id].zone,s.state.cards[source.object_id].counters.get('+1/+1',0)))
                raise GameRuleError('Injected qualified death post-entry fault')
            return result
        with patch.object(ZoneTransitionOwner,'move_card',fault):
            result=s.act(principal,command);self.assertFalse(result.ok,result.summary)
            self.assertIn('Injected qualified death post-entry fault',result.summary)
        self.assertEqual([('battlefield',1)],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual('stack',s.state.cards[source.object_id].zone);self.assertFalse(s.state.cards[source.object_id].counters)
        s=self.replay(s,load=True);result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(s)


class QualifiedDeathRiderCopyMutationTests(DeathCastPermissionFixture,unittest.TestCase):
    offered=QualifiedDeathRiderInteractionTests.offered
    kill_bear=QualifiedDeathRiderInteractionTests.kill_bear
    use=QualifiedDeathRiderInteractionTests.use

    def test_actual_uncast_creature_spell_copy_does_not_inherit_selected_graveyard_permission(self):
        s=self.session(400721250);source=self.add(s.engine,'Undead Sprinter',zone='graveyard')
        copy=self.add(s.engine,'Lithoform Engine');self.assert_closed(s,copy)
        self.kill_bear(s,mana={'B':1,'R':2,'C':4})
        action=self.offered(s,source);self.use(s,action,action['cost_options'][0])
        original=s.state.stack[-1].ref;self.priority(s,'A')
        import json
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        ability=next(a for a in actions if a['id'].startswith('activate:'+copy.ref+':') and original in json.dumps(a.get('target_schema') or {}))
        result=s.act('pilot:A',{'action_id':ability['id'],'targets':[original],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        bodies=[c for c in s.state.cards.values() if c.zone=='battlefield' and s.engine.display_name(c.object_id)=='Undead Sprinter']
        self.assertEqual(2,len(bodies));actual=s.state.cards[source.object_id]
        self.assertEqual(1,actual.counters.get('+1/+1',0))
        token=next(c for c in bodies if c.object_id!=source.object_id)
        self.assertEqual('token',token.object_kind);self.assertEqual(0,token.counters.get('+1/+1',0))
        self.replay(s,load=True)

    def test_fresh_native_entry_rider_omission_is_killed_after_positive_paid_cast_and_replay(self):
        from unittest.mock import patch
        import quorune.rules.death_cast_permissions as owner
        witness=lambda:QualifiedDeathRiderNativeTests.test_actual_sprinter_requires_non_zombie_death_pays_printed_cost_and_enters_with_one_counter(self)
        witness()
        class NativeWitness(unittest.TestCase):
            def runTest(child):witness()
        result=unittest.TestResult()
        with patch.object(owner,'qualified_death_entry_counters',return_value=()):NativeWitness().run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


class QualifiedDeathRiderPriceTimingTests(DeathCastPermissionFixture,unittest.TestCase):
    offered=QualifiedDeathRiderInteractionTests.offered
    kill_bear=QualifiedDeathRiderInteractionTests.kill_bear
    use=QualifiedDeathRiderInteractionTests.use

    def test_actual_selected_permission_keeps_global_alternative_price_timing_and_opponent_prohibition(self):
        s=self.session(400721260);source=self.add(s.engine,'Undead Sprinter',zone='graveyard')
        fist=self.add(s.engine,'Fist of Suns');self.assert_closed(s,fist)
        self.kill_bear(s,mana={'W':1,'U':1,'B':1,'R':2,'G':1})
        action=self.offered(s,source);self.assertIsNotNone(action)
        option=next(o for o in action['cost_options'] if o['requirements'].get('W')==1)
        self.assertTrue(option['id'].startswith('qualified-death:'));self.use(s,action,option)
        self.assertFalse(any(s.state.players['A'].mana_pool.values()));self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(s,load=True)
        for active,prohibited in (('B',False),('A',True)):
            with self.subTest(active=active,prohibited=prohibited):
                s=self.session(400721261+prohibited);source=self.add(s.engine,'Undead Sprinter',zone='graveyard')
                if prohibited:
                    restriction=self.add(s.engine,'Drannith Magistrate',seat='B');self.assert_closed(s,restriction)
                body=self.add(s.engine,'Grizzly Bears',seat='B');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
                self.prepare(s,active=active,mana={'B':1,'R':2});self.checkpoint(s)
                self.cast_for(s,'A',bolt,[body.ref],reserve={'B':1,'R':1});self.assertIsNone(self.resolve(s))
                self.assertIsNone(self.offered(s,source));self.replay(s)


class QualifiedDeathNameCopyNativeTests(DeathCastPermissionFixture,unittest.TestCase):
    offered=QualifiedDeathRiderInteractionTests.offered

    def test_actual_clone_departure_name_controls_permission_after_the_graveyard_name_changes(self):
        import test_fixed_copy_type_exceptions as copies
        for index,(name,allowed) in enumerate((('Ebondeath, Dracolich',False),('Grizzly Bears',True))):
            with self.subTest(copied=name):
                s=self.session(400721270+index);source=self.add(s.engine,'Ebondeath, Dracolich',zone='graveyard',ref='qualified-source')
                target=self.add(s.engine,name,seat='C',ref='copy-target')
                clone=self.add(s.engine,'Clone',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
                self.prepare(s,mana={'C':5,'U':1,'B':2,'R':1});self.checkpoint(s)
                self.cast_for(s,'A',clone,reserve={'C':2,'B':2,'R':1})
                s=copies.FixedCopyTypeExceptionRuntimeTests.choose_copy(self,s,target)
                current=s.state.cards[clone.object_id];self.assertEqual(name,s.engine.display_name(current.object_id))
                self.cast_for(s,'A',s.state.cards[bolt.object_id],[current.ref],reserve={'C':2,'B':2});self.assertIsNone(self.resolve(s))
                current=s.state.cards[clone.object_id];self.assertEqual('graveyard',current.zone)
                self.assertEqual('Clone',s.engine.display_name(current.object_id))
                deaths=[e for e in s.state.turn_history.events if e.kind=='permanent_died']
                self.assertEqual((name,),deaths[-1].rules_names)
                action=self.offered(s,s.state.cards[source.object_id]);self.assertEqual(allowed,action is not None)
                if allowed:
                    result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(s));current=s.state.cards[source.object_id]
                    self.assertEqual('battlefield',current.zone);self.assertTrue(current.tapped)
                self.replay(s,load=True)


class QualifiedDeathTemporalNativeTests(DeathCastPermissionFixture,unittest.TestCase):
    offered=QualifiedDeathRiderInteractionTests.offered

    def test_actual_token_death_survives_token_ceasing_and_late_source_arrival_but_not_the_next_turn(self):
        from quorune.public_history_query_model import PublicHistoryQuerySpec,public_history_query_value
        s=self.session(400721280);source=self.add(s.engine,'Ebondeath, Dracolich',ref='late-graveyard-source')
        other=self.add(s.engine,'Ebondeath, Dracolich',zone='graveyard',ref='next-turn-source')
        maker=self.add(s.engine,'Raise the Alarm',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand',ref='token-death-bolt')
        response=self.add(s.engine,'Lightning Bolt',seat='B',zone='hand',ref='late-source-bolt')
        for i,name in enumerate(('Swamp','Swamp','Wastes','Wastes')):self.add(s.engine,name,ref='expiry-land-'+str(i))
        s.state.players['B'].mana_pool.update({'R':1})
        self.prepare(s,mana={'C':3,'W':1,'B':2,'R':1});self.checkpoint(s)
        self.assert_closed(s,maker);self.cast_for(s,'A',maker,reserve={'C':2,'B':2,'R':1});self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token']
        self.assertEqual(2,len(tokens));token=tokens[0]
        self.cast_for(s,'A',bolt,[token.ref],reserve={'C':2,'B':2});self.assertIsNone(self.resolve(s))
        self.assertFalse(any(c.object_id==token.object_id and c.zone=='graveyard' for c in s.state.cards.values()))
        deaths=[e for e in s.state.turn_history.events if e.kind=='permanent_died']
        self.assertTrue(deaths[-1].token);self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.cast_for(s,'B',response,[s.state.cards[source.object_id].ref]);self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id];self.assertEqual('graveyard',current.zone)
        action=self.offered(s,current);self.assertIsNotNone(action)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertIsNotNone(self.offered(s,s.state.cards[other.object_id]))
        turn=s.state.turn_sequence
        self.until(s,lambda q:q.state.turn_sequence>turn and q.state.pending_decision is not None and q.state.pending_decision.kind=='priority')
        query=PublicHistoryQuerySpec('permanent_died','any_player',types_all=('creature',),schema_version=5,excluded_name='Ebondeath, Dracolich')
        self.assertEqual(0,public_history_query_value(s.state,'A',query))
        self.assertIsNone(self.offered(s,s.state.cards[other.object_id]));self.replay(s,load=True)
