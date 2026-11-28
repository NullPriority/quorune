"""CR608.2h/120: dealer information differs from the source owning a trigger."""
from types import SimpleNamespace
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.event_damage_source_model import (EventDamageSourceReference, EventDamageSourceInformation,
    EVENT_DAMAGE_SOURCE_CAPABILITY,EVENT_DAMAGE_SOURCE_CONTEXT)
from quorune.rules.event_damage_sources import (event_damage_source_node_capabilities,event_damage_source_runtime_dependencies,
    bind_event_damage_source,event_damage_departure_updates)
from quorune.event_counter_reference import event_object_identity
from quorune.damage_source import DamageSourceSnapshot
from quorune.model import CardInstance
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients
import test_received_damage_events as receiving

PANTHER = "Black Panther, Most Dangerous"
RAPTORS = "Wrathful Raptors"
LONER = "Ill-Tempered Loner // Howlpack Avenger"
NEMESIS = "Screaming Nemesis"

ANY_TARGET={'zones':['player','battlefield'],'categories':['player','permanent'],'predicate':'damageable','count':1}
NAMES=['Extra Arms',"Hawkeye's Bow",'Aether Charge','Pain for All','Blazing Sunsteel']


class EventDamageSourceModelTests(unittest.TestCase):
    def test_closed_event_dealer_reference_requires_exact_version_and_single_proved_event(self):
        value=EventDamageSourceReference('creature.enter').to_dict()
        self.assertEqual(value,EventDamageSourceReference.from_dict(value).to_dict())
        for change in [{'schema_version':True},{'schema_version':2},{'event':'spell.cast'},{'kind':'attachment'},{'reference':'arbitrary'}]:
            with self.assertRaises(ValueError):EventDamageSourceReference.from_dict({**value,**change})

    def test_dealer_dependency_is_derived_from_closed_payload_and_matches_its_program_event(self):
        effect={'op':'damage','source':EventDamageSourceReference('creature.enter').to_dict(),'target':'$target.0','amount':2}
        self.assertIn(EVENT_DAMAGE_SOURCE_CAPABILITY,event_damage_source_node_capabilities(effects=(effect,),target_schema=ANY_TARGET,mechanic_ids=('cr-120-damage','cr-115-targets')))
        program=SimpleNamespace(effects=(effect,),active_zone='battlefield',event='creature.enter',event_condition={},target_schema=ANY_TARGET)
        self.assertIn(EVENT_DAMAGE_SOURCE_CAPABILITY,event_damage_source_runtime_dependencies(program))
        program.event='permanent.tap'
        with self.assertRaises(ValueError):event_damage_source_runtime_dependencies(program)

    def test_dealer_effect_rejects_nonpositive_boolean_and_unrepresented_result_fields(self):
        effect={'op':'damage','source':EventDamageSourceReference('permanent.tap').to_dict(),'target':'$target.0','amount':1}
        for change in [{'amount':True},{'amount':0},{'amount':-1},{'extra':'unrepresented'}]:
            self.assertFalse(event_damage_source_node_capabilities(effects=({**effect,**change},),target_schema=ANY_TARGET,mechanic_ids=('cr-120-damage',)))

    def test_single_received_event_identity_uses_the_recipient_not_an_outgoing_source(self):
        context={'card':'recipient-ref','target_kind':'permanent','target_logical_object_id':'recipient@2','source_logical_object_id':'other@8'}
        self.assertEqual({'reference':'recipient-ref','logical_object_id':'recipient@2'},event_object_identity('damage.received',context))
        context['target_kind']='player'
        with self.assertRaises(ValueError):event_object_identity('damage.received',context)

    def test_existing_self_source_binding_keeps_its_previous_compiler_owner(self):
        from quorune.compiler.event_damage_source_templates import event_damage_source_template
        binding=SimpleNamespace(event=SimpleNamespace(value='damage.received'),active_zone='battlefield',
            explicit_capabilities=(),variant='source_dealt_damage')
        with patch('quorune.compiler.event_damage_source_templates.trigger_scalar_effect_amount_template',side_effect=AssertionError('Self binding owner was replaced')):
            self.assertIsNone(event_damage_source_template(binding,'it deals that much damage to any target.',card_name='Generic Creature',effect_template=lambda *a,**kw:None))

    def received_fixture(self):
        card=CardInstance('dealer','dealer-ref','dealer-oracle','Generic Dealer','C','B','battlefield',zone_change_counter=1)
        snapshot=DamageSourceSnapshot(card.ref,card.object_id,card.logical_object_id,'B','C',zone='battlefield',
            types=('creature',),colors=('G',),keywords=('lifelink',))
        info=EventDamageSourceInformation('damage.received',snapshot,event_id='transaction-1',received_amount=3)
        context={'event':'damage.received','event_id':'transaction-1','amount':3,'card':card.ref,
            'target_kind':'permanent','target_object_id':card.object_id,'target_logical_object_id':card.logical_object_id,
            EVENT_DAMAGE_SOURCE_CONTEXT:info.to_dict()}
        effect={'op':'damage','source':EventDamageSourceReference('damage.received').to_dict(),'target':'$target.0','amount':1}
        item=SimpleNamespace(ref='ability-frame',source_object_id='equipment-source',controller='A',context=context)
        host=SimpleNamespace(state=SimpleNamespace(cards={card.object_id:card}))
        return host,card,snapshot,info,effect,item

    def test_received_information_seals_amount_event_and_incarnation_without_changing_ability_source(self):
        host,card,snapshot,info,effect,item=self.received_fixture()
        self.assertEqual(info,EventDamageSourceInformation.from_dict(info.to_dict()))
        for change in [{'amount':4},{'amount':True},{'event_id':'other-transaction'},
                {'target_logical_object_id':'dealer@2'}]:
            altered=SimpleNamespace(context={**item.context,**change})
            with self.assertRaises(ValueError):bind_event_damage_source(host,effect,altered)
        for change in [{'schema_version':True},{'received_amount':0},{'event_id':''}]:
            with self.assertRaises(ValueError):EventDamageSourceInformation.from_dict({**info.to_dict(),**change})

    def test_current_control_then_immediate_departure_lki_does_not_rebind_returned_dealer(self):
        host,card,snapshot,info,effect,item=self.received_fixture()
        def current(_host,source,**_kw):
            return replace(snapshot,controller=source.controller,logical_object_id=source.logical_object_id)
        with patch('quorune.rules.stack_damage_sources._public_source_snapshot',side_effect=current):
            card.controller='D';bound=bind_event_damage_source(host,effect,item)
            self.assertEqual('D',bound['source_snapshot']['controller']);self.assertEqual('A',item.controller)
            self.assertEqual('equipment-source',item.source_object_id)
            updates=event_damage_departure_updates(host,[{'ref':item.ref,'context':item.context}],{(card.object_id,card.logical_object_id):card})
            item.context[EVENT_DAMAGE_SOURCE_CONTEXT]=updates[item.ref]
            card.zone_change_counter+=1;card.controller='B'
            bound=bind_event_damage_source(host,effect,item)
            self.assertEqual('D',bound['source_snapshot']['controller'])
            self.assertEqual(snapshot.logical_object_id,bound['source_snapshot']['logical_object_id'])

    def test_missing_immediate_departure_refresh_mutant_is_killed_by_last_controller(self):
        from quorune.rules import event_damage_sources as owner
        def witness():
            host,card,snapshot,info,effect,item=self.received_fixture();card.controller='D'
            with patch('quorune.rules.stack_damage_sources._public_source_snapshot',
                    side_effect=lambda _h,c:replace(snapshot,controller=c.controller)):
                updates=owner.event_damage_departure_updates(host,[{'ref':item.ref,'context':item.context}],
                    {(card.object_id,card.logical_object_id):card})
            if item.ref in updates:item.context[EVENT_DAMAGE_SOURCE_CONTEXT]=updates[item.ref]
            card.zone_change_counter+=1;card.controller='B'
            self.assertEqual('D',owner.bind_event_damage_source(host,effect,item)['source_snapshot']['controller'])
        witness()
        with patch.object(owner,'event_damage_departure_updates',return_value={}),self.assertRaises(AssertionError):witness()


    def test_nemesis_game_permission_codec_freezes_only_nonempty_canonical_players(self):
        from quorune.life_gain_permission import ResolvedLifeGainProhibition

        rule = ResolvedLifeGainProhibition.for_dealt_damage("LG1", "stack1", 4, "A", ("A", "C"))
        wire = rule.to_dict()
        self.assertEqual((5, "until_end_of_game", "damage_recipients", ["A", "C"]),
                         (wire["schema_version"], wire["duration"], wire["affected_player_relation"], wire["denied_players"]))
        self.assertFalse(rule.denies_loss)
        self.assertIsNone(rule.expires_on_next_turn_of)
        self.assertEqual(rule, ResolvedLifeGainProhibition.from_dict(wire))
        for update in ({"schema_version": True}, {"schema_version": 4}, {"duration": "until_end_of_turn"},
                       {"affected_player_relation": "any"}, {"denied_players": []},
                       {"denied_players": ["C", "A"]}, {"denied_players": ["A", "A"]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                ResolvedLifeGainProhibition.from_dict({**wire, **update})


class EventDamageSourceCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'event-damage-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/event-damage-source-cards.json',
            ROOT/'tests/fixtures/targeted-receiving-damage-cards.json'],path);cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_all_originals_have_complete_normal_admission_and_source_spans(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        semantics=SemanticRegistry();cards=[self.db.lookup(n,fuzzy=False) for n in NAMES]
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(name=card.name):
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                for face in ir.faces:
                    for node in face.nodes:self.assertEqual(node.text,card.oracle_text[node.span.start:node.span.end])
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],(card.name,bound['blockers']));self.assertEqual('capability_closed',bound['trust_basis'])

    def test_unproved_dealers_wrong_quantities_and_multiple_subscriptions_remain_residual(self):
        from test_qualified_zone_event_queries import query_record
        for text in ['Whenever you draw a card, it deals 2 damage to any target.',
                'Whenever a creature attacks, it deals that much damage to any target.',
                'Whenever enchanted creature attacks or dies, it deals 2 damage to any target.',
                'Whenever enchanted creature attacks, it deals 2 damage to any target and draw a card.']:
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=self.registry,capability_profile='commander_review').status)

    def test_existing_self_reflectors_keep_old_source_references_without_new_dealer_authority(self):
        from quorune.carddb import CardDatabase
        from quorune.compiler.ir_model import OracleNode
        from test_qualified_zone_event_queries import query_record
        record=query_record('Whenever this creature is dealt damage, it deals that much damage to any target.')
        ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertTrue(all(EVENT_DAMAGE_SOURCE_CAPABILITY not in n.capability_dependencies for f in ir.faces for n in f.nodes))

    def test_verified_event_dealer_withdrawal_blocks_new_originals_and_keeps_self_reflection(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_qualified_zone_event_queries import query_record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id']==EVENT_DAMAGE_SOURCE_CAPABILITY).update(status='blocked',blockers=['sealed dealer source owner withdrawn'])
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)
        old=query_record('Whenever this creature is dealt damage, it deals that much damage to any target.')
        self.assertEqual('exact',compile_oracle_card(old,capability_registry=registry,capability_profile='commander_review').status)


    def test_targeted_receiving_requires_its_exact_event_amount_and_recipient_scope(self):
        from quorune.card_programs import bind_semantic_program_runtime
        from test_controller_creature_keyword_absence import register_card

        for name, before, after in (
            (PANTHER, "any other target", "any other target creature"),
            (RAPTORS, "isn't a Dinosaur", "isn't a Beast"),
        ):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = (row for row in semantics.programs_for_oracle(card.oracle_id)
                            if row.event == "damage.received")
                injury = program.effects[0]
                for changed in (
                    replace(program, event="combat.damage.received"),
                    replace(program, event_condition={"field": "target_kind", "op": "eq", "value": "permanent"}),
                    replace(program, target_schema=ANY_TARGET),
                    replace(program, effects=({**injury, "amount": 1},)),
                    replace(program, effects=({**injury, "source": "$source"},)),
                ):
                    bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
                    self.assertFalse(bound["strict"], bound)
                for oracle in (
                    card.oracle_text.replace(before, after),
                    card.oracle_text.replace("that much", "twice that much"),
                    card.oracle_text.replace(" is dealt damage,", " is dealt damage or dies,"),
                ):
                    result = compile_oracle_card(replace(card, oracle_text=oracle), capability_registry=self.registry)
                    self.assertNotEqual("exact", result.status, oracle)


    def test_loner_admits_both_priced_activation_faces_and_rejects_a_broadened_damage_scope(self):
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.semantic_runtime.activated_abilities import activated_abilities_from_descriptors
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup(LONER, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, LONER)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        activations = {program.provenance["face_id"]: program for program in programs if program.event == "activate"}
        self.assertEqual({"Ill-Tempered Loner", "Howlpack Avenger"}, set(activations))
        front, back = (activations[face] for face in ("Ill-Tempered Loner", "Howlpack Avenger"))
        self.assertEqual("ability:ab2", front.ability_id)
        self.assertEqual("ability:ab2:face:Howlpack Avenger", back.ability_id)
        self.assertNotEqual(front.key, back.key)
        for program in (front, back):
            ability, = activated_abilities_from_descriptors(program.handlers)
            self.assertEqual(("ab2", program.key), (ability.ability_id, ability.builtin_semantic_key))

        avenger, = (program for program in programs if program.event == "damage.received"
                    and program.provenance["face_id"] == "Howlpack Avenger")
        effect, = avenger.effects
        self.assertEqual("$source", effect["source"])
        for changed in (
            replace(avenger, event_condition=None),
            replace(avenger, effects=({**effect, "amount": 1},)),
            replace(avenger, effects=({**effect, "source": EventDamageSourceReference("damage.received").to_dict()},)),
            replace(avenger, target_schema={**avenger.target_schema, "source_exclusion": True}),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"], bound)
        for before, after in (
            ("a permanent you control", "an opponent's permanent"),
            ("this creature deals that much", "it deals that much"),
            ("that much damage", "twice that much damage"),
        ):
            back_text = card.faces[1]["oracle_text"].replace(before, after)
            modified = replace(
                card, oracle_text=card.oracle_text.replace(card.faces[1]["oracle_text"], back_text),
                faces=(card.faces[0], {**card.faces[1], "oracle_text": back_text}),
            )
            self.assertNotEqual("exact", compile_oracle_card(modified, capability_registry=self.registry).status)

    def test_nemesis_life_rider_rejects_changed_source_duration_and_dependency(self):
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.life_gain_permission import (
            DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_CAPABILITY, DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD,
        )
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup(NEMESIS, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NEMESIS)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = (row for row in semantics.programs_for_oracle(card.oracle_id) if row.event == "damage.received")
        effect, = program.effects
        self.assertIn(DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_CAPABILITY, program.capability_dependencies)
        for changed in (
            replace(program, effects=({key: value for key, value in effect.items()
                                       if key != DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD},)),
            replace(program, effects=({**effect, DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD: True},)),
            replace(program, provenance={**program.provenance, "template_id": "fixed-typed-effect-targeted-receiving-damage-v1"}),
            replace(program, target_schema=ANY_TARGET),
            replace(program, event="combat.damage.received"),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"], bound)
        for before, after in (
            ("for the rest of the game", "this turn"),
            ("If a player", "If an opponent"),
            ("damage this way", "combat damage this way"),
            ("any other target.", "any other target creature."),
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status, changed.oracle_text)
        document = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in document["capabilities"] if row["id"] == DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_CAPABILITY)
        row.update(status="blocked", blockers=["Fixture: game-duration life authority withdrawn"])
        registry = CapabilityRegistry(document)
        proof_index = build_index(
            registry_value=document,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(proof_index, registry=registry))
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any(DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_CAPABILITY in blocker
                            for residual in ir.material_residuals for blocker in residual.blockers))
        control = self.db.lookup(PANTHER, fuzzy=False)
        self.assertEqual("exact", compile_oracle_card(control, capability_registry=registry,
                                                     capability_profile="commander_review").status)

    def test_nemesis_missing_life_rider_lowering_is_detected_before_minting_a_program(self):
        from quorune.compiler import received_damage_bindings
        from test_controller_creature_keyword_absence import register_card

        def check():
            binding, _ = register_card(self.db, self.registry, NEMESIS)
            self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        check()
        with patch.object(received_damage_bindings, "targeted_receiving_life_prohibition_effect",
                          received_damage_bindings.targeted_receiving_damage_effect):
            with self.assertRaises(AssertionError):
                check()


class EventDamageSourceNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'event-damage-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/event-damage-source-cards.json',
            ROOT/'tests/fixtures/targeted-receiving-damage-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Event damage dealers',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def lives(self,s):return tuple(s.state.players[p].life for p in 'ABCD')

    def equip(self,s,source,creature):
        self.priority(s,'A');ability,=s.engine._activated_abilities(s.state.cards[source.object_id])
        command={'action_id':'activate:'+source.ref+':'+ability.ability_id,'targets':[creature.ref],'pay':'auto'}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(creature.object_id,s.state.cards[source.object_id].attached_to)

    def test_actual_extra_arms_uses_ability_source_for_targets_and_white_dealer_for_protection(self):
        s=self.session(1116400);source=self.add(s.engine,'Extra Arms',zone='hand');dealer=self.add(s.engine,"Healer's Hawk")
        protected_source=self.add(s.engine,'Silver Knight',seat='B');protected_dealer=self.add(s.engine,'Black Knight',seat='B')
        self.add(s.engine,'Fervor');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-extra-protection-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':4,'R':1});self.checkpoint(s)
        self.cast(s,'A',source,[dealer.ref]);self.assertIsNone(self.resolve(s));self.attack(s,[(dealer,'D')])
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        self.reject(s,'A',{'action_id':'choose','targets':[protected_source.ref]});self.target(s,'A',[protected_dealer.ref])
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[protected_dealer.object_id].zone)
        self.assertEqual(0,s.state.cards[protected_dealer.object_id].marked_damage);self.assertEqual((40,40,40,40),self.lives(s));self.finish(s,private)

    def test_actual_extra_arms_keeps_last_dealer_controller_after_control_change_blink_and_aura_loss(self):
        s=self.session(1116401);source=self.add(s.engine,'Extra Arms',zone='hand');dealer=self.add(s.engine,"Healer's Hawk",seat='C')
        s.engine.change_control(dealer.object_id,'A');donate=self.add(s.engine,'Donate',zone='hand');blink=self.add(s.engine,'Momentary Blink',seat='B',zone='hand')
        self.add(s.engine,'Fervor');self.add(s.engine,'Vedalken Orrery');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-extra-lki-C')
        for i in range(3):self.add(s.engine,'Island',ref='extra-response-island-'+str(i))
        for i in range(2):self.add(s.engine,'Generic Bound Plains',seat='B',ref='blink-response-plains-'+str(i))
        self.assert_closed(s,source);self.prepare(s,mana={'C':6,'R':1,'U':1});s.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(s)
        self.cast_for(s,'A',source,[dealer.ref],reserve={'U':1});self.assertIsNone(self.resolve(s));self.attack(s,[(dealer,'D')]);self.target(s,'A',['D'])
        old=s.state.cards[dealer.object_id].logical_object_id;self.cast(s,'A',donate,['B',dealer.ref]);self.resolve_top(s)
        self.cast(s,'B',blink,[dealer.ref]);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[dealer.object_id].logical_object_id);self.assertEqual('C',s.state.cards[dealer.object_id].controller)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((40,42,40,38),self.lives(s));self.finish(s,private)

    def test_actual_bow_keeps_old_tapped_dealer_after_equipment_moves_and_uses_current_controller(self):
        s=self.session(1116410);source=self.add(s.engine,"Hawkeye's Bow",zone='hand');dealer=self.add(s.engine,"Healer's Hawk")
        other=self.add(s.engine,'Grizzly Bears');donate=self.add(s.engine,'Donate',zone='hand');move=self.add(s.engine,'Brass Squire')
        self.add(s.engine,'Fervor');self.add(s.engine,'Vedalken Orrery');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-bow-current-C')
        for i in range(3):self.add(s.engine,'Island',ref='bow-response-island-'+str(i))
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'R':2,'U':1});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'R':1,'U':1});self.assertIsNone(self.resolve(s));self.equip(s,source,dealer)
        self.assertEqual(2,s.engine._numeric_stat(dealer.object_id,'power'));self.assertIn('reach',s.engine._combat_keywords(s.state.cards[dealer.object_id]))
        self.attack(s,[(dealer,'D')]);self.cast(s,'A',donate,['B',dealer.ref]);self.resolve_top(s)
        self.priority(s,'A');ability,=s.engine._activated_abilities(s.state.cards[move.object_id])
        result=s.act('pilot:A',{'action_id':'activate:'+move.ref+':'+ability.ability_id,'targets':[source.ref,other.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.resolve_top(s);self.assertEqual(other.object_id,s.state.cards[source.object_id].attached_to)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual((40,42,39,39),self.lives(s));self.finish(s,private)

    def test_actual_aether_charge_has_dead_zero_power_beast_deal_fixed_damage_with_last_known_lifelink(self):
        s=self.session(1116420);source=self.add(s.engine,'Aether Charge',zone='hand');dealer=self.add(s.engine,'Krakilin',zone='hand')
        self.add(s.engine,'Whip of Erebos');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-aether-zero-C')
        self.assert_closed(s,source);self.assert_closed(s,dealer);self.prepare(s,mana={'C':4,'R':1,'G':2});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'G':2});self.assertIsNone(self.resolve(s));self.priority(s,'A');offer=self.action(s,'A',dealer)
        result=s.act('pilot:A',{'action_id':offer['id'],'x':0,'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve_top(s)
        self.target(s,'A',['B']);self.assertEqual('graveyard',s.state.cards[dealer.object_id].zone);decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        self.replay(s,load=True);command={'action_id':'choose','choice':'apply'};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual((44,36,40,40),self.lives(s));self.finish(s,private)

    def test_actual_aether_optional_decline_keeps_announced_own_planeswalker_and_rejects_self_player(self):
        s=self.session(1116421);source=self.add(s.engine,'Aether Charge',zone='hand');dealer=self.add(s.engine,'Leatherback Baloth',zone='hand')
        walker=self.add(s.engine,'Jace Beleren');walker.counters['loyalty']=3
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-aether-decline-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':4,'R':1,'G':3});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'G':3});self.assertIsNone(self.resolve(s));self.cast(s,'A',dealer);self.resolve_top(s)
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        self.reject(s,'A',{'action_id':'choose','targets':['A']});self.target(s,'A',[walker.ref]);decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[walker.object_id].counters['loyalty']);self.assertEqual((40,40,40,40),self.lives(s));self.finish(s,private)

    def pain(self,seed,*,dealer_name="Healer's Hawk"):
        s=self.session(seed);source=self.add(s.engine,'Pain for All',zone='hand');dealer=self.add(s.engine,dealer_name)
        shock=self.add(s.engine,'Shock',seat='B',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-pain-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':2,'R':1,'W':2});s.state.players['B'].mana_pool.update({'R':1});self.checkpoint(s)
        self.cast_for(s,'A',source,[dealer.ref],reserve={'W':2});self.resolve_top(s);self.target(s,'A',['B']);self.assertIsNone(self.resolve(s))
        return s,source,dealer,shock,private

    def test_actual_pain_lethal_received_damage_reflects_committed_amount_after_dealer_and_aura_die(self):
        s,source,dealer,shock,private=self.pain(1116430);before=self.lives(s)
        self.cast(s,'B',shock,[dealer.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[dealer.object_id].zone);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((before[0]+6,before[1]-2,before[2]-2,before[3]-2),self.lives(s));self.finish(s,private)

    def test_actual_pain_uses_received_amount_after_prevention_and_full_prevention_does_not_trigger(self):
        s=self.session(1116431);source=self.add(s.engine,'Pain for All',zone='hand');dealer=self.add(s.engine,'Vampire Nighthawk')
        healer=self.add(s.engine,'Samite Healer');shield=self.add(s.engine,'Mending Hands',zone='hand');self.add(s.engine,'Fervor')
        first=self.add(s.engine,'Shock',seat='B',zone='hand',ref='first-pain-shock');second=self.add(s.engine,'Shock',seat='B',zone='hand',ref='second-pain-shock')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-pain-prevention-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':2,'R':1,'W':1});s.state.players['B'].mana_pool.update({'R':2});self.checkpoint(s)
        self.cast_for(s,'A',source,[dealer.ref],reserve={'W':1});self.resolve_top(s);self.target(s,'A',['B']);self.assertIsNone(self.resolve(s));before=self.lives(s)
        self.priority(s,'A');ability,=s.engine._activated_abilities(s.state.cards[healer.object_id])
        result=s.act('pilot:A',{'action_id':'activate:'+healer.ref+':'+ability.ability_id,'targets':[dealer.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.cast(s,'B',first,[dealer.ref]);self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[dealer.object_id].marked_damage)
        self.assertEqual((before[0]+3,before[1]-1,before[2]-1,before[3]-1),self.lives(s))
        before=self.lives(s);self.cast(s,'A',shield,[dealer.ref]);self.assertIsNone(self.resolve(s));self.cast(s,'B',second,[dealer.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(before,self.lives(s));self.assertEqual(1,s.state.cards[dealer.object_id].marked_damage);self.finish(s,private)

    def test_actual_sunsteel_target_protection_uses_equipment_and_damage_prevention_uses_dead_creature(self):
        s=self.session(1116440);source=self.add(s.engine,'Blazing Sunsteel',zone='hand');dealer=self.add(s.engine,"Healer's Hawk")
        illegal=self.add(s.engine,'Silver Knight',seat='B');target=self.add(s.engine,'Black Knight',seat='B');shock=self.add(s.engine,'Shock',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-sunsteel-protection-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':5,'R':1});s.state.players['B'].mana_pool.update({'R':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.equip(s,source,dealer);self.assertEqual(4,s.engine._numeric_stat(dealer.object_id,'power'))
        self.cast(s,'B',shock,[dealer.ref]);self.resolve_top(s)
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        self.reject(s,'A',{'action_id':'choose','targets':[illegal.ref]});self.target(s,'A',[target.ref]);self.assertEqual('graveyard',s.state.cards[dealer.object_id].zone)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[target.object_id].zone)
        self.assertEqual(0,s.state.cards[target.object_id].marked_damage);self.assertEqual((40,40,40,40),self.lives(s));self.finish(s,private)

    def test_actual_sunsteel_source_departure_keeps_dealer_reflection_and_lifelink(self):
        s=self.session(1116441);source=self.add(s.engine,'Blazing Sunsteel',zone='hand');dealer=self.add(s.engine,"Healer's Hawk")
        shock=self.add(s.engine,'Shock',seat='B',zone='hand');destroy=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-sunsteel-departure-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':5,'R':1});s.state.players['B'].mana_pool.update({'R':1,'C':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.equip(s,source,dealer)
        self.cast(s,'B',shock,[dealer.ref]);self.resolve_top(s);self.target(s,'A',['D'])
        self.cast(s,'B',destroy,[source.ref]);self.resolve_top(s);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual((42,40,40,38),self.lives(s));self.finish(s,private)

    def test_referenced_dealer_authority_is_required_independently_of_payload_annotations(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(1116450);source=self.add(s.engine,'Extra Arms');self.assert_closed(s,source)
        programs=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if EVENT_DAMAGE_SOURCE_CAPABILITY in p.capability_dependencies];self.assertTrue(programs)
        for program in programs:
            deps=tuple(c for c in program.capability_dependencies if c!=EVENT_DAMAGE_SOURCE_CAPABILITY)
            changed=replace(program,capability_dependencies=deps,coverage=(),capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+EVENT_DAMAGE_SOURCE_CAPABILITY,bound['blockers'])

    def test_post_received_departure_fault_rolls_back_damage_trigger_and_zone_information_then_retries(self):
        from quorune.rules import event_damage_sources as owner
        from quorune.errors import GameRuleError
        s,source,dealer,shock,private=self.pain(1116451);before=self.lives(s);self.cast(s,'B',shock,[dealer.ref])
        for _ in range(3):
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        seat=s.pending_principals()[0].split(':',1)[1]
        with patch.object(owner,'event_damage_departure_updates',side_effect=GameRuleError('Injected dealer departure failure')):
            self.reject(s,seat,{'action_id':'pass'})
        self.assertEqual('battlefield',s.state.cards[dealer.object_id].zone);self.assertEqual(0,s.state.cards[dealer.object_id].marked_damage)
        self.assertEqual(before,self.lives(s));result=s.act('pilot:'+seat,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual((before[0]+6,before[1]-2,before[2]-2,before[3]-2),self.lives(s));self.finish(s,private)

    def _dealt_to(self, game, target):
        return [event.details["damage_event"] for event in game.state.events
                if event.code == "effect.damage" and event.details.get("target") == target]

    def test_actual_black_panther_prevention_and_retargeting_after_blink_replay(self):
        game = self.session(1116450)
        panther = self.add(game.engine, PANTHER)
        healer = self.add(game.engine, "Samite Healer")
        shield = self.add(game.engine, "Mending Hands", zone="hand")
        blink = self.add(game.engine, "Momentary Blink", zone="hand")
        swat = self.add(game.engine, "Deflecting Swat", seat="B", zone="hand")
        blast = self.add(game.engine, "Lightning Blast", seat="B", zone="hand")
        shock2 = self.add(game.engine, "Shock", seat="B", zone="hand", ref="panther-partly-prevented")
        self.add(game.engine, "Fervor")
        for card in (panther, healer, shield, blink, swat, blast, shock2):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 2})
        game.state.players["B"].mana_pool.update({"C": 5, "R": 3})
        self.checkpoint(game)

        self.cast_for(game, "A", shield, [panther.ref], reserve={"C": 1, "W": 1})
        self.resolve_top(game)
        self.cast_for(game, "B", blast, [panther.ref], reserve={"C": 2, "R": 2})
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(0, game.state.cards[panther.object_id].marked_damage)

        self.priority(game, "A")
        ability, = game.engine._activated_abilities(game.state.cards[healer.object_id])
        activation = {"action_id": f"activate:{healer.ref}:{ability.ability_id}", "targets": [panther.ref]}
        accepted = game.act("pilot:A", activation)
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        self.cast_for(game, "B", shock2, [panther.ref], reserve={"C": 2, "R": 1})
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.reject(game, "A", {"action_id": "choose", "targets": [panther.ref]})
        self.target(game, "A", ["D"])
        original = game.state.cards[panther.object_id].logical_object_id
        trigger, = game.state.stack
        self.assertEqual(1, trigger.context["amount"])
        self.cast(game, "A", blink, [panther.ref])
        self.resolve_top(game)
        current = game.state.cards[panther.object_id].logical_object_id
        self.assertNotEqual(original, current)

        self.cast(game, "B", swat, [trigger.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        result = game.act("pilot:B", {"action_id": "choose", "targets": [panther.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", self.resolve(game).kind)
        damage, = [row for row in self._dealt_to(game, panther.ref) if row["source"] == panther.ref]
        self.assertEqual((original, current, 1),
                         (damage["source_logical_object_id"], damage["target_logical_object_id"], damage["amount"]))
        self.reject(game, "A", {"action_id": "choose", "targets": [panther.ref]})
        self.target(game, "A", ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 39, 40, 40), self.lives(game))
        self.replay(game, load=True)

    def test_actual_black_panther_simultaneous_lethal_damage_has_one_lki_dealer_replay(self):
        game = self.session(1116451)
        panther = self.add(game.engine, PANTHER)
        blockers = [self.add(game.engine, "Grizzly Bears", seat="B", ref=f"panther-blocker-{i}")
                    for i in range(2)]
        self.add(game.engine, "Whip of Erebos")
        self.assert_closed(game, panther)
        self.prepare(game)
        self.checkpoint(game)
        original = game.state.cards[panther.object_id].logical_object_id
        receiving.ReceivedDamageNativeTests.multiblock(self, game, panther, blockers)
        self.assertEqual("graveyard", game.state.cards[panther.object_id].zone)
        before = self.lives(game)
        self.target(game, "A", ["D"])
        trigger, = game.state.stack
        self.assertEqual(4, trigger.context["amount"])

        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        damage, = self._dealt_to(game, "D")
        self.assertEqual((original, 4), (damage["source_logical_object_id"], damage["amount"]))
        self.assertEqual((before[0] + 4, before[1], before[2], before[3] - 4), self.lives(game))
        self.replay(game, load=True)

    receiving_items = receiving.ReceivedDamageNativeTests.receiving_items

    def test_actual_wrathful_raptors_lethal_damage_uses_each_controlled_dinosaur_and_order_replay(self):
        game = self.session(1116452)
        raptors = self.add(game.engine, RAPTORS)
        friend = self.add(game.engine, "Colossadactyl", ref="owned-dinosaur")
        opponent = self.add(game.engine, "Colossadactyl", ref="opposing-dinosaur", seat="C")
        wrath = self.add(game.engine, "Blasphemous Act", zone="hand")
        for card in (raptors, friend, opponent, wrath):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "R": 1})
        self.checkpoint(game)
        originals = {card.ref: game.state.cards[card.object_id].logical_object_id
                     for card in (raptors, friend, opponent)}
        self.cast(game, "A", wrath)
        order = self.resolve(game)
        self.assertEqual(("trigger.order", ["A"]), (order.kind, order.actors))
        for card in (raptors, friend, opponent):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        self.assertEqual(2, len(rows))
        ordering = {"action_id": "order", "triggers": [row["id"] for row in reversed(rows)]}
        self.reject(game, "C", ordering)
        self.assertTrue(game.act("pilot:A", ordering).ok)
        self.target(game, "A", ["B"])
        self.target(game, "A", ["D"])
        queued = {item.context["target"]: item.targets[0] for item in game.state.stack}
        self.assertEqual({raptors.ref, friend.ref}, set(queued))
        self.assertEqual(set("BD"), set(queued.values()))
        self.assertTrue(all(item.source_object_id == raptors.object_id and item.context["amount"] == 13
                            for item in game.state.stack))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual((40, 27, 40, 27), self.lives(game))
        for dealer, seat in queued.items():
            reflected, = self._dealt_to(game, seat)
            self.assertEqual((dealer, originals[dealer], 13),
                             (reflected["source"], reflected["source_logical_object_id"], reflected["amount"]))
        self.replay(game, load=True)

    def test_actual_wrathful_raptors_rechecks_dinosaur_exclusion_after_target_gains_subtype(self):
        game = self.session(1116453)
        raptors = self.add(game.engine, RAPTORS)
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        shifter = self.add(game.engine, "Amoeboid Changeling", seat="B")
        self.add(game.engine, "Fervor", seat="B")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        for card in (raptors, shock, shifter, target):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast(game, "B", shock, [raptors.ref])
        self.assertEqual("semantic.target", self.resolve(game).kind)
        for illegal in (raptors, shifter):
            self.reject(game, "A", {"action_id": "choose", "targets": [illegal.ref]})
        self.target(game, "A", [target.ref])
        self.priority(game, "B")
        ability, = (row for row in game.engine._activated_abilities(game.state.cards[shifter.object_id])
                    if "gains all creature types" in row.effect_text.casefold())
        result = game.act("pilot:B", {
            "action_id": f"activate:{shifter.ref}:{ability.ability_id}",
            "targets": [target.ref], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        characteristics = game.engine._effective_card_data(game.state.cards[target.object_id])
        self.assertIn("dinosaur", game.engine._type_parts(characteristics["type_line"])[1])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self._dealt_to(game, target.ref))
        self.assertEqual(0, game.state.cards[target.object_id].marked_damage)
        self.replay(game, load=True)

    def _activate_loner_pump(self, game, source, face, power):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        self.assertEqual(f"activate:{source.ref}:ab2", action["id"])
        mana = game.state.players["A"].mana_pool
        before = (mana.get("C", 0), mana.get("R", 0))
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        stack_item, = game.state.stack
        program = game.engine.semantics.get(stack_item.semantic_key)
        self.assertEqual(face, program.provenance["face_id"])
        self.assertEqual(source.object_id, stack_item.source_object_id)
        remaining = game.state.players["A"].mana_pool
        after = (remaining.get("C", 0), remaining.get("R", 0))
        self.assertEqual(2, sum(before) - sum(after))
        self.assertLess(after[1], before[1])
        self.resolve_top(game)
        self.assertEqual(power, game.engine._numeric_stat(source.object_id, "power"))

    def test_actual_loner_transforms_and_uses_each_priced_activation_and_its_face_trigger_replay(self):
        game = self.session(1116460)
        source = self.add(game.engine, LONER, zone="hand")
        night = self.add(game.engine, "Into the Night", zone="hand")
        walker = self.add(game.engine, "Jace Beleren", zone="hand")
        healer = self.add(game.engine, "Samite Healer")
        self.add(game.engine, "Fervor")
        shield = self.add(game.engine, "Mending Hands", zone="hand")
        blast = self.add(game.engine, "Lightning Blast", seat="B", zone="hand")
        front_other = self.add(game.engine, "Shock", seat="B", zone="hand", ref="loner-front-other")
        front_self = self.add(game.engine, "Shock", seat="B", zone="hand", ref="loner-front-self")
        back_other = self.add(game.engine, "Shock", seat="B", zone="hand", ref="avenger-back-other")
        for card in (source, night, walker, shield, blast, front_other, front_self, back_other):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 10, "R": 6, "U": 2, "W": 1})
        game.state.players["B"].mana_pool.update({"C": 3, "R": 4})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("day", game.state.day_night)
        identity = game.state.cards[source.object_id].logical_object_id
        self._activate_loner_pump(game, source, "Ill-Tempered Loner", 5)
        self.cast(game, "A", walker)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.cards[walker.object_id].counters["loyalty"])

        self.cast_for(game, "B", front_other, [walker.ref], reserve={"C": 3, "R": 3})
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(1, game.state.cards[walker.object_id].counters["loyalty"])
        self.cast_for(game, "B", front_self, [source.ref], reserve={"C": 3, "R": 2})
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.target(game, "A", ["C"])
        self.assertIsNone(self.resolve(game))
        reflected, = self._dealt_to(game, "C")
        self.assertEqual((identity, 2), (reflected["source_logical_object_id"], reflected["amount"]))

        self.cast(game, "A", night)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "cards": []})
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        current = game.state.cards[source.object_id]
        self.assertEqual(("night", "Howlpack Avenger", identity),
                         (game.state.day_night, current.active_face, current.logical_object_id))
        self._activate_loner_pump(game, source, "Howlpack Avenger", 8)

        self.cast(game, "A", shield, [walker.ref])
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "B", blast, [walker.ref], reserve={"R": 1})
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(1, game.state.cards[walker.object_id].counters["loyalty"])
        self.priority(game, "A")
        ability, = game.engine._activated_abilities(game.state.cards[healer.object_id])
        result = game.act("pilot:A", {"action_id": f"activate:{healer.ref}:{ability.ability_id}",
                                     "targets": [walker.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.cast(game, "B", back_other, [walker.ref])
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.target(game, "A", ["D"])
        trigger, = game.state.stack
        self.assertEqual((walker.ref, 1, source.object_id),
                         (trigger.context["target"], trigger.context["amount"], trigger.source_object_id))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        reflected, = self._dealt_to(game, "D")
        self.assertEqual((identity, 1), (reflected["source_logical_object_id"], reflected["amount"]))
        self.assertEqual("graveyard", game.state.cards[walker.object_id].zone)
        self.assertEqual((40, 40, 38, 39), self.lives(game))
        self.replay(game, load=True)

    def test_actual_howlpack_avenger_owns_both_lethal_occurrences_and_lifelink_replay(self):
        game = self.session(1116461)
        source = self.add(game.engine, LONER, zone="hand")
        friend = self.add(game.engine, "Grizzly Bears", ref="avenger-friendly-recipient")
        opponent = self.add(game.engine, "Grizzly Bears", seat="C", ref="avenger-opposing-recipient")
        wrath = self.add(game.engine, "Blasphemous Act", zone="hand")
        whip = self.add(game.engine, "Whip of Erebos")
        for card in (source, friend, opponent, wrath, whip):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 10, "R": 3})
        game.state.day_night = "night"
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual("Howlpack Avenger", current.active_face)
        identity = current.logical_object_id

        self.cast(game, "A", wrath)
        order = self.resolve(game)
        self.assertEqual(("trigger.order", ["A"]), (order.kind, order.actors))
        for dead in (source, friend, opponent):
            self.assertEqual("graveyard", game.state.cards[dead.object_id].zone)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        self.assertEqual(2, len(rows))
        action = {"action_id": "order", "triggers": [row["id"] for row in reversed(rows)]}
        self.reject(game, "C", action)
        self.assertTrue(game.act("pilot:A", action).ok)
        self.target(game, "A", ["B"])
        self.target(game, "A", ["D"])
        self.assertEqual({source.ref, friend.ref}, {item.context["target"] for item in game.state.stack})
        self.assertTrue(all(item.source_object_id == source.object_id and item.context["amount"] == 13
                            for item in game.state.stack))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((66, 27, 40, 27), self.lives(game))
        for seat in "BD":
            outgoing, = self._dealt_to(game, seat)
            self.assertEqual((source.ref, identity, 13),
                             (outgoing["source"], outgoing["source_logical_object_id"], outgoing["amount"]))
            self.assertIn("lifelink", outgoing["source_keywords"])
        self.replay(game, load=True)

    def test_actual_howlpack_avenger_reflects_ally_combat_damage_from_the_avenger_replay(self):
        game = self.session(1116462)
        source = self.add(game.engine, LONER, zone="hand")
        friend = self.add(game.engine, "Grizzly Bears", ref="combat-avenger-ally")
        blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="combat-avenger-blocker")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "R": 2})
        game.state.day_night = "night"
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        identity = game.state.cards[source.object_id].logical_object_id
        self.attack(game, [(friend, "B")])
        self.until(game, lambda session: session.state.pending_decision is not None
                   and session.state.pending_decision.kind == "combat.blockers")
        result = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: friend.ref}})
        self.assertTrue(result.ok, result.summary)
        self.target(game, "A", ["D"])
        trigger, = game.state.stack
        self.assertEqual((friend.ref, 2, True, source.object_id),
                         (trigger.context["target"], trigger.context["amount"], trigger.context["combat"], trigger.source_object_id))
        self.assertEqual("graveyard", game.state.cards[friend.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[blocker.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        outgoing, = self._dealt_to(game, "D")
        self.assertEqual((identity, 2, False),
                         (outgoing["source_logical_object_id"], outgoing["amount"], outgoing["combat"]))
        self.assertEqual((40, 40, 40, 38), self.lives(game))
        self.replay(game, load=True)

    def _damage_life_rules(self, game):
        from quorune.life_gain_permission import ResolvedLifeGainProhibition

        return tuple(rule for rule in game.state.continuous_effects if isinstance(rule, ResolvedLifeGainProhibition))

    def test_actual_nemesis_locks_only_redirected_player_after_lethal_damage_through_turn_and_replay(self):
        from quorune.damage_modifier_state import DamageModifierDuration, DamageRedirectionEffect, DamageSubject
        from quorune.semantic_runtime.life_prohibitions import collect_life_gain_permissions
        from test_zero_mana_damage_and_life_gain_permission import ZeroManaDamageAndLifeGainPermissionTests

        game = self.session(1116470)
        source = self.add(game.engine, NEMESIS)
        killer = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        denied_gain = self.add(game.engine, "Revitalize", seat="C", zone="hand", ref="nemesis-denied-later")
        allowed_gain = self.add(game.engine, "Revitalize", seat="B", zone="hand", ref="nemesis-allowed-later")
        multiplier = self.add(game.engine, "Rhox Faithmender", seat="C")
        for card in (source, killer, denied_gain, allowed_gain, multiplier):
            self.assert_closed(game, card)
        for seat in "BC":
            for index in range(2):
                self.add(game.engine, "Generic Bound Plains", seat=seat, ref=f"nemesis-mana-{seat}-{index}")
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        game.state.damage_redirections.append(DamageRedirectionEffect(
            redirection_id="nemesis-recipient-witness", source_id="fixture:recipient-witness", controller="B",
            subject=DamageSubject("B", "player", "B"), destination=DamageSubject("C", "player", "C"),
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=game.state.turn_sequence, label="Recorded recipient replacement",
        ))
        self.checkpoint(game)
        original = game.state.cards[source.object_id].logical_object_id
        self.cast(game, "B", killer, [source.ref])
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(self._damage_life_rules(game))
        self.target(game, "A", ["B"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 40, 37, 40), self.lives(game))
        outgoing, = self._dealt_to(game, "C")
        self.assertEqual((original, 3), (outgoing["source_logical_object_id"], outgoing["amount"]))
        rule, = self._damage_life_rules(game)
        self.assertEqual((("C",), "until_end_of_game", 5),
                         (rule.denied_players, rule.duration.value, rule.to_dict()["schema_version"]))
        game = self.replay(game, load=True)
        self.assertEqual((rule,), self._damage_life_rules(game))

        turn = game.state.turn_sequence
        ZeroManaDamageAndLifeGainPermissionTests._advance(
            self, game, lambda session: session.state.turn_sequence > turn and session.state.active_player == "B",
        )
        self.assertEqual((rule,), self._damage_life_rules(game))
        self.assertEqual(("C",), collect_life_gain_permissions(game.engine).denied_players)
        self.cast(game, "B", allowed_gain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["B"].life)
        previous_hand = set(game.state.players["C"].zones["hand"])
        self.cast(game, "C", denied_gain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["C"].life)
        drawn, = set(game.state.players["C"].zones["hand"]) - previous_hand
        for seat in "ABD":
            self.assertNotIn(game.state.cards[drawn].ref, str(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_nemesis_prevention_and_palm_aftermath_do_not_lock_a_player_replay(self):
        game = self.session(1116471)
        source = self.add(game.engine, NEMESIS)
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        palm = self.add(game.engine, "Deflecting Palm", seat="B", zone="hand")
        passage = self.add(game.engine, "Safe Passage", seat="B", zone="hand")
        for card in (source, shock, palm, passage):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 2, "R": 2, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", palm, reserve={"C": 2, "R": 1, "W": 1})
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        result = game.act("pilot:B", {"action_id": "choose", "source": source.ref})
        self.assertTrue(result.ok, result.summary)
        self.cast_for(game, "B", passage, reserve={"R": 1})
        self.resolve_top(game)
        self.cast(game, "B", shock, [source.ref])
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.reject(game, "A", {"action_id": "choose", "targets": [source.ref]})
        self.target(game, "A", ["B"])
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertFalse(self._damage_life_rules(game))
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        choice, = (option for option in options if "deflecting palm" in str(option).casefold())
        command = {"action_id": "choose", "replacement": choice["id"]}
        self.reject(game, "C", command)
        self.assertTrue(game.act("pilot:B", command).ok)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((38, 40, 40, 40), self.lives(game))
        self.assertFalse(self._damage_life_rules(game))
        self.assertFalse(self._dealt_to(game, "B"))
        self.replay(game, load=True)

    def test_actual_nemesis_damage_redirected_to_palisade_does_not_lock_its_controller(self):
        game = self.session(1116472)
        source = self.add(game.engine, NEMESIS)
        giant = self.add(game.engine, "Palisade Giant", seat="B")
        bolt = self.add(game.engine, "Shock", seat="B", zone="hand")
        for card in (source, giant, bolt):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast(game, "B", bolt, [source.ref])
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.target(game, "A", ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[giant.object_id].marked_damage)
        self.assertEqual((40, 40, 40, 40), self.lives(game))
        self.assertFalse(self._damage_life_rules(game))
        self.replay(game, load=True)
