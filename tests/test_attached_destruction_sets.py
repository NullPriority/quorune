"""CR701.8/608.2h: current attached sets freeze before one destruction."""
from dataclasses import asdict, FrozenInstanceError, replace
import json
from types import SimpleNamespace
import unittest

from quorune.affected_permanents import AffectedPermanentSetError, AffectedPermanentSetSpec
from quorune.attachment_destruction_model import (
    AttachmentDestructionSetSpec as Spec, AttachedDestructionScope as Scope,
    current_attachment_destruction_rows, select_attachment_destruction_rows as select,
)
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult


def row(ref, *, types=('creature',), subtypes=(), attached=None, seat='A', phased=False, zone='battlefield'):
    return ObjectQueryResult(object_id='object:'+ref, logical_object_id='logical:'+ref, ref=ref,
        printed_name='Generic attachment set member', owner=seat, controller=seat, zone=zone,
        types=types, subtypes=subtypes, attached_to_ref=attached, phased_out=phased)


class AttachmentDestructionModelTests(unittest.TestCase):
    def test_explicit_new_codec_is_immutable_and_preserves_all_four_scopes(self):
        for scope in Scope:
            spec=Spec(scope,'anchor' if scope in (Scope.AURAS_EQUIPMENT_ON_CREATURE,Scope.AURAS_ON_LAND) else None)
            self.assertEqual(spec,Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            self.assertEqual(spec,AffectedPermanentSetSpec.from_dict(spec.to_dict()))
            self.assertIsInstance(spec,AffectedPermanentSetSpec)
            with self.assertRaises(FrozenInstanceError):spec.target_ref='foreign'
        old=AffectedPermanentSetSpec(ObjectQuerySpec(zones=('battlefield',)))
        self.assertEqual({'query','controller_relation','target_controller','exclude_source','mana_value_filter'},set(asdict(old)))
        self.assertEqual(old,AffectedPermanentSetSpec.from_dict(old.to_dict()))
    def test_unknown_or_incomplete_new_fields_and_target_scope_mismatches_reject(self):
        raw=Spec(Scope.CREATURES_AND_ATTACHED).to_dict()
        for changed in ({'schema_version':True},{'schema_version':2},{'scope':'unknown'},
                {'target_ref':'unrequested'},{'selection_kind':'unknown'},{'owner':'B'}):
            with self.assertRaises(ValueError):Spec.from_dict({**raw,**changed})
        for scope in (Scope.AURAS_EQUIPMENT_ON_CREATURE,Scope.AURAS_ON_LAND):
            with self.assertRaises(ValueError):Spec(scope)
        with self.assertRaises(ValueError):Spec.from_dict({k:v for k,v in raw.items() if k!='target_ref'})
    def test_simultaneous_union_uses_all_creatures_and_all_direct_attachments_once(self):
        creature=row('body',seat='B')
        gear=row('gear',types=('artifact','creature'),subtypes=('equipment',),attached='body',seat='C')
        aura=row('aura',types=('enchantment',),subtypes=('aura',),attached='body',seat='D')
        fort=row('fort',types=('artifact',),subtypes=('fortification',),attached='body')
        indirect=row('indirect',types=('enchantment',),subtypes=('aura',),attached='aura')
        free=row('free',types=('artifact',),subtypes=('equipment',))
        rows=(creature,gear,aura,fort,indirect,free)
        result=select(rows,Spec(Scope.CREATURES_AND_ATTACHED),actor='A')
        self.assertEqual({'body','gear','aura','fort'},{r.ref for r in result})
        self.assertEqual(4,len(result))
    def test_targeted_creature_set_uses_current_aura_and_equipment_subtypes_only(self):
        rows=(row('body'),row('foreign'),row('aura',types=('enchantment',),subtypes=('aura',),attached='body',seat='B'),
              row('equipment',types=('artifact',),subtypes=('equipment',),attached='body',seat='C'),
              row('fort',types=('artifact',),subtypes=('fortification',),attached='body'),
              row('other',types=('enchantment',),subtypes=('aura',),attached='foreign'))
        spec=Spec(Scope.AURAS_EQUIPMENT_ON_CREATURE,'body')
        self.assertEqual({'aura','equipment'},{r.ref for r in select(rows,spec,actor='D')})
        self.assertEqual((),select((rows[0],),spec,actor='A'))
    def test_land_anchor_uses_current_land_type_and_current_attachment(self):
        rows=(row('land',types=('land','creature')),row('aura',types=('enchantment',),subtypes=('aura',),attached='land'),
              row('equipment',types=('artifact',),subtypes=('equipment',),attached='land'))
        spec=Spec(Scope.AURAS_ON_LAND,'land')
        self.assertEqual(('aura',),tuple(r.ref for r in select(rows,spec,actor='B')))
        with self.assertRaises(ValueError):select((replace(rows[0],types=('creature',)),*rows[1:]),spec,actor='B')
    def test_curses_on_actor_ignore_controller_owner_and_other_player_attachments(self):
        rows=(row('curse',types=('enchantment',),subtypes=('aura','curse'),attached='A',seat='B'),
              row('other',types=('enchantment',),subtypes=('aura','curse'),attached='B',seat='A'),
              row('noncurse',types=('enchantment',),subtypes=('aura',),attached='A',seat='C'))
        self.assertEqual(('curse',),tuple(r.ref for r in select(rows,Spec(Scope.CURSES_ON_ACTOR),actor='A')))
    def test_phased_and_nonbattlefield_members_and_anchors_are_excluded(self):
        body=row('body')
        aura=row('aura',types=('enchantment',),subtypes=('aura',),attached='body')
        spec=Spec(Scope.CREATURES_AND_ATTACHED)
        self.assertEqual(('body',),tuple(r.ref for r in select((body,replace(aura,phased_out=True)),spec,actor='A')))
        self.assertEqual((),select((replace(body,phased_out=True),aura),spec,actor='A'))
        self.assertEqual((),select((replace(body,zone='graveyard'),aura),spec,actor='A'))
    def test_reciprocal_object_and_player_projection_is_new_scope_local_and_fail_closed(self):
        body=SimpleNamespace(object_id='object:body',logical_object_id='logical:body',ref='body',zone='battlefield',attachments=['object:aura'])
        aura=SimpleNamespace(object_id='object:aura',logical_object_id='logical:aura',ref='aura',zone='battlefield',attachments=[],attached_to='object:body')
        curse=SimpleNamespace(object_id='object:curse',logical_object_id='logical:curse',ref='curse',zone='battlefield',attachments=[],attached_to='player:A')
        body.attached_to=None
        host=SimpleNamespace(state=SimpleNamespace(cards={x.object_id:x for x in (body,aura,curse)},players={'A':SimpleNamespace(seat='A',attachments=['object:curse'])}))
        rows=(row('body'),row('aura',types=('enchantment',),subtypes=('aura',)),row('curse',types=('enchantment',),subtypes=('aura','curse')))
        projected=current_attachment_destruction_rows(host,rows)
        self.assertEqual((None,'body','A'),tuple(r.attached_to_ref for r in projected))
        self.assertTrue(all(r.attached_to_ref is None for r in rows))
        self.assertEqual(projected,current_attachment_destruction_rows(host,(*rows,row('stack-only',zone='stack'))))
        host.state.players['A'].attachments=[]
        self.assertIsNone(current_attachment_destruction_rows(host,(rows[2],))[0].attached_to_ref)
        with self.assertRaises(ValueError):current_attachment_destruction_rows(host,(replace(rows[1],logical_object_id='old'),))


class AttachmentDestructionCompilerShapeTests(unittest.TestCase):
    def test_all_four_exact_clauses_use_existing_destroy_all_and_matching_anchor_targets(self):
        from quorune.compiler.destruction_templates import mass_destruction_effect_template
        from quorune.rules.destruction_regeneration_capability_shapes import mass_destruction_node_capabilities
        from quorune.attachment_destruction_model import ATTACHED_DESTRUCTION_CAPABILITY
        clauses=(('Destroy all creatures and all permanents attached to creatures.',Scope.CREATURES_AND_ATTACHED),
                 ('Destroy all Auras and Equipment attached to target creature.',Scope.AURAS_EQUIPMENT_ON_CREATURE),
                 ('Destroy all Curses attached to you.',Scope.CURSES_ON_ACTOR),
                 ('Destroy all Auras attached to target land.',Scope.AURAS_ON_LAND))
        for text,scope in clauses:
            with self.subTest(text=text):
                compiled=mass_destruction_effect_template(text).compiled()
                _,effects,schema,mechanics=compiled
                self.assertEqual('destroy_all',effects[0]['op']);self.assertEqual(scope.value,effects[0]['set']['scope'])
                self.assertIn(ATTACHED_DESTRUCTION_CAPABILITY,mass_destruction_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics))
                self.assertEqual(scope in (Scope.AURAS_ON_LAND,Scope.AURAS_EQUIPMENT_ON_CREATURE),schema is not None)
    def test_other_attachment_kinds_subjects_followups_and_extra_targets_stay_unrepresented(self):
        from quorune.compiler.attached_destruction_sets import attached_destruction_set_template
        for text in ('Destroy all Auras attached to target player.','Destroy all Equipment attached to target creature.',
                     'Destroy all creatures and all permanents attached to creatures. Draw a card.',
                     'Destroy all Auras attached to up to two target lands.','Destroy all Curses attached to target opponent.',
                     'Destroy all permanents other than this Equipment.','Destroy all creatures of the creature type of your choice.'):
            self.assertIsNone(attached_destruction_set_template(text))
    def test_fine_dependency_is_owned_by_new_payload_without_coverage_annotations(self):
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.attachment_destruction_model import ATTACHED_DESTRUCTION_CAPABILITY
        spec=Spec(Scope.CURSES_ON_ACTOR)
        self.assertIn(ATTACHED_DESTRUCTION_CAPABILITY,typed_result_amount_runtime_dependencies({'op':'destroy_all','set':spec.to_dict()}))
        malformed={**spec.to_dict(),'schema_version':True}
        with self.assertRaises(ValueError):typed_result_amount_runtime_dependencies(malformed)
    def test_new_target_schema_cannot_be_withdrawn_or_reinterpreted_as_player_target(self):
        from quorune.compiler.attached_destruction_sets import attached_destruction_set_template
        from quorune.rules.destruction_regeneration_capability_shapes import mass_destruction_node_capabilities
        _,effects,schema,mechanics=attached_destruction_set_template('Destroy all Auras attached to target land.').compiled()
        for broken in (None,{'zones':['player'],'categories':['player'],'count':1},
                       {**schema,'count':2},{**schema,'types_any':['creature']}):
            self.assertEqual((),mass_destruction_node_capabilities(effects=effects,target_schema=broken,mechanic_ids=mechanics))


from pathlib import Path
import tempfile
from unittest.mock import patch
from common import ROOT
from quorune.attachments import attach_objects, attach_to_player
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_public_condition_self_replacements as controls

FIXTURE=ROOT/'tests/fixtures/attached-destruction-set-cards.json'
CANDIDATES=('End Hostilities','Strip Bare','Witchbane Orb','Street Sweeper')


class AttachmentDestructionRegistrationTests(unittest.TestCase):
    def test_four_complete_originals_close_normally_and_unrepresented_choice_neighbors_do_not(self):
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'originals.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,'Worldslayer','Extinction'):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False)
                        register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=registry,
                            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                        self.assertEqual(name in CANDIDATES,bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),bound['blockers'])


class AttachmentDestructionGameplayTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicConditionSelfReplacementTests.session
    start=controls.PublicConditionSelfReplacementTests.start
    add=controls.PublicConditionSelfReplacementTests.add
    prepare=controls.PublicConditionSelfReplacementTests.prepare
    ready=controls.PublicConditionSelfReplacementTests.ready
    checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
    resolve=controls.PublicConditionSelfReplacementTests.resolve
    replay=controls.PublicConditionSelfReplacementTests.replay
    binding=controls.PublicConditionSelfReplacementTests.binding
    priority=controls.PublicConditionSelfReplacementTests.priority
    cast=controls.PublicConditionSelfReplacementTests.cast
    reject=controls.PublicConditionSelfReplacementTests.reject
    advance=controls.PublicConditionSelfReplacementTests.advance
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'attached-destruction.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Attached destruction',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def closed(self,s,card):
        bound=self.binding(s,card)
        self.assertTrue(bound['strict_capability_ready'],(card.printed_name,bound['blockers']))
        self.assertEqual('capability_closed',bound['trust_basis'])
    def attach(self,s,source,target):
        self.closed(s,source)
        attach_objects(s.state.cards,source,target,source_timestamp=s.engine._next_zone_timestamp(),players=s.state.players)
    def cast_as(self,s,card,targets=(),seat='A'):
        self.closed(s,card);self.priority(s,seat)
        action=next(x for x in s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions'] if x['id']=='cast:'+card.ref)
        command={'action_id':action['id'],'targets':list(targets),'pay':'auto'};self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
    def reach(self,s,kind,seat=None):
        self.advance(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind==kind and (seat is None or game.state.pending_decision.actors==[seat]))
    def test_actual_end_hostilities_destroys_one_simultaneous_union_and_preserves_later_death_token(self):
        s=self.start(930010);spell=self.add(s.engine,'End Hostilities',zone='hand');self.closed(s,spell)
        body=self.add(s.engine,'Doomed Traveler',seat='B');self.closed(s,body)
        aura=self.add(s.engine,'Unholy Strength',seat='C',ref='wipe-aura');gear=self.add(s.engine,'Short Sword',seat='B',ref='wipe-gear')
        self.attach(s,aura,body);self.attach(s,gear,body)
        survivor=self.add(s.engine,'Darksteel Myr',seat='D');self.closed(s,survivor)
        survivor_aura=self.add(s.engine,'Unholy Strength',ref='wipe-indestructible-aura');self.attach(s,survivor_aura,survivor)
        free=self.add(s.engine,'Short Sword',seat='C',ref='wipe-unattached-equipment')
        land=self.add(s.engine,'Forest',seat='D');growth=self.add(s.engine,'Wild Growth',seat='C');self.attach(s,growth,land)
        secret=self.add(s.engine,'Generic Bound Growth',seat='D',zone='hand',ref='wipe-private-D')
        self.ready(s);self.cast(s,spell);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in (body,aura,gear,survivor_aura)))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='battlefield' for c in (survivor,free,land,growth)))
        self.assertEqual(1,sum(c.zone=='battlefield' and c.is_token and 'spirit' in c.printed_name.casefold() for c in s.state.cards.values()))
        for principal in ('pilot:A','pilot:B','pilot:C','spectator'):self.assertNotIn(secret.ref,str(s.packet(principal,full=True)))
        self.replay(s,load=True)
    def test_actual_strip_bare_targets_only_the_creature_and_destroys_its_foreign_attachments(self):
        s=self.start(930020);spell=self.add(s.engine,'Strip Bare',zone='hand');self.closed(s,spell)
        body=self.add(s.engine,'Grizzly Bears',seat='B')
        aura=self.add(s.engine,'Unholy Strength',seat='B');gear=self.add(s.engine,'Short Sword',seat='C')
        self.attach(s,aura,body);self.attach(s,gear,body)
        free=self.add(s.engine,'Short Sword',seat='D',ref='strip-unattached')
        self.ready(s);self.cast(s,spell,[body.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in (aura,gear)))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='battlefield' for c in (body,free)));self.replay(s,load=True)
    def test_actual_witchbane_orb_untargeted_etb_removes_only_own_curses_then_hexproof_rejects_spell(self):
        s=self.start(930030);source=self.add(s.engine,'Witchbane Orb',zone='hand');self.closed(s,source)
        first=self.add(s.engine,'Curse of Exhaustion',seat='B',ref='orb-curse-on-A');other=self.add(s.engine,'Curse of Exhaustion',seat='C',ref='orb-curse-on-B')
        for card,seat in ((first,'A'),(other,'B')):
            self.closed(s,card);attach_to_player(s.state.cards,s.state.players,card,seat,source_timestamp=s.engine._next_zone_timestamp())
        burn=self.add(s.engine,'Shock',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'R':1})
        self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone);self.assertEqual('battlefield',s.state.cards[other.object_id].zone)
        self.priority(s,'B');self.reject(s,{'action_id':'cast:'+burn.ref,'targets':['A'],'pay':'auto'},'B')
        self.cast_as(s,burn,['C'],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['C'].life);self.replay(s,load=True)
    def test_actual_street_sweeper_mandatory_land_target_includes_empty_set_and_survives_source_departure(self):
        import test_combat_prevention_source_scopes as combat
        for attached in (False,True):
            with self.subTest(attached=attached):
                s=self.start(930040+attached);source=self.add(s.engine,'Street Sweeper',zone='hand');self.closed(s,source)
                self.add(s.engine,'Fervor');land=self.add(s.engine,'Forest',seat='B')
                if attached:
                    aura=self.add(s.engine,'Wild Growth',seat='C');self.attach(s,aura,land)
                    removal=self.add(s.engine,'Murder',seat='B',zone='hand')
                    for i in range(3):self.add(s.engine,'Swamp',seat='B',ref='sweeper-response-swamp-'+str(i))
                self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
                combat.CombatPreventionSourceRuntimeTests.attack(self,s,'A',[(source,'B')]);decision=self.resolve(s)
                self.assertEqual('semantic.target',decision.kind);s=self.replay(s,load=True)
                command={'action_id':'choose','targets':[land.ref]};self.reject(s,command,'D')
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                if attached:self.cast_as(s,removal,[source.ref],seat='B')
                self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[land.object_id].zone)
                if attached:
                    self.assertEqual('graveyard',s.state.cards[aura.object_id].zone);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.replay(s,load=True)
    def test_fresh_unrestricted_set_mutation_is_killed_by_actual_strip_bare(self):
        from quorune import attachment_destruction_model as owned
        original=owned.select_attachment_destruction_rows
        def mutant(rows,spec,*,actor):
            if spec.scope is Scope.AURAS_EQUIPMENT_ON_CREATURE:return tuple(rows)
            return original(rows,spec,actor=actor)
        with patch.object(owned,'select_attachment_destruction_rows',mutant):
            with self.assertRaises(AssertionError):self.test_actual_strip_bare_targets_only_the_creature_and_destroys_its_foreign_attachments()
    def test_fault_after_first_attachment_movement_restores_the_entire_set_and_retries(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.start(930050);spell=self.add(s.engine,'Strip Bare',zone='hand');self.closed(s,spell)
        body=self.add(s.engine,'Grizzly Bears',seat='B')
        aura=self.add(s.engine,'Unholy Strength',seat='B');gear=self.add(s.engine,'Short Sword',seat='C')
        self.attach(s,aura,body);self.attach(s,gear,body)
        self.ready(s);self.cast(s,spell,[body.ref]);original=ZoneTransitionOwner._commit_move;partial=[];failed=None
        def fault(owner,plan,departure,**kwargs):
            value=original(owner,plan,departure,**kwargs)
            if plan.card.object_id==aura.object_id:
                partial.append(plan.card.zone);raise GameRuleError('Injected attached destruction after movement fault')
            return value
        with patch.object(ZoneTransitionOwner,'_commit_move',fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
                if not result.ok:self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual(['graveyard'],partial);self.assertIsNotNone(failed)
        for card in (aura,gear):
            self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
            self.assertEqual(body.object_id,s.state.cards[card.object_id].attached_to)
            self.assertIn(card.object_id,s.state.cards[body.object_id].attachments)
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in (aura,gear)));self.replay(s,load=True)
    def test_actual_strip_bare_uses_current_attachments_after_a_real_squire_move(self):
        s=self.start(930060);spell=self.add(s.engine,'Strip Bare',zone='hand');self.closed(s,spell)
        body=self.add(s.engine,'Grizzly Bears',seat='B',ref='squire-original-anchor')
        other=self.add(s.engine,'Grizzly Bears',seat='B',ref='squire-new-anchor')
        aura=self.add(s.engine,'Unholy Strength',seat='C');gear=self.add(s.engine,'Short Sword',seat='B')
        self.attach(s,aura,body);self.attach(s,gear,body)
        squire=self.add(s.engine,'Brass Squire',seat='B');squire.acquired_control_turn_count=-1;self.closed(s,squire)
        self.ready(s);self.cast(s,spell,[body.ref]);self.priority(s,'B')
        action=next(x for x in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if x['id'].startswith('activate:'+squire.ref+':'))
        command={'action_id':action['id'],'targets':{'target_0':[gear.ref],'target_1':[other.ref]},'pay':'auto'}
        self.reject(s,command,'D');result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[gear.object_id].zone);self.assertEqual(other.object_id,s.state.cards[gear.object_id].attached_to)
        self.assertEqual('battlefield',s.state.cards[body.object_id].zone);self.replay(s,load=True)
    def test_actual_strip_bare_allows_empty_set_and_rechecks_anchor_hexproof(self):
        for protected in (False,True):
            with self.subTest(protected=protected):
                s=self.start(930070+protected);spell=self.add(s.engine,'Strip Bare',zone='hand');self.closed(s,spell)
                body=self.add(s.engine,'Grizzly Bears',seat='B')
                if protected:
                    gear=self.add(s.engine,'Short Sword',seat='C');self.attach(s,gear,body)
                    response=self.add(s.engine,"Ranger's Guile",seat='B',zone='hand');s.state.players['B'].mana_pool['G']=1
                self.ready(s);self.cast(s,spell,[body.ref])
                if protected:self.cast_as(s,response,[body.ref],seat='B')
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.assertEqual('battlefield',s.state.cards[body.object_id].zone)
                if protected:self.assertEqual('battlefield',s.state.cards[gear.object_id].zone)
                self.replay(s,load=True)

    def test_actual_competing_replacements_retain_the_new_set_identity_through_pending_replay(self):
        s=self.start(930080);spell=self.add(s.engine,'End Hostilities',zone='hand');self.closed(s,spell)
        first=self.add(s.engine,'Rest in Peace',ref='attached-replacement-A')
        second=self.add(s.engine,'Rest in Peace',seat='C',ref='attached-replacement-C')
        self.closed(s,first);self.closed(s,second)
        body=self.add(s.engine,'Grizzly Bears',seat='B')
        aura=self.add(s.engine,'Unholy Strength',seat='B');gear=self.add(s.engine,'Short Sword',seat='C')
        self.attach(s,aura,body);self.attach(s,gear,body)
        self.ready(s);self.cast(s,spell);decision=self.resolve(s)
        self.assertEqual('replacement.order',decision.kind)
        self.assertTrue(all(s.state.cards[c.object_id].zone=='battlefield' for c in (body,aura,gear)))
        s=self.replay(s,load=True);choices=0
        for _ in range(10):
            decision=s.state.pending_decision
            if decision is None or decision.kind!='replacement.order':break
            seat=decision.actors[0];options=s.packet('pilot:'+seat,full=True)['decision']['ctx']['options']
            self.assertEqual(2,len(options))
            command={'action_id':'choose','replacement':options[0]['id']}
            self.reject(s,command,'D' if seat!='D' else 'A')
            result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary);choices+=1
            self.resolve(s)
        self.assertGreaterEqual(choices,3);self.assertFalse(s.state.stack)
        self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in (body,aura,gear,spell)))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='battlefield' for c in (first,second)))
        self.replay(s,load=True)
    def test_actual_registered_set_dependency_remains_required_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.attachment_destruction_model import ATTACHED_DESTRUCTION_CAPABILITY as cap
        s=self.start(930090);card=self.add(s.engine,'End Hostilities',zone='hand');self.closed(s,card)
        program=next(p for p in s.engine.semantics.programs_for_oracle(card.oracle_id)
                     if any(e.get('op')=='destroy_all' for e in p.effects))
        deps=tuple(value for value in program.capability_dependencies if value!=cap)
        missing=replace(program,coverage=(),capability_dependencies=deps,
            capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
