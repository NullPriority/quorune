"""CR 608.2h: target controller, immutable owner and spell controller differ."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT,packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase,CardRecord
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.closed_effect_program_shapes import closed_effect_program_runtime_dependencies
from quorune.rules.target_recipient_results import target_recipient_result_node_capabilities
from quorune.semantic_runtime import SemanticNodeError
from quorune.semantic_runtime.values import resolve_semantic_value
from quorune.target_controller_information import target_owner_reference
from scripts.build_test_database import build_fixture_database
import test_singular_public_permanent_sets as actions

CAP='resolution.effect_program.target_recipient_result'
OWNER_CAP='resolution.target_owner_information'


class TargetRecipientModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def record(self,text,*,permanent=False):
        return CardRecord(oracle_id='fixture:target-recipient-results',name='Recipient Witness',mana_cost='{G}',mana_value=1,
            type_line='Creature — Human' if permanent else 'Instant',oracle_text=text,power='2' if permanent else None,
            toughness='2' if permanent else None,loyalty=None,defense=None,colors=('G',),color_identity=('G',),keywords=(),
            produced_mana=(),layout='normal',released_at=None,legalities={'commander':'legal'},faces=(),raw={})

    def compile(self,text,*,permanent=False,registry=None):
        return compile_oracle_card(self.record(text,permanent=permanent),capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def owner_witness(self,owner='D'):
        host=SimpleNamespace(state=SimpleNamespace(players={seat:object() for seat in 'ABCD'},
            cards={'object':SimpleNamespace(owner='D',controller='B',zone_change_counter=2)}))
        item=SimpleNamespace(controller='A',targets=['target'],context={'target_snapshots':{
            'target':{'object_id':'object','zone_change_counter':2,'owner':owner,'controller':'C'}}})
        return host,item

    def test_owner_value_uses_the_physical_owner_and_rejects_corrupt_reference_or_snapshot(self):
        host,item=self.owner_witness()
        self.assertEqual('D',resolve_semantic_value(host,target_owner_reference(),item))
        host.state.cards['object'].controller='A'
        self.assertEqual('D',resolve_semantic_value(host,target_owner_reference(),item))
        for change in ({'index':True},{'index':-1},{'index':1},{'schema_version':True},{'owner':'A'}):
            with self.subTest(change=change),self.assertRaises((SemanticNodeError,IndexError)):
                resolve_semantic_value(host,{**target_owner_reference(),**change},item)
        host,item=self.owner_witness('B')
        with self.assertRaises(SemanticNodeError):resolve_semantic_value(host,target_owner_reference(),item)
        for counter in (-1,3,True):
            host,item=self.owner_witness();item.context['target_snapshots']['target']['zone_change_counter']=counter
            with self.assertRaises(SemanticNodeError):resolve_semantic_value(host,target_owner_reference(),item)

    def test_owner_reader_omission_is_detected_through_the_actual_value_dispatch(self):
        host,item=self.owner_witness()
        with patch('quorune.target_controller_information.resolve_target_owner_information',return_value='A'):
            with self.assertRaises(AssertionError):self.assertEqual('D',resolve_semantic_value(host,target_owner_reference(),item))

    def test_fixed_recipient_results_compile_in_spell_trigger_and_activation_contexts(self):
        bodies=("Destroy target creature. Its owner gains 4 life.",
            'Destroy target permanent. Its controller creates a 3/3 green Elephant creature token.',
            "Destroy target creature. It can't be regenerated. Its controller creates a 3/3 green Ape creature token.",
            'Exile target card from a graveyard. Its owner creates a 1/1 white Spirit creature token with flying.',
            'Destroy target land. Its controller discards a card.',
            "Return target permanent to its owner's hand. Then that player discards a card.",
            'Destroy target creature with flying. Its controller gets a poison counter.')
        for body in bodies:
            for prefix in ('','{2}: ','When this creature enters, '):
                with self.subTest(body=body,prefix=prefix):
                    ir=self.compile(prefix+body,permanent=bool(prefix))
                    self.assertEqual('exact',ir.status,ir.material_residuals)
                    node=next(node for node in ir.faces[0].nodes if node.effects)
                    self.assertIn(CAP,node.capability_dependencies)
                    self.assertEqual(1,node.target_schema['count'])
                    self.assertEqual(2,len(node.effects))

    def test_unknown_siblings_optional_targets_dynamic_results_and_wrong_antecedents_stay_residual(self):
        # Existing public-quantity composition owns this amount independently.
        self.assertEqual('exact',self.compile('Destroy target creature. Its owner gains life equal to its toughness.').status)
        for text in ('Destroy up to one target creature. Its controller creates a 3/3 green Beast creature token.',
            'Destroy target creature. That land\'s controller creates a 3/3 green Beast creature token.',
            'Exile target card from a graveyard. Its controller creates a 3/3 green Beast creature token.',
            'Destroy target creature. Its owner gains life equal to its unrepresented characteristic.',
            'Destroy target creature. Its controller creates a token with an unrepresented ability.',
            'Destroy target creature. If it dies, its controller creates a 3/3 green Beast creature token.',
            'Destroy target creature. Then that player discards a card.',
            'Destroy target creature. Its owner gains 4 life. Invent an unrepresented sibling.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text).status)

    def test_actual_recipient_shapes_infer_required_owners_and_fail_closed_when_tampered(self):
        node,=self.compile('Destroy target creature. Its owner gains 4 life.').faces[0].nodes
        probe=SimpleNamespace(effects=node.effects,target_schema=node.target_schema,coverage=node.mechanics)
        required=closed_effect_program_runtime_dependencies(probe)
        self.assertTrue({CAP,OWNER_CAP}<=set(required))
        for mutation in ('index','field','order','mechanic'):
            effects=deepcopy(list(node.effects));mechanics=list(node.mechanics)
            if mutation=='index':effects[1]['player']['index']=1
            elif mutation=='field':effects[1]['player']['unexpected']='D'
            elif mutation=='order':effects.reverse()
            else:mechanics.remove('fixed-target-recipient-result')
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                closed_effect_program_runtime_dependencies(SimpleNamespace(effects=effects,
                    target_schema=node.target_schema,coverage=mechanics))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for missing in (CAP,OWNER_CAP,'permanent.destroy.effect'):
            changed=deepcopy(raw);next(row for row in changed['capabilities'] if row['id']==missing).update(status='blocked',blockers=['Owner withdrawn'])
            self.assertNotEqual('exact',self.compile('Destroy target creature. Its owner gains 4 life.',registry=CapabilityRegistry(changed)).status)

    def test_target_controller_manifest_requires_exact_creature_exile_and_top_result(self):
        from quorune.target_controller_reference import target_controller_reference

        text = "Exile target creature. Its controller manifests the top card of their library."
        ir = self.compile(text)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((
            {"op": "exile_permanent", "card": "$target.0"},
            {"op": "manifest_card", "player": target_controller_reference(), "mode": "top"},
        ), node.effects)
        expected = {CAP, "resolution.target_controller_information", "keyword_action.manifest.card"}
        self.assertLessEqual(expected, set(node.capability_dependencies))
        for variant in (
            text.replace("Exile", "Destroy"),
            text.replace("target creature", "target artifact"),
            text.replace("controller", "owner"),
            text.replace("the top card", "the top two cards"),
            text + " Draw a card.",
        ):
            with self.subTest(variant=variant):
                self.assertNotEqual("exact", self.compile(variant).status)
        with patch("quorune.compiler.target_recipient_results.target_recipient_result_template", return_value=None):
            self.assertNotEqual("exact", self.compile(text).status)

    def test_recipient_compiler_omission_cannot_satisfy_the_original_positive_contract(self):
        with patch('quorune.compiler.target_recipient_results.target_recipient_result_template',return_value=None):
            self.assertNotEqual('exact',self.compile('Destroy target creature. Its owner gains 4 life.').status)


class TargetRecipientNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=actions.SingularPublicSetNativeTests.reject
    finish=actions.SingularPublicSetNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'target-recipient.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/target-recipient-result-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Target recipient results',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def tokens(self,session,seat):
        return [card for card in session.state.cards.values() if not card.is_card_object
            and card.zone=='battlefield' and card.controller==seat]

    def test_new_producer_requires_its_recipient_recipe_even_without_annotations(self):
        session=self.session(999000);source=self.add(session.engine,"Misfortune's Gain")
        self.assert_closed(session,source)
        program=next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if program.event=='resolve' and program.active_zone=='stack')
        for missing in (CAP,OWNER_CAP,'permanent.destroy.effect'):
            dependencies=tuple(cap for cap in program.capability_dependencies if cap!=missing)
            changed=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+missing,binding['blockers'])
        effects=deepcopy(program.effects);effects[1]['player']='$controller'
        dropped={CAP,OWNER_CAP};dependencies=tuple(cap for cap in program.capability_dependencies if cap not in dropped)
        changed=replace(program,effects=effects,coverage=[word for word in program.coverage if word not in {
            'fixed-target-recipient-result','fixed-target-owner-information'}],capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
        binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(binding['strict']);self.assertTrue(any('recipient recipe' in blocker for blocker in binding['blockers']),binding['blockers'])

    def control_response(self,session,target):
        response=self.add(session.engine,'Blind with Anger',seat='B',zone='hand')
        self.add(session.engine,'Mountain',seat='B')
        for index in range(3):self.add(session.engine,'Wastes',seat='B',ref='recipient-response-payer-'+str(index))
        return response

    def test_actual_reality_shift_manifests_for_departure_controller_after_exile_or_redirect(self):
        from quorune.manifest import is_manifested
        from quorune.replacement.model import ReplacementClass, ReplacementEffect
        from quorune.replacement.operations import SetField
        from quorune.semantic_runtime import zone_replacements

        collect = zone_replacements.collect_zone_change_replacement_effects
        redirect = ReplacementEffect(
            effect_id="fixture:reality-shift-redirect", source_id="fixture:redirect-source",
            event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
            conditions={"destination": {"eq": "exile"}},
            operations=(SetField("destination", "graveyard"),),
        )

        def collect_with_redirect(host, **kwargs):
            return (*collect(host, **kwargs), redirect)

        for redirected in (False, True):
            with self.subTest(replaced_exile=redirected):
                scope = patch.object(zone_replacements, "collect_zone_change_replacement_effects",
                                     side_effect=collect_with_redirect) if redirected else nullcontext()
                with scope:
                    session = self.session(999061 + redirected)
                    engine = session.engine
                    spell = self.add(engine, "Reality Shift", zone="hand")
                    target = self.add(engine, "Grizzly Bears", seat="C")
                    target.owner = "D"
                    top = self.add(engine, "Counterspell", seat="B", zone="library")
                    backup = self.add(engine, "Wind Drake", seat="C", zone="library")
                    response = self.control_response(session, target)
                    self.assert_closed(session, spell)
                    self.prepare(session, mana={"C": 1, "U": 1})
                    self.checkpoint(session)
                    self.cast(session, "A", spell, targets=[target.ref])
                    self.cast(session, "B", response, targets=[target.ref])
                    self.resolve_top(session)
                    self.assertEqual("B", session.state.cards[target.object_id].controller)
                    session = self.replay(session, load=True)
                    self.assertIsNone(self.resolve(session))

                    moved = session.state.cards[target.object_id]
                    self.assertEqual("graveyard" if redirected else "exile", moved.zone)
                    self.assertEqual("D", moved.owner)
                    card = session.state.cards[top.object_id]
                    self.assertEqual(("battlefield", "B", "B"), (card.zone, card.controller, card.owner))
                    self.assertTrue(is_manifested(card))
                    self.assertEqual(2, session.engine._numeric_stat(card.object_id, "power"))
                    self.assertEqual(2, session.engine._numeric_stat(card.object_id, "toughness"))
                    self.assertEqual("library", session.state.cards[backup.object_id].zone)
                    for viewer in "ABCD":
                        packet = packet_without_decision_capability(session.packet("pilot:" + viewer, full=True))
                        if viewer == "B":
                            self.assertIn("Counterspell", json.dumps(packet))
                        else:
                            self.assertNotIn("Counterspell", json.dumps(packet))
                    self.replay(session, load=True)

    def test_actual_reality_shift_fizzles_when_the_only_target_leaves_before_resolution(self):
        session = self.session(999064)
        engine = session.engine
        spell = self.add(engine, "Reality Shift", zone="hand")
        target = self.add(engine, "Grizzly Bears", seat="B")
        top = self.add(engine, "Counterspell", seat="B", zone="library")
        response = self.add(engine, "Unsummon", seat="B", zone="hand")
        self.add(engine, "Island", seat="B")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"C": 1, "U": 1})
        self.checkpoint(session)
        self.cast(session, "A", spell, targets=[target.ref])
        self.cast(session, "B", response, targets=[target.ref])
        self.resolve_top(session)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.assertEqual(top.object_id, session.state.players["B"].zones["library"][-1])
        self.assertFalse(session.state.cards[top.object_id].face_down)
        self.replay(session, load=True)

    def test_actual_gift_uses_departure_controller_after_a_real_control_change_and_replacement(self):
        session=self.session(999001);source=self.add(session.engine,'Generous Gift',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='C');target.owner='D'
        response=self.control_response(session,target);doubler=self.add(session.engine,'Parallel Lives',seat='B')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-gift-private-card')
        self.assert_closed(session,source);self.assert_closed(session,doubler)
        self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session)
        self.cast(session,'A',source,targets=[target.ref]);self.cast(session,'B',response,targets=[target.ref]);self.resolve_top(session)
        self.assertEqual('B',session.state.cards[target.object_id].controller)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id,session.state.players['D'].zones['graveyard'])
        self.assertEqual(2,len(self.tokens(session,'B')))
        for card in self.tokens(session,'B'):
            self.assertEqual('B',card.owner);self.assertEqual((3,3),(session.engine._numeric_stat(card.object_id,'power'),session.engine._numeric_stat(card.object_id,'toughness')))
            self.assertIn('Elephant',session.engine._effective_card_data(card)['type_line'])
        for seat in 'ACD':self.assertEqual([],self.tokens(session,seat))
        self.finish(session,private)

    def test_actual_indestructible_still_gets_a_token_but_an_illegal_sole_target_gets_none(self):
        for bounce in (False,True):
            with self.subTest(bounce=bounce):
                session=self.session(999010+bounce);source=self.add(session.engine,'Beast Within',zone='hand')
                if bounce:target=self.add(session.engine,'Grizzly Bears',seat='B')
                else:target=self.add(session.engine,'Darksteel Myr',seat='B')
                target.owner='D';response=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-destruction-private-card')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'G':1});self.checkpoint(session)
                self.cast(session,'A',source,targets=[target.ref])
                if bounce:self.cast(session,'B',response,targets=[target.ref]);self.resolve_top(session)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual('hand' if bounce else 'battlefield',session.state.cards[target.object_id].zone)
                self.assertEqual(0 if bounce else 1,len(self.tokens(session,'B')))
                if not bounce:self.assertEqual('B',self.tokens(session,'B')[0].owner)
                self.finish(session,private)

    def test_actual_owner_life_is_distinct_from_caster_original_and_current_controller(self):
        session=self.session(999020);source=self.add(session.engine,"Misfortune's Gain",zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='C');target.owner='D';response=self.control_response(session,target)
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-owner-life-private-card')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'W':1});self.checkpoint(session)
        self.cast(session,'A',source,targets=[target.ref]);self.cast(session,'B',response,targets=[target.ref]);self.resolve_top(session)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id,session.state.players['D'].zones['graveyard'])
        self.assertEqual([40,40,40,44],[session.state.players[seat].life for seat in 'ABCD']);self.finish(session,private)

    def test_actual_destroyed_token_owner_result_finishes_before_quiescent_token_removal(self):
        session=self.session(999025);source=self.add(session.engine,"Misfortune's Gain",zone='hand')
        create=self.add(session.engine,'Raise the Alarm',seat='D',zone='hand')
        self.add(session.engine,'Plains',seat='D');self.add(session.engine,'Wastes',seat='D',ref='recipient-token-creator-payer')
        response=self.control_response(session,None)
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-token-owner-private-card')
        self.assert_closed(session,source);self.assert_closed(session,create)
        self.prepare(session,mana={'C':3,'W':1});self.checkpoint(session)
        self.cast(session,'D',create);self.assertIsNone(self.resolve(session));self.assertEqual(2,len(self.tokens(session,'D')))
        target=self.tokens(session,'D')[0]
        self.cast(session,'A',source,targets=[target.ref]);self.cast(session,'B',response,targets=[target.ref]);self.resolve_top(session)
        self.assertEqual('B',session.state.cards[target.object_id].controller)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([40,40,40,44],[session.state.players[seat].life for seat in 'ABCD'])
        for seat in 'ABCD':
            self.assertNotIn(target.object_id,session.state.players[seat].zones['battlefield'])
            self.assertNotIn(target.object_id,session.state.players[seat].zones['graveyard'])
        self.finish(session,private)

    def test_actual_pharika_exiles_a_graveyard_card_and_gives_its_owner_the_complete_token(self):
        session=self.session(999030);source=self.add(session.engine,'Pharika, God of Affliction')
        target=self.add(session.engine,'Grizzly Bears',seat='D',zone='graveyard')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-graveyard-private-card')
        self.assert_closed(session,source);self.prepare(session,mana={'B':1,'G':1});self.checkpoint(session)
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':offered['id'],'targets':[target.ref],'pay':'auto'};self.reject(session,'B',command)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id,session.state.players['D'].zones['exile']);self.assertEqual(1,len(self.tokens(session,'D')))
        token=self.tokens(session,'D')[0];self.assertEqual('D',token.owner)
        data=session.engine._effective_card_data(token);self.assertIn('Enchantment',data['type_line']);self.assertIn('Creature',data['type_line'])
        self.assertEqual({'B','G'},set(data['colors']));self.assertIn('deathtouch',session.engine._combat_keywords(token))
        self.finish(session,private)

    def test_actual_discard_commits_movement_then_only_the_affected_player_can_choose(self):
        session=self.session(999040);source=self.add(session.engine,"Assassin's Strike",zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.owner='D'
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-discard-private-card')
        chosen=session.state.cards[session.state.players['B'].zones['hand'][0]]
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'B':2});self.checkpoint(session)
        self.cast(session,'A',source,targets=[target.ref]);decision=self.resolve(session)
        self.assertEqual('choice.apnap',decision.kind);self.assertEqual(['pilot:B'],session.pending_principals())
        self.assertIn(target.object_id,session.state.players['D'].zones['graveyard'])
        for seat in 'ACD':
            packet=packet_without_decision_capability(session.packet('pilot:'+seat,full=True))
            self.assertNotIn(chosen.ref,json.dumps(packet))
        session=self.replay(session,load=True);command={'action_id':'choose','cards':[chosen.ref]};self.reject(session,'D',command)
        result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertIn(chosen.object_id,session.state.players['B'].zones['graveyard']);self.finish(session,private)

    def test_actual_recoil_returns_to_owner_then_only_that_owner_can_discard_the_returned_card(self):
        session=self.session(999045);source=self.add(session.engine,'Recoil',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='C');target.owner='D';response=self.control_response(session,target)
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-recoil-private-card')
        hidden=session.state.cards[session.state.players['D'].zones['hand'][0]]
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'U':1,'B':1});self.checkpoint(session)
        self.cast(session,'A',source,targets=[target.ref]);self.cast(session,'B',response,targets=[target.ref]);self.resolve_top(session)
        decision=self.resolve(session);self.assertEqual('choice.apnap',decision.kind)
        self.assertEqual(['pilot:D'],session.pending_principals());self.assertIn(target.object_id,session.state.players['D'].zones['hand'])
        for seat in 'ABC':
            packet=packet_without_decision_capability(session.packet('pilot:'+seat,full=True))
            self.assertNotIn(hidden.ref,json.dumps(packet))
        session=self.replay(session,load=True);command={'action_id':'choose','cards':[target.ref]};self.reject(session,'B',command)
        result=session.act('pilot:D',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id,session.state.players['D'].zones['graveyard']);self.finish(session,private)

    def test_actual_get_lost_maps_activate_for_the_recipient_and_explore_into_that_players_hand(self):
        session=self.session(999047);source=self.add(session.engine,'Get Lost',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='B',ref='recipient-map-removed-creature')
        survivor=self.add(session.engine,'Grizzly Bears',seat='B',ref='recipient-map-exploring-creature')
        self.add(session.engine,'Wastes',seat='B')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-map-private-card')
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',source,targets=[target.ref]);self.assertIsNone(self.resolve(session))
        maps=self.tokens(session,'B');self.assertEqual(2,len(maps))
        for token in maps:self.assertEqual('B',token.owner);self.assertIn('Map',session.engine._effective_card_data(token)['type_line'])
        self.until(session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main' and s.state.priority_player=='B')
        token=maps[0];before=len(session.state.players['B'].zones['hand']);foreign=len(session.state.players['A'].zones['hand'])
        offered=next(row for row in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+token.ref+':'))
        command={'action_id':offered['id'],'targets':[survivor.ref],'pay':'auto'};self.reject(session,'A',command)
        result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(before+1,len(session.state.players['B'].zones['hand']))
        self.assertEqual(foreign,len(session.state.players['A'].zones['hand']))
        self.assertEqual(0,session.state.cards[survivor.object_id].counters.get('+1/+1',0))
        self.assertEqual(1,len(self.tokens(session,'B')));self.assertNotIn(token.object_id,session.state.players['B'].zones['graveyard'])
        self.finish(session,private)

    def test_actual_pistus_poison_and_pongify_regeneration_prohibition_keep_their_owner_results(self):
        for poison in (False,True):
            with self.subTest(poison=poison):
                session=self.session(999050+poison)
                if poison:
                    source=self.add(session.engine,'Pistus Strike',zone='hand');target=self.add(session.engine,'Wind Drake',seat='C')
                else:
                    source=self.add(session.engine,'Pongify',zone='hand');target=self.add(session.engine,'Grizzly Bears',seat='C')
                    shield=self.add(session.engine,'Regenerate',seat='C',zone='hand');self.add(session.engine,'Forest',seat='C');self.add(session.engine,'Wastes',seat='C')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='recipient-counter-private-card')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'G':1} if poison else {'U':1});self.checkpoint(session)
                if not poison:self.cast(session,'C',shield,targets=[target.ref]);self.assertIsNone(self.resolve(session));self.assertEqual(1,session.state.cards[target.object_id].regeneration_shields)
                self.cast(session,'A',source,targets=[target.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
                if poison:
                    self.assertEqual(1,session.state.players['C'].poison);self.assertEqual([],self.tokens(session,'C'))
                else:
                    self.assertEqual(1,len(self.tokens(session,'C')));self.assertIn('Ape',session.engine._effective_card_data(self.tokens(session,'C')[0])['type_line'])
                self.finish(session,private)
