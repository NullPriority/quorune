"""CR106.6/605.3b: recipient-owned mana retains its actual spending restriction."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_granted_static_characteristics as native
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.granted_activation_model import GrantedActivatedAbilitySpec,RESTRICTED_MANA_GRANT_CAPABILITY as CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


RESTRICTION='mana-restriction-v1|spell.types_any=instant,sorcery'
INNER='{T}: Add one mana of any color. Spend this mana only to cast an instant or sorcery spell.'


class RestrictedManaGrantModelTests(unittest.TestCase):
    def test_versioned_restrictions_preserve_legacy_payloads_and_reject_extra_results(self):
        legacy=GrantedActivatedAbilitySpec('ability:granted:front:n1','fixture:ability','{T}','Add {U}.',
            tap_source=True,mana_ability=True,fixed_mana_outputs=((('U',1),),))
        self.assertEqual(legacy,GrantedActivatedAbilitySpec.from_dict(legacy.to_dict()))
        old=replace(legacy,schema_version=2,mana_spend_restriction='nonartifact_spell_prohibited')
        self.assertEqual(old,GrantedActivatedAbilitySpec.from_dict(old.to_dict()))
        current=replace(legacy,schema_version=4,mana_spend_restriction=RESTRICTION)
        self.assertEqual(CAPABILITY,current.capability_id)
        self.assertEqual(current,GrantedActivatedAbilitySpec.from_dict(current.to_dict()))
        for changes in ({'schema_version':2},{'schema_version':True},{'mana_spend_restriction':'legendary_spell_uncounterable'},
                {'mana_spend_restriction':None},{'mana_spend_restriction':'anything'},
                {'mana_ability':False},{'fixed_mana_outputs':[]},{'crew_threshold':2}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                GrantedActivatedAbilitySpec.from_dict({**current.to_dict(),**changes})
        from quorune.rules.keyword_runtime_requirements import _fragment_descriptor_requirements
        self.assertIn(CAPABILITY,_fragment_descriptor_requirements(ability_fragment_to_dict(current)))

    def test_land_ally_overlap_is_one_recipient_and_controller_scope_covers_both_branches(self):
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
        from quorune.object_query import ObjectQueryResult,query_objects
        relation,query,excluded=fixed_characteristic_battlefield_query_subject('Each land and Ally you control')
        self.assertEqual(('source_controller',False),(relation,excluded))
        query=replace(query,controller='A')
        row=ObjectQueryResult('both','both','Generic land Ally','A','A','battlefield',types=('land','creature'),subtypes=('ally',))
        opponent=replace(row,object_id='opponent',ref='opponent',controller='B')
        self.assertEqual((row,),query_objects((row,opponent),query))


class RestrictedManaGrantCompilerTests(unittest.TestCase):
    def test_complete_restricted_grants_and_live_land_subtype_union_queries(self):
        registry=load_default_capability_registry()
        for text in ('Artifacts you control have "'+INNER+'"',
                'Lands you control have "{T}: Add two mana of any one color. Spend this mana only to cast instant and sorcery spells."',
                'Other Caves you control have "{T}, Pay 1 life: Add one mana of any color."',
                'Each land and Ally you control has "{T}: Add one mana of any color."'):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                outer,child=ir.faces[0].nodes
                self.assertEqual('granted_mana_ability',child.kind)
                fragment=outer.handlers[0]['modifier']['add_ability_fragments'][0]['value']
                if 'Spend' in text:
                    self.assertEqual(4,fragment['schema_version']);self.assertEqual(RESTRICTION,fragment['mana_spend_restriction'])
                    self.assertIn(CAPABILITY,outer.capability_dependencies);self.assertIn(CAPABILITY,child.capability_dependencies)
                if 'Caves' in text:
                    condition=outer.handlers[0]['condition']
                    self.assertEqual(['land'],condition['predicate']['types_all'])
                    self.assertEqual(['cave'],condition['predicate']['subtypes_all']);self.assertTrue(condition['exclude_source'])
                if 'Ally' in text:self.assertEqual(2,len(outer.handlers[0]['condition']['predicate']['any_of']))
        for text in ('Artifacts you control have "'+INNER.replace('instant or sorcery','chosen')+'"',
                'Artifacts you control have "{T}: Add {U}. Spend this mana only to cast a legendary spell, and that spell can\'t be countered."',
                'Other NotARealLandTypes you control have "{T}: Add {U}."',
                'Each land and Ally your teammate controls has "{T}: Add {U}."'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status)

    def test_verified_restriction_and_inner_mana_owner_withdrawal_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner in (CAPABILITY,'mana.activated.restricted_fixed_output','continuous.ability.fixed_query_grant','variant.monarch.designate'):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==owner).update(status='blocked',blockers=['granted mana owner withdrawn'])
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(query_record('Artifacts you control have "'+INNER+'"'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(owner=='variant.monarch.designate',ir.status=='exact',owner)


class RestrictedManaGrantNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.GrantedStaticCharacteristicNativeTests.session
    add=native.GrantedStaticCharacteristicNativeTests.add
    prepare=native.GrantedStaticCharacteristicNativeTests.prepare
    cast=native.GrantedStaticCharacteristicNativeTests.cast
    cast_for=native.GrantedStaticCharacteristicNativeTests.cast_for
    cast_reserved=native.GrantedStaticCharacteristicNativeTests.cast_reserved
    checkpoint=native.GrantedStaticCharacteristicNativeTests.checkpoint
    resolve=native.GrantedStaticCharacteristicNativeTests.resolve
    replay=native.GrantedStaticCharacteristicNativeTests.replay
    assert_closed=native.GrantedStaticCharacteristicNativeTests.assert_closed
    priority=native.GrantedStaticCharacteristicNativeTests.priority
    action=native.GrantedStaticCharacteristicNativeTests.action

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'restricted-grants.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/restricted-mana-grant-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mana grant witness',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def granted_action(self,session,seat,card):
        return next(row for row in session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+session.state.cards[card.object_id].ref+':ability:granted:'))

    def activate_mana(self,session,seat,card,color):
        self.priority(session,seat)
        offered=self.granted_action(session,seat,card)
        before=authoritative_state_hash(session.state)
        command={'action_id':offered['id'],'mana_output':{color:1},'pay':'auto'}
        self.assertFalse(session.act('pilot:'+('C' if seat!='C' else 'D'),command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_actual_galazeth_retains_restricted_units_after_source_departure(self):
        session=self.session(106614001)
        source=self.add(session.engine,'Galazeth Prismari')
        artifact=self.add(session.engine,'Darksteel Relic')
        self.add(session.engine,'Mountain')
        spell=self.add(session.engine,'Shock',zone='hand')
        forbidden=self.add(session.engine,'Goblin Piker',zone='hand')
        removal=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,source);self.prepare(session)
        session.state.players['B'].mana_pool['U']=1;self.checkpoint(session)
        self.activate_mana(session,'A',artifact,'R')
        self.assertFalse(session.state.stack)
        from quorune.mana_provenance import restricted_mana
        self.assertEqual(1,restricted_mana(session.state.players['A']).get(RESTRICTION,{}).get('R',0))
        self.cast(session,'B',removal,[source.ref]);self.assertIsNone(self.resolve(session))
        self.priority(session,'A');before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':'cast:'+forbidden.ref,'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual(1,restricted_mana(session.state.players['A'])[RESTRICTION]['R'])
        self.cast(session,'A',spell,['B']);self.assertIsNone(self.resolve(session))
        self.assertEqual(38,session.state.players['B'].life)
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_monument_grants_only_other_current_caves_and_charges_life(self):
        session=self.session(106614010)
        source=self.add(session.engine,'Forgotten Monument',zone='hand')
        cave=self.add(session.engine,'Hidden Cataract')
        other=self.add(session.engine,'Island')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4});self.checkpoint(session)
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='play-land:'+source.ref)
        result=session.act('pilot:A',{'action_id':offered['id']});self.assertTrue(result.ok,result.summary)
        self.assertFalse([a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if a.ability_id.startswith('ability:granted:')])
        self.assertFalse([a for a in session.engine._activated_abilities(other) if a.ability_id.startswith('ability:granted:')])
        late=self.add(session.engine,'Hidden Volcano',ref='late-cave')
        self.assertEqual(1,len([a for a in session.engine._activated_abilities(late) if a.ability_id.startswith('ability:granted:')]))
        self.checkpoint(session)
        life=session.state.players['A'].life;self.activate_mana(session,'A',cave,'G')
        self.assertEqual(life-1,session.state.players['A'].life);self.assertEqual(1,session.state.players['A'].mana_pool['G'])
        self.assertTrue(session.state.cards[cave.object_id].tapped);self.replay(session,load=True)

    def test_actual_guide_grants_land_or_ally_once_with_current_controller_scope(self):
        session=self.session(106614020)
        source=self.add(session.engine,'Great Divide Guide',zone='hand')
        land=self.add(session.engine,'Island');ally=self.add(session.engine,'Cliffside Lookout')
        opponent=self.add(session.engine,'Island',seat='B',ref='opponent-land')
        body=self.add(session.engine,'Goblin Piker')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'G':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        for card in (source,land,ally):
            abilities=[a for a in session.engine._activated_abilities(session.state.cards[card.object_id]) if a.ability_id.startswith('ability:granted:')]
            self.assertEqual(1,len(abilities),card.printed_name)
        for card in (opponent,body):
            self.assertFalse([a for a in session.engine._activated_abilities(card) if a.ability_id.startswith('ability:granted:')])
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.object_query import object_matches_query
        union=ObjectQuerySpec.from_dict(next(p.handlers[0]['condition']['predicate'] for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.handlers and p.handlers[0].get('handler_id')=='continuous.ability.fixed-query-grant.v1'))
        row=session.engine._public_object_query_result(session.state.cards[land.object_id])
        self.assertTrue(object_matches_query(replace(row,subtypes=tuple(sorted(set(row.subtypes)|{'ally'}))),union))
        self.activate_mana(session,'A',land,'W');self.assertEqual(1,session.state.players['A'].mana_pool['W'])
        self.replay(session,load=True)

    def test_runtime_restricted_grant_owner_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(106614030);source=self.add(session.engine,'Galazeth Prismari')
        checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(cap for cap in program.capability_dependencies if cap!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
            if program.provenance.get('granted_only'):
                stripped=replace(changed,handlers=tuple(d for d in changed.handlers if d.get('handler_id')!='activation.catalog.pinned.v1'))
                bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
        self.assertGreater(checked,0)

    def test_actual_lute_automatic_payment_spends_two_restricted_mana_on_a_sorcery(self):
        session=self.session(106614040)
        source=self.add(session.engine,'Resonating Lute')
        land=self.add(session.engine,'Island');land.tapped=False
        sorcery=self.add(session.engine,'Divination',zone='hand')
        creature=self.add(session.engine,'Goblin Piker',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':1});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':'cast:'+creature.ref,'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,'A',sorcery);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[land.object_id].tapped)
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_restriction_omission_mutant_is_killed_by_actual_forbidden_spending(self):
        import quorune.rules.activation.query as owner
        original=owner._typed_granted_abilities
        def mutant(data):return tuple(replace(ability,mana_spend_restriction=None) for ability in original(data))
        with patch.object(owner,'_typed_granted_abilities',side_effect=mutant),self.assertRaises(AssertionError):
            self.test_actual_galazeth_retains_restricted_units_after_source_departure()


class ExactManaPoolPaymentNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = RestrictedManaGrantNativeTests.session
    add = RestrictedManaGrantNativeTests.add
    prepare = RestrictedManaGrantNativeTests.prepare
    checkpoint = RestrictedManaGrantNativeTests.checkpoint
    assert_closed = RestrictedManaGrantNativeTests.assert_closed
    priority = RestrictedManaGrantNativeTests.priority
    cast = RestrictedManaGrantNativeTests.cast
    replay = RestrictedManaGrantNativeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "exact-mana-pool-payment.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "restricted-mana-grant-cards.json", "scryfall-exact-lists.json",
            "ability-source-counter-cards.json", "defender-declaration-numeric-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Exact mana pool payment witness",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def activate_mana(self, session, source, color, *, restriction=None):
        self.priority(session, "A")
        ability = next(ability for ability in session.engine._activated_abilities(source)
                       if ability.mana_ability and ability.mana_spend_restriction == restriction)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        offered = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        self.assertIn(action_id, {row["id"] for row in offered})
        result = session.act("pilot:A", {"action_id": action_id, "mana_output": {color: 1}})
        self.assertTrue(result.ok, result.summary)

    def resolve_top(self, session):
        ref = session.state.stack[-1].ref
        for _ in range(20):
            if not any(item.ref == ref for item in session.state.stack):
                return
            self.assertEqual("priority", session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("The original stack item did not resolve")

    def test_same_color_pool_choice_controls_whether_the_spell_can_be_countered(self):
        from jsonschema import Draft202012Validator
        from quorune import CommandEnvelope, GameService, PROTOCOL_VERSION
        from quorune.rules.mana_pool_payment import MANA_POOL_PAYMENT_FIELD, PoolManaUnit
        from quorune.mana_provenance import mana_provenance_lots

        schema = json.loads((ROOT / "schemas/decision-packet.schema.json").read_text(encoding="utf-8"))
        rider = "legendary_spell_uncounterable"
        for use_rider in (False, True):
            with self.subTest(use_rider=use_rider):
                session = self.session(10660100 + int(use_rider))
                engine = session.engine
                ordinary = self.add(engine, "Generic Bound Plains")
                halfling = self.add(engine, "Delighted Halfling")
                halfling.acquired_control_turn_count = -1
                spell = self.add(engine, "Isamaru, Hound of Konda", zone="hand")
                counter = self.add(engine, "Counterspell", seat="B", zone="hand")
                for card in (halfling, spell, counter):
                    self.assert_closed(session, card)
                self.prepare(session)
                session.state.players["B"].mana_pool["U"] = 2
                self.checkpoint(session)
                self.activate_mana(session, ordinary, "W")
                self.activate_mana(session, halfling, "W", restriction=rider)
                service = GameService(session)
                packet = service.observe("pilot:A", full=True)
                Draft202012Validator(schema).validate(packet)
                decision = packet["decision"]
                action = next(row for row in decision["legal_actions"] if row["id"] == f"cast:{spell.ref}")
                field = next(field for field in action["form"]["fields"] if field["name"] == MANA_POOL_PAYMENT_FIELD)
                ordinary_key = PoolManaUnit("W").key
                rider_key = PoolManaUnit("W", restriction=rider).key
                self.assertEqual({ordinary_key: 1, rider_key: 1},
                    {row["id"]: row["maximum"] for row in field["rows"]})
                self.assertNotIn("mana_pool_rows", session.state.pending_decision.payload_by_actor["A"])
                for other in ("pilot:B", "pilot:D"):
                    self.assertNotIn(rider_key, json.dumps(service.observe(other, full=True)))
                selected = rider_key if use_rider else ordinary_key
                def command(identifier, values):
                    return CommandEnvelope(
                        protocol_version=PROTOCOL_VERSION, game_id=session.state.game_id,
                        command_id=identifier, decision_id=decision["id"], action_id=action["id"],
                        capability=decision["cap"], expected_view_revision=packet["view_revision"],
                        choices={MANA_POOL_PAYMENT_FIELD: values},
                    )
                before = authoritative_state_hash(session.state)
                if not use_rider:
                    for index, invalid in enumerate(({ordinary_key: 2}, {ordinary_key: True}, {"pool-forged": 1})):
                        receipt = service.command(command(f"invalid-{index}", invalid), principal="pilot:A")
                        self.assertFalse(receipt.ok)
                        self.assertEqual(before, authoritative_state_hash(session.state))
                    receipt = service.command(command("foreign", {selected: 1}), principal="pilot:B")
                    self.assertFalse(receipt.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    contradictory = session.act("pilot:A", {"action_id": action["id"],
                        MANA_POOL_PAYMENT_FIELD: {selected: 1}, "payment": {"W": 1}})
                    self.assertFalse(contradictory.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                receipt = service.command(command("pay-selected", {selected: 1}), principal="pilot:A")
                self.assertTrue(receipt.ok, receipt.summary)
                item = session.state.stack[-1]
                self.assertEqual(use_rider, bool(item.context.get("cant_be_countered")))
                player = session.state.players["A"]
                self.assertEqual(1, player.mana_pool["W"])
                self.assertEqual(int(not use_rider), sum(dict(lot.bundle).get("W", 0)
                    for lot in mana_provenance_lots(player) if lot.restriction == rider))
                self.assertEqual({selected: 1}, session.commands[-1]["payload"][MANA_POOL_PAYMENT_FIELD])
                self.cast(session, "B", counter, [item.ref])
                self.resolve_top(session)
                self.assertEqual("stack" if use_rider else "graveyard", session.state.cards[spell.object_id].zone)
                self.replay(session, load=True)

    def test_exact_pool_payment_sees_mana_activated_inside_the_cast(self):
        from quorune.rules.mana_pool_payment import MANA_POOL_PAYMENT_FIELD, PoolManaUnit

        session = self.session(10660103)
        engine = session.engine
        forest = self.add(engine, "Forest")
        halfling = self.add(engine, "Delighted Halfling")
        halfling.acquired_control_turn_count = -1
        spell = self.add(engine, "Giant Growth", zone="hand")
        target = self.add(engine, "Generic Bound Body")
        self.assert_closed(session, spell)
        self.prepare(session)
        self.checkpoint(session)
        rider = "legendary_spell_uncounterable"
        self.activate_mana(session, halfling, "G", restriction=rider)
        action = {"action_id": f"cast:{spell.ref}", "targets": [target.ref],
                  "mana": [{"source": forest.ref, "bundle": {"G": 1}}]}
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:A", {**action,
            MANA_POOL_PAYMENT_FIELD: {PoolManaUnit("G", restriction=rider).key: 1}})
        self.assertFalse(denied.ok)
        self.assertIn("cannot be spent on this cost", denied.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertFalse(session.state.cards[forest.object_id].tapped)
        result = session.act("pilot:A", {**action, MANA_POOL_PAYMENT_FIELD: {PoolManaUnit("G").key: 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[forest.object_id].tapped)
        self.assertEqual(1, session.state.players["A"].mana_pool["G"])
        self.resolve_top(session)
        self.assertEqual(5, session.engine._numeric_stat(target.object_id, "power"))
        self.replay(session, load=True)


class TypedManaSpendRuleTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = RestrictedManaGrantNativeTests.session
    add = RestrictedManaGrantNativeTests.add
    prepare = RestrictedManaGrantNativeTests.prepare
    checkpoint = RestrictedManaGrantNativeTests.checkpoint
    cast = RestrictedManaGrantNativeTests.cast
    action = RestrictedManaGrantNativeTests.action
    priority = RestrictedManaGrantNativeTests.priority
    resolve = RestrictedManaGrantNativeTests.resolve
    replay = RestrictedManaGrantNativeTests.replay
    assert_closed = RestrictedManaGrantNativeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "typed-mana-spend-rules.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "typed-mana-spend-rule-cards.json",
            fixtures / "ability-source-counter-cards.json",
            fixtures / "all-blocker-requirement-cards.json",
            fixtures / "chosen-type-event-cards.json",
            fixtures / "additional-conditional-modifier-cards.json",
            fixtures / "enchanted-player-static-cards.json",
            fixtures / "noncreature-subtype-quantity-cards.json",
            fixtures / "scryfall-exact-lists.json",
            fixtures / "self-public-zone-cards.json",
            fixtures / "fixed-stack-spell-copy-cards.json",
            fixtures / "fixed-resolution-mana-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Typed mana spending", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def resolve_top(self, session):
        ref = session.state.stack[-1].ref
        for _ in range(20):
            if not any(item.ref == ref for item in session.state.stack):
                return
            self.assertEqual("priority", session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("The typed-mana witness did not resolve")

    def actions(self, session, seat="A"):
        return session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def choose_type(self, session, subtype):
        self.assertEqual("replacement.order", self.resolve(session).kind)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option = next(row["id"] for row in options if row["id"].endswith(":" + subtype))
        command = {"action_id": "choose", "choices": {"replacement": option}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def play_land(self, session, source, *, subtype=None):
        self.priority(session, "A")
        action_id = f"play-land:{source.ref}"
        self.assertIn(action_id, {row["id"] for row in self.actions(session)})
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        if subtype is not None:
            self.choose_type(session, subtype)
            self.assertEqual(subtype, session.state.cards[source.object_id].annotations["chosen_creature_type"])

    def produce(self, session, source, color, *, typed=True):
        from quorune.mana_spend_rules import is_typed_mana_spend_rule

        self.priority(session, "A")
        ability = next(ability for ability in session.engine._activated_abilities(session.state.cards[source.object_id])
            if ability.mana_ability and is_typed_mana_spend_rule(ability.mana_spend_restriction) == typed)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.assertIn(action_id, {row["id"] for row in self.actions(session)})
        result = session.act("pilot:A", {"action_id": action_id, "mana_output": {color: 1}, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    @staticmethod
    def spell_item(session, card):
        return next(item for item in session.state.stack if item.card_object_id == card.object_id)

    def test_cavern_locks_the_chosen_type_and_protects_only_restricted_spending(self):
        from quorune.mana_provenance import mana_provenance_lots
        from quorune.mana_spend_rules import decode_mana_spend_rule
        from quorune.stack_counter import stack_item_can_be_countered

        for restricted in (False, True):
            with self.subTest(restricted=restricted):
                session = self.session(10660101 + int(restricted))
                engine = session.engine
                cavern = self.add(engine, "Cavern of Souls", zone="hand")
                goblin = self.add(engine, "Goblin Piker", zone="hand")
                kindred = self.add(engine, "Tarfire", zone="hand")
                counter = self.add(engine, "Counterspell", seat="B", zone="hand")
                bounce = self.add(engine, "Boomerang", seat="B", zone="hand")
                self.assert_closed(session, cavern)
                self.prepare(session, mana={"C": 1} if restricted else {"R": 1})
                session.state.players["B"].mana_pool["U"] = 4
                self.checkpoint(session)
                self.play_land(session, cavern, subtype="goblin")
                self.produce(session, cavern, "R" if restricted else "C", typed=restricted)
                if restricted:
                    self.cast(session, "B", bounce, [cavern.ref])
                    self.resolve_top(session)
                    self.assertEqual("hand", session.state.cards[cavern.object_id].zone)
                    self.assertNotIn("chosen_creature_type", session.state.cards[cavern.object_id].annotations)
                    lot, = mana_provenance_lots(session.state.players["A"])
                    self.assertEqual("goblin", decode_mana_spend_rule(lot.restriction).chosen_type)
                    self.priority(session, "A")
                    self.assertNotIn(f"cast:{kindred.ref}", {row["id"] for row in self.actions(session)})
                    before = authoritative_state_hash(session.state)
                    rejected = session.act("pilot:A", {"action_id": f"cast:{kindred.ref}", "targets": ["D"], "pay": "auto"})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                self.cast(session, "A", goblin)
                item = self.spell_item(session, goblin)
                self.assertEqual(not restricted, stack_item_can_be_countered(engine, item))
                self.assertFalse(mana_provenance_lots(session.state.players["A"]))
                self.cast(session, "B", counter, [item.ref])
                self.resolve_top(session)
                self.assertEqual("stack" if restricted else "graveyard", session.state.cards[goblin.object_id].zone)
                self.replay(session, load=True)

    def test_selected_creature_mana_is_offered_for_changeling_casts(self):
        for index, name in enumerate(("Pillar of Origins", "Unclaimed Territory")):
            with self.subTest(source=name):
                session = self.session(10660104 + index)
                source = self.add(session.engine, name, zone="hand")
                changeling = self.add(session.engine, "Changeling Outcast", zone="hand")
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 2} if index == 0 else None)
                self.checkpoint(session)
                if index == 0:
                    self.cast(session, "A", source)
                    self.choose_type(session, "goblin")
                else:
                    self.play_land(session, source, subtype="goblin")
                self.cast(session, "A", changeling)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertEqual("stack", session.state.cards[changeling.object_id].zone)
                self.replay(session, load=True)

    def test_secluded_courtyard_pays_for_the_chosen_creature_source_ability(self):
        from quorune.mana_provenance import mana_provenance_lots

        session = self.session(10660106)
        courtyard = self.add(session.engine, "Secluded Courtyard", zone="hand")
        creature = self.add(session.engine, "Walking Ballista")
        creature.counters["+1/+1"] = 1
        self.assert_closed(session, courtyard)
        self.prepare(session, mana={"C": 3})
        self.checkpoint(session)
        self.play_land(session, courtyard, subtype="construct")
        self.produce(session, courtyard, "U")
        self.priority(session, "A")
        action = next(row for row in self.actions(session) if row["id"].startswith(f"activate:{creature.ref}:"))
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        self.assertEqual(2, session.state.cards[creature.object_id].counters["+1/+1"])
        self.assertFalse(mana_provenance_lots(session.state.players["A"]))
        self.replay(session, load=True)

    def test_ringhart_crest_preserves_its_independent_legendary_creature_branch(self):
        from quorune.mana_provenance import mana_provenance_lots
        from quorune.mana_restrictions import mana_restriction_allows, spell_mana_spend_context

        session = self.session(10660107)
        source = self.add(session.engine, "Kolvori, God of Kinship // The Ringhart Crest", zone="hand")
        other_subtype = self.add(session.engine, "Squee, the Immortal", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 1, "G": 1, "R": 2})
        self.checkpoint(session)
        face = "The Ringhart Crest"
        action_id = f"cast:{source.ref}:face:{face}"
        self.assertIn(action_id, {row["id"] for row in self.actions(session)})
        result = session.act("pilot:A", {"action_id": action_id, "face": face, "pay": "manual",
            "payment": {"C": 1, "G": 1}})
        self.assertTrue(result.ok, result.summary)
        self.choose_type(session, "elf")
        self.assertEqual("The Ringhart Crest", session.state.cards[source.object_id].active_face)
        self.produce(session, source, "G")
        lot, = mana_provenance_lots(session.state.players["A"])
        self.assertFalse(mana_restriction_allows(lot.restriction, spell_mana_spend_context("Legendary Artifact")))
        self.assertFalse(mana_restriction_allows(lot.restriction, spell_mana_spend_context("Creature — Goblin")))
        self.cast(session, "A", other_subtype)
        self.assertEqual("stack", session.state.cards[other_subtype.object_id].zone)
        self.assertFalse(mana_provenance_lots(session.state.players["A"]))
        self.replay(session, load=True)

    def test_boseiju_protects_only_an_instant_or_sorcery_that_spends_its_mana(self):
        from quorune.mana_provenance import mana_provenance_lots
        from quorune.stack_counter import stack_item_can_be_countered

        for index, target in enumerate(("Divination", "Universal Automaton", "Walking Ballista")):
            with self.subTest(target=target):
                session = self.session(10660108 + index)
                engine = session.engine
                source = self.add(engine, "Boseiju, Who Shelters All")
                use = self.add(engine, target, zone="battlefield" if index == 2 else "hand")
                counter = self.add(engine, "Counterspell", seat="C", zone="hand")
                twincast = self.add(engine, "Twincast", seat="B", zone="hand")
                copy_counter = self.add(engine, "Counterspell", seat="B", zone="hand", ref="boseiju-copy-counter")
                next_spell = self.add(engine, "Dark Ritual", zone="hand")
                if index == 2:
                    use.counters["+1/+1"] = 1
                self.assert_closed(session, source)
                available = {"C": 1, "U": 1} if index == 0 else {"C": 3, "B": 1} if index == 2 else None
                self.prepare(session, mana=available)
                session.state.players["B"].mana_pool["U"] = 4
                session.state.players["C"].mana_pool["U"] = 2
                self.checkpoint(session)
                self.produce(session, source, "C")
                self.assertEqual(38, session.state.players["A"].life)
                if index == 2:
                    self.priority(session, "A")
                    action = next(row for row in self.actions(session) if row["id"].startswith(f"activate:{use.ref}:"))
                    result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                    self.resolve_top(session)
                    self.assertFalse(mana_provenance_lots(session.state.players["A"]))
                    self.cast(session, "A", next_spell)
                    self.assertTrue(stack_item_can_be_countered(engine, self.spell_item(session, next_spell)))
                else:
                    self.cast(session, "A", use)
                    original = self.spell_item(session, use)
                    self.assertEqual(index != 0, stack_item_can_be_countered(engine, original))
                    self.cast(session, "C", counter, [original.ref])
                    self.resolve_top(session)
                    self.assertEqual("stack" if index == 0 else "graveyard", session.state.cards[use.object_id].zone)
                    if index == 0:
                        self.cast(session, "B", twincast, [original.ref])
                        self.resolve_top(session)
                        copied = next(item for item in session.state.stack if item.kind == "spell_copy")
                        self.assertTrue(stack_item_can_be_countered(engine, copied))
                        self.cast(session, "B", copy_counter, [copied.ref])
                        self.resolve_top(session)
                        self.assertFalse(any(item.ref == copied.ref for item in session.state.stack))
                    self.assertFalse(mana_provenance_lots(session.state.players["A"]))
                self.replay(session, load=True)

    def test_typed_spend_rule_and_runtime_authority_fail_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.fixed_mana_abilities import FIXED_MANA_HANDLER_ID, FIXED_MANA_SPEND_RULE_HANDLER_ID
        from quorune.mana_provenance import ManaProvenanceLot
        from quorune.mana_spend_rules import MANA_SPEND_RULE_CAPABILITY
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.semantic_runtime.mana_abilities import default_fixed_mana_ability_registry

        session = self.session(10660111)
        source = self.add(session.engine, "Cavern of Souls")
        self.assert_closed(session, source)
        program = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(desc["handler_id"] == FIXED_MANA_SPEND_RULE_HANDLER_ID for desc in program.handlers))
        descriptor = next(desc for desc in program.handlers if desc["handler_id"] == FIXED_MANA_SPEND_RULE_HANDLER_ID)
        legacy = {**descriptor, "handler_id": FIXED_MANA_HANDLER_ID, "schema_version": 1}
        with self.assertRaisesRegex(SemanticNodeError, "schema version four"):
            default_fixed_mana_ability_registry().validate(legacy)
        with self.assertRaisesRegex(ValueError, "production-bound"):
            ManaProvenanceLot.create({"R": 1}, restriction=descriptor["ability"]["spend_restriction"])
        orphaned = replace(program, handlers=[item for item in program.handlers if item is not descriptor])
        binding = bind_semantic_program_runtime(orphaned, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(any("Typed mana catalog requires" in blocker for blocker in binding["blockers"]))
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != MANA_SPEND_RULE_CAPABILITY)
        unbound = replace(program, capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(unbound, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + MANA_SPEND_RULE_CAPABILITY, binding["blockers"])
        with patch("quorune.fixed_mana_abilities.fixed_mana_spend_rule_template", return_value=None):
            ir = compile_oracle_card(self.db.lookup("Cavern of Souls"), capability_registry=self.registry,
                capability_profile="commander_review")
        self.assertTrue(ir.material_residuals)
