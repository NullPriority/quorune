"""CR506.5: a source's alone qualification uses its sealed declaration."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_qualified_attack_subjects as native
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

CAPABILITY='trigger.event.normalized_public_action'
RESULT_CAPABILITY='resolution.event_object.attack_characteristics'
TEXT='Whenever this creature attacks alone, draw a card.'


class SourceAttackAloneCompilerTests(unittest.TestCase):
    def test_source_and_alone_are_both_required_and_complete_results_are_preserved(self):
        registry=load_default_capability_registry()
        from quorune.compiler.fixed_public_event_trigger_bindings import fixed_public_event_binding_spec
        binding=fixed_public_event_binding_spec(TEXT)
        self.assertEqual({'all':[{'field':'card','op':'eq','value':'$source.ref'},
            {'field':'attacking_alone','op':'truthy','value':True}]},binding.condition)
        self.assertEqual('source_attacks',binding.variant)
        for body,operations in (('draw a card.',['draw']),('put a +1/+1 counter on this creature.',['counter']),
                ('it gets +2/+0 until end of turn.',['continuous']),('you draw a card and lose 1 life.',['draw','lose_life'])):
            ir=compile_oracle_card(query_record('Whenever this creature attacks alone, '+body),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            effects=ir.faces[0].nodes[0].effects;self.assertTrue(effects)
            self.assertEqual(len(operations),len(effects))
            if len(operations)==2:self.assertEqual(operations,[effect['op'] for effect in effects])
        for text in ('Whenever this creature is attacking alone, draw a card.',
                'Whenever this creature attacks alone you or a planeswalker you control, draw a card.',
                TEXT+' Rewrite history.','Whenever this creature attacks alone or with another creature, draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_verified_public_action_withdrawal_keeps_unrelated_draw_resolution(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner,text in ((CAPABILITY,TEXT),(RESULT_CAPABILITY,'Whenever a creature you control attacks alone, it gains lifelink until end of turn.')):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==owner).update(status='blocked',blockers=['sealed attack authority withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Sorcery'),capability_registry=registry).status)

    def test_omitting_the_alone_condition_mutant_is_killed(self):
        import quorune.compiler.fixed_public_event_trigger_bindings as owner
        original=owner._public_attack_spec
        def witness():
            binding=owner.fixed_public_event_binding_spec(TEXT)
            self.assertIn({'field':'attacking_alone','op':'truthy','value':True},binding.condition['all'])
        witness()
        def omitted(text, *, card_name=None):
            value=original(text, card_name=card_name)
            return replace(value,condition={'all':[v for v in value.condition['all'] if v.get('field')!='attacking_alone']}) if value is not None and value.variant=='source_attacks' else value
        with patch.object(owner,'_public_attack_spec',side_effect=omitted),self.assertRaises(AssertionError):witness()

    def test_existing_public_attack_pronoun_results_pin_the_attacker_and_keep_unknown_siblings_residual(self):
        registry=load_default_capability_registry()
        prefix='Whenever a creature you control attacks alone, '
        text=prefix+'it gains lifelink until end of turn.'
        ir=compile_oracle_card(query_record(text),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertEqual('$event.permanent_object',node.effects[0]['card']);self.assertIn(RESULT_CAPABILITY,node.capability_dependencies)
        explicit=compile_oracle_card(query_record(prefix+'Generic Query Observer gets +1/+1 until end of turn.'),capability_registry=registry)
        self.assertEqual('exact',explicit.status,explicit.material_residuals);self.assertNotEqual('$event.permanent_object',explicit.faces[0].nodes[0].effects[0].get('card'))
        for body in ('it gains lifelink.','it gains an unrepresented ability until end of turn.','it gains lifelink until end of turn. Rewrite history.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(prefix+body),capability_registry=registry).status)

    def test_attached_attack_pronoun_cannot_fall_back_to_the_aura_source(self):
        record = replace(query_record(
            'Enchant creature\nWhenever enchanted creature attacks, it gets +X/+0 until end of turn, '
            'where X is your devotion to red.',
        ), type_line='Enchantment — Aura', keywords=('Enchant',), power=None, toughness=None)
        registry = load_default_capability_registry()

        def witness():
            ir = compile_oracle_card(record, capability_registry=registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            trigger, = (node for node in ir.faces[0].nodes if node.event == 'creature.attacks')
            self.assertEqual('$event.permanent_object', trigger.effects[0]['card'])
            self.assertIn(RESULT_CAPABILITY, trigger.capability_dependencies)

        witness()
        with patch('quorune.compiler.attack_event_results.qualified_attack_characteristic_result', return_value=None):
            with self.assertRaises(AssertionError):
                witness()


class SourceAttackAloneNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.QualifiedAttackNativeTests.session
    add=native.QualifiedAttackNativeTests.add
    prepare=native.QualifiedAttackNativeTests.prepare
    cast=native.QualifiedAttackNativeTests.cast
    cast_for=native.QualifiedAttackNativeTests.cast_for
    action=native.QualifiedAttackNativeTests.action
    priority=native.QualifiedAttackNativeTests.priority
    checkpoint=native.QualifiedAttackNativeTests.checkpoint
    resolve=native.QualifiedAttackNativeTests.resolve
    replay=native.QualifiedAttackNativeTests.replay
    assert_closed=native.QualifiedAttackNativeTests.assert_closed
    until=native.QualifiedAttackNativeTests.until
    attack=native.QualifiedAttackNativeTests.attack
    trigger_count=native.QualifiedAttackNativeTests.trigger_count

    def resolve_top(self,session):
        ref=session.state.stack[-1].ref
        for _ in range(20):
            if not any(i.ref==ref for i in session.state.stack):return
            self.assertEqual('priority',session.state.pending_decision.kind)
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Source-alone response did not resolve')

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-alone.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/source-attack-alone-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source alone',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_actual_rogue_kavu_requires_that_source_and_one_declared_attacker(self):
        for mode in ('alone','two','other'):
            with self.subTest(mode=mode):
                session=self.session(506639001+len(mode));source=self.add(session.engine,'Rogue Kavu',zone='hand');other=self.add(session.engine,'Grizzly Bears')
                self.add(session.engine,'Fervor');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
                self.assert_closed(session,source);self.prepare(session,mana={'C':1,'R':1});self.checkpoint(session);self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
                self.attack(session,[(source,'B')] if mode=='alone' else [(source,'B'),(other,'C')] if mode=='two' else [(other,'C')])
                self.assertEqual(1 if mode=='alone' else 0,self.trigger_count(session,source))
                if mode=='two':self.cast(session,'B',bounce,[other.ref])
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual(3 if mode=='alone' else 1,session.engine._numeric_stat(source.object_id,'power'));self.replay(session,load=True)

    def test_actual_voltaic_whip_draws_for_recipient_controller_after_provider_departure(self):
        session=self.session(506639010);source=self.add(session.engine,'Voltaic Whip',seat='B');body=self.add(session.engine,'Grizzly Bears',seat='B')
        theft=self.add(session.engine,'Act of Treason',zone='hand');destroy=self.add(session.engine,'Disenchant',seat='B',zone='hand')
        self.add(session.engine,'Plains',seat='B');self.add(session.engine,'Wastes',seat='B');self.assert_closed(session,source);self.assert_closed(session,theft)
        attach_objects(session.state.cards,source,body,source_timestamp=source.zone_timestamp,players=session.state.players)
        private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        self.prepare(session,mana={'C':2,'R':1});self.checkpoint(session);self.cast(session,'A',theft,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('A',session.state.cards[body.object_id].controller);self.attack(session,[(body,'C')])
        self.assertEqual(1,self.trigger_count(session,body));hand={s:len(session.state.players[s].zones['hand']) for s in 'AB'};life={s:session.state.players[s].life for s in 'AB'}
        self.cast(session,'B',destroy,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual(hand['A']+1,len(session.state.players['A'].zones['hand']));self.assertEqual(life['A']-1,session.state.players['A'].life)
        self.assertEqual(hand['B']-1,len(session.state.players['B'].zones['hand']));self.assertEqual(life['B'],session.state.players['B'].life)
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_queued_alone_result_survives_another_attacking_creature_entering(self):
        session=self.session(506639020);source=self.add(session.engine,'Rogue Kavu',zone='hand');other=self.add(session.engine,'Grizzly Bears')
        self.add(session.engine,'Fervor');self.assert_closed(session,source);self.prepare(session,mana={'C':1,'R':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.attack(session,[(source,'B')]);self.assertEqual(1,self.trigger_count(session,source))
        created=session.engine.create_token('A',name='Attacking copy',copy_of=other.ref,tapped=True,attacking='C',reason='Canonical owner interaction after a sealed sole declaration')
        self.assertEqual(1,len(created));self.assertEqual(2,len(session.state.combat.attackers));self.assertEqual(1,self.trigger_count(session,source))
        # Replay begins from this prepared owner-interaction state; token entry
        # is not represented as a newly supported card or player action.
        self.checkpoint(session);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(3,session.engine._numeric_stat(source.object_id,'power'));self.replay(session,load=True)

    def test_actual_battlegrace_results_reach_the_attacker_and_never_its_returned_incarnation(self):
        for blink in (False,True):
            with self.subTest(blink=blink):
                session=self.session(506639030+blink);source=self.add(session.engine,'Battlegrace Angel');body=self.add(session.engine,'Grizzly Bears')
                self.add(session.engine,'Fervor');self.add(session.engine,'Vedalken Orrery');self.add(session.engine,'Forest');self.add(session.engine,'Wastes')
                bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.assert_closed(session,source)
                self.prepare(session);self.checkpoint(session);self.attack(session,[(body,'B')]);self.assertEqual(2,self.trigger_count(session,source))
                if blink:
                    old=session.state.cards[body.object_id].logical_object_id;self.cast(session,'B',bounce,[body.ref]);self.resolve_top(session)
                    self.cast(session,'A',body);self.resolve_top(session);self.assertNotEqual(old,session.state.cards[body.object_id].logical_object_id)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual(2 if blink else 3,session.engine._numeric_stat(body.object_id,'power'))
                self.assertEqual(not blink,'lifelink' in session.engine._combat_keywords(session.state.cards[body.object_id]))
                self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'));self.assertNotIn('lifelink',session.engine._combat_keywords(session.state.cards[source.object_id]))
                if not blink:
                    life={s:session.state.players[s].life for s in 'AB'};self.until(session,lambda s:s.state.phase_index>=9)
                    self.assertEqual(life['A']+3,session.state.players['A'].life);self.assertEqual(life['B']-3,session.state.players['B'].life)
                self.replay(session,load=True)

    def test_runtime_attack_result_dependency_is_inferred_without_declaration(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(506639040);source=self.add(session.engine,'Battlegrace Angel');found=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if RESULT_CAPABILITY not in program.capability_dependencies:continue
            found+=1;deps=tuple(c for c in program.capability_dependencies if c!=RESULT_CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+RESULT_CAPABILITY,result['blockers'])
        self.assertGreater(found,0)

    def test_actual_rafiq_double_strike_reaches_the_lone_attacker_and_both_damage_steps(self):
        session=self.session(506639050);source=self.add(session.engine,'Rafiq of the Many');body=self.add(session.engine,'Grizzly Bears');self.add(session.engine,'Fervor')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session);self.attack(session,[(body,'C')]);self.assertEqual(2,self.trigger_count(session,source))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(3,session.engine._numeric_stat(body.object_id,'power'))
        self.assertIn('double strike',session.engine._combat_keywords(session.state.cards[body.object_id]));self.assertNotIn('double strike',session.engine._combat_keywords(session.state.cards[source.object_id]))
        life={s:session.state.players[s].life for s in 'AC'};self.until(session,lambda s:s.state.phase_index>=9)
        self.assertEqual(life['A'],session.state.players['A'].life);self.assertEqual(life['C']-6,session.state.players['C'].life);self.replay(session,load=True)

    def test_actual_idolized_reads_current_nonland_count_for_the_borrowed_recipients_controller(self):
        session=self.session(506639060);source=self.add(session.engine,'Idolized',seat='B');body=self.add(session.engine,'Grizzly Bears',seat='B')
        other=self.add(session.engine,'Grizzly Bears',ref='alone-other-body');self.add(session.engine,'Fervor')
        theft=self.add(session.engine,'Act of Treason',zone='hand');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        attach_objects(session.state.cards,source,body,source_timestamp=source.zone_timestamp,players=session.state.players);self.assert_closed(session,source)
        self.prepare(session,mana={'C':2,'R':1});self.checkpoint(session);self.cast(session,'A',theft,[body.ref]);self.assertIsNone(self.resolve(session))
        self.attack(session,[(body,'C')]);self.assertEqual(1,self.trigger_count(session,body));self.cast(session,'B',bounce,[other.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual('B',session.state.cards[source.object_id].controller);self.assertEqual('A',session.state.cards[body.object_id].controller)
        self.assertEqual((4,4),tuple(session.engine._numeric_stat(body.object_id,stat) for stat in ('power','toughness')));self.assertEqual('hand',session.state.cards[other.object_id].zone);self.replay(session,load=True)

    def test_actual_fervent_charge_binds_each_declared_attacker_without_buffing_idle_creatures(self):
        session=self.session(506639070);source=self.add(session.engine,'Fervent Charge',zone='hand');bear=self.add(session.engine,'Grizzly Bears');kavu=self.add(session.engine,'Rogue Kavu')
        idle=self.add(session.engine,'Grizzly Bears',ref='idle-charge-body');self.add(session.engine,'Fervor');self.assert_closed(session,source);self.assert_closed(session,kavu)
        self.prepare(session,mana={'C':1,'R':1,'W':1,'B':1});self.checkpoint(session);self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.attack(session,[(bear,'B'),(kavu,'C')]);self.assertEqual(2,self.trigger_count(session,source));self.assertEqual(0,self.trigger_count(session,kavu))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((4,4),tuple(session.engine._numeric_stat(bear.object_id,s) for s in ('power','toughness')))
        self.assertEqual((3,3),tuple(session.engine._numeric_stat(kavu.object_id,s) for s in ('power','toughness')))
        self.assertEqual((2,2),tuple(session.engine._numeric_stat(idle.object_id,s) for s in ('power','toughness')));self.replay(session,load=True)

    def test_actual_renegade_freighter_crew_then_attack_preserves_vehicle_result_identity(self):
        session=self.session(506639080);source=self.add(session.engine,'Renegade Freighter',zone='hand');body=self.add(session.engine,'Grizzly Bears');self.add(session.engine,'Fervor')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3});self.checkpoint(session);self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.priority(session,'A')
        crew=next(a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if a.crew_threshold==2)
        offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='activate:'+source.ref+':'+crew.ability_id)
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));self.assertTrue(session.state.cards[body.object_id].tapped)
        self.attack(session,[(source,'D')]);self.assertEqual(1,self.trigger_count(session,source));session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((5,4),tuple(session.engine._numeric_stat(source.object_id,s) for s in ('power','toughness')));self.assertIn('trample',session.engine._combat_keywords(session.state.cards[source.object_id]))
        life=session.state.players['D'].life;self.until(session,lambda s:s.state.phase_index>=9);self.assertEqual(life-5,session.state.players['D'].life);self.replay(session,load=True)


_PONY_RULES = (
    "Trample, haste\nWhenever this creature attacks, attacking creatures get +1/+1 until end of turn. "
    "If a Food entered under your control this turn, untap those creatures and they get an additional "
    "+2/+2 until end of turn."
)


class MotivatedPonyCompilerTests(unittest.TestCase):
    def test_current_attack_and_food_history_are_both_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.public_history_query_model import (
            ARTIFACT_SUBTYPE_ENTRY_HISTORY_CAPABILITY, PublicHistoryQuerySpec, controller_food_entry_query,
        )
        from quorune.rules.attack_food_bonus import (
            ATTACK_FOOD_SOURCE_CONDITION, ATTACK_FOOD_STATS_CAPABILITY, attack_food_stats_effect,
        )
        from quorune.semantics import SemanticProgram

        registry = load_default_capability_registry()
        profile = "commander_review"

        def compile_text(text):
            record = replace(query_record(text), keywords=("Trample", "Haste"))
            return compile_oracle_card(record, capability_registry=registry, capability_profile=profile)

        for wording in (_PONY_RULES, _PONY_RULES.replace("Food entered under", "Food entered the battlefield under")):
            result = compile_text(wording)
            self.assertEqual("exact", result.status, result.material_residuals)
            node, = [member for member in result.faces[0].nodes if member.event == "creature.attacks"]
            self.assertEqual(wording.splitlines()[1], wording[node.span.start:node.span.end])
            self.assertEqual(ATTACK_FOOD_SOURCE_CONDITION, node.event_condition)
            self.assertEqual((attack_food_stats_effect(),), node.effects)
            self.assertIsNone(node.target_schema)

        scope = controller_food_entry_query().to_dict()
        self.assertEqual((9, "controller", "food"),
                         (scope["schema_version"], scope["relation"], scope["artifact_subtype"]))
        for off_scope in ({"artifact_subtype": "treasure"}, {"relation": "opponents"}, {"schema_version": 4}):
            with self.assertRaises(ValueError):
                PublicHistoryQuerySpec.from_dict({**scope, **off_scope})

        dependencies = tuple(node.capability_dependencies)
        values = {
            "key": "compiler:attacking-food-instance", "label": "Attack Food fixture", "ability_id": "trigger:front:n2",
            "event": node.event, "active_zone": node.active_zone, "event_condition": node.event_condition,
            "effects": list(node.effects), "coverage": [*node.mechanics, *node.runtime_coverage],
        }
        def declared_program(deps, **overrides):
            return SemanticProgram(
                **{**values, **overrides}, capability_dependencies=list(deps),
                capability_closure=registry.closure(deps, profile=profile).to_dict(),
            )

        self.assertTrue(bind_semantic_program_runtime(
            declared_program(dependencies), capability_registry=registry, profile=profile,
        )["strict"])
        for owner in (ARTIFACT_SUBTYPE_ENTRY_HISTORY_CAPABILITY, ATTACK_FOOD_STATS_CAPABILITY):
            reduced = tuple(capability for capability in dependencies if capability != owner)
            bound = bind_semantic_program_runtime(declared_program(reduced), capability_registry=registry, profile=profile)
            self.assertFalse(bound["strict"], bound)
            self.assertIn("capability:undeclared_runtime_dependency:" + owner, bound["blockers"])
        effect, = node.effects
        malformed = (
            declared_program(dependencies, event_condition=None),
            declared_program(dependencies, effects=[{**effect, "power": True}]),
            declared_program(dependencies, effects=[{**effect, "food_followup": {**effect["food_followup"], "untap": False}}]),
        )
        for program in malformed:
            bound = bind_semantic_program_runtime(program, capability_registry=registry, profile=profile)
            self.assertFalse(bound["strict"], bound)
        for near in (_PONY_RULES.replace("a Food entered", "a Food left"),
                     _PONY_RULES.replace("under your control", "under an opponent's control")):
            self.assertNotEqual("exact", compile_text(near).status)
        with patch("quorune.compiler.trigger_result_templates.attack_food_stats_trigger_result", return_value=None):
            omitted = compile_text(_PONY_RULES)
            self.assertNotEqual("exact", omitted.status)
            self.assertTrue(any(member.exact for member in omitted.faces[0].nodes))


class MotivatedPonyNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = SourceAttackAloneNativeTests.session
    add = SourceAttackAloneNativeTests.add
    prepare = SourceAttackAloneNativeTests.prepare
    cast_for = SourceAttackAloneNativeTests.cast_for
    action = SourceAttackAloneNativeTests.action
    priority = SourceAttackAloneNativeTests.priority
    checkpoint = SourceAttackAloneNativeTests.checkpoint
    resolve = SourceAttackAloneNativeTests.resolve
    resolve_top = SourceAttackAloneNativeTests.resolve_top
    replay = SourceAttackAloneNativeTests.replay
    assert_closed = SourceAttackAloneNativeTests.assert_closed
    until = SourceAttackAloneNativeTests.until
    attack = SourceAttackAloneNativeTests.attack
    trigger_count = SourceAttackAloneNativeTests.trigger_count

    @classmethod
    def setUpClass(cls):
        import os

        cls.temporary = tempfile.TemporaryDirectory()
        pinned = os.environ.get("MOTIVATED_PONY_PINNED_DB")
        database = Path(cls.temporary.name) / "motivated-pony.sqlite3"
        if not pinned:
            build_fixture_database([ROOT / "tests/fixtures" / name for name in (
                "source-attack-alone-cards.json", "recorded-history-condition-cards.json",
            )], database)
        cls.db = CardDatabase(pinned or database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Current attackers and Food entry witness",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    @staticmethod
    def _current(game, card):
        return game.state.cards[card.object_id]

    @staticmethod
    def _food_entries(game, seat):
        from quorune.public_history_query_model import controller_food_entry_query, matching_public_history_events

        facts = matching_public_history_events(game.state, seat, controller_food_entry_query())
        if facts is None:
            raise AssertionError("The current typed movement history is unavailable")
        return facts

    @staticmethod
    def _stats(game, creature):
        return tuple(game.engine._numeric_stat(creature.object_id, key) for key in ("power", "toughness"))

    def _pony(self, game):
        card = self.add(game.engine, "Motivated Pony", zone="hand")
        self.assert_closed(game, card)
        return card

    def _food_spell(self, game, seat):
        source = self.add(game.engine, "Elanor Gardner", seat=seat, zone="hand")
        self.assert_closed(game, source)
        return source

    def _cast_and_resolve(self, game, seat, card, targets=()):
        self.cast_for(game, seat, card, targets)
        self.assertIsNone(self.resolve(game))

    def _finish_current_turn(self, game):
        from optional_result_followup_fixture import OptionalResultFollowupFixture

        OptionalResultFollowupFixture.finish_turn(self, game)

    def _assert_attacking(self, game, card, *, tapped):
        current = self._current(game, card)
        self.assertIs(current.tapped, tapped)
        self.assertIsNotNone(current.attacking)
        self.assertIn(current.object_id, game.state.combat.attackers)

    def test_departed_printed_food_buff_uses_current_attackers_after_source_departs(self):
        game = self.session(508206401)
        pony = self._pony(game)
        food = self.add(game.engine, "Gingerbrute", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", ref="retained-attacker")
        removed = self.add(game.engine, "Grizzly Bears", ref="departing-attacker")
        idle = self.add(game.engine, "Grizzly Bears", ref="idle-creature")
        hidden = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="private-bystander")
        bounce = [
            self.add(game.engine, "Unsummon", seat="B", zone="hand", ref=f"attack-test-bounce-{i}")
            for i in range(3)
        ]
        for index in range(3):
            self.add(game.engine, "Island", seat="B", ref=f"attack-test-island-{index}")
        self.prepare(game, mana={"C": 5, "G": 1})
        self.checkpoint(game)

        self._cast_and_resolve(game, "A", food)
        self.assertFalse(self._current(game, food).is_token)
        self.assertEqual("hand", self._current(game, pony).zone)
        entry, = self._food_entries(game, "A")
        self.assertEqual(("A", self._current(game, food).logical_object_id, "food"),
                         (entry.actor, entry.object_incarnation, entry.target))
        self.cast_for(game, "B", bounce[0], [self._current(game, food).ref])
        self.resolve_top(game)
        self.assertEqual("hand", self._current(game, food).zone)
        self.assertEqual((entry,), self._food_entries(game, "A"))

        self._cast_and_resolve(game, "A", pony)
        self.assertTrue({"haste", "trample"} <= set(game.engine._combat_keywords(self._current(game, pony))))
        self.attack(game, [(self._current(game, pony), "B"), (first, "C"), (removed, "D")])
        self.assertEqual(1, self.trigger_count(game, pony))
        for spell, target in zip(bounce[1:], (removed, pony), strict=True):
            self.cast_for(game, "B", spell, [self._current(game, target).ref])
            self.resolve_top(game)
            self.assertEqual("hand", self._current(game, target).zone)
        game = self.replay(game, load=True)

        late_ref, = game.engine.create_token(
            "A", name="Late attacking creature", copy_of=self._current(game, idle).ref,
            tapped=True, attacking="B", reason="Committed attacking entry after the trigger was queued",
        )
        late = next(card for card in game.state.cards.values() if card.ref == late_ref)
        self.checkpoint(game)
        self.resolve_top(game)
        for creature in (first, late):
            self.assertEqual((5, 5), self._stats(game, creature))
            self._assert_attacking(game, creature, tapped=False)
        self.assertEqual((2, 2), self._stats(game, idle))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))

        self._finish_current_turn(game)
        self.assertEqual((2, 2), self._stats(game, first))
        self.assertEqual((), self._food_entries(game, "A"))
        self.replay(game, load=True)

    def test_token_food_and_other_or_previous_turn_entries(self):
        for i, mode in enumerate(("caster", "other_player", "previous_turn")):
            with self.subTest(mode=mode):
                game = self.session(508206415 + i)
                pony = self._pony(game)
                creator = "B" if mode == "other_player" else "A"
                source = self._food_spell(game, creator)
                body = self.add(game.engine, "Grizzly Bears", ref="attack-rider-token-control")
                hidden = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="token-test-private-card")
                if mode == "other_player":
                    self.add(game.engine, "Vedalken Orrery", seat="B")
                    game.state.players["B"].mana_pool.update({"C": 3, "G": 1})
                    self.prepare(game, mana={"C": 4, "G": 1})
                elif mode == "previous_turn":
                    self.add(game.engine, "Vedalken Orrery")
                    for index in range(5):
                        self.add(game.engine, "Forest", ref=f"next-turn-forest-{index}")
                    self.prepare(game, active="D", actor="A", mana={"C": 3, "G": 1})
                    game.state.last_normal_turn_player = "D"
                else:
                    self.prepare(game, mana={"C": 7, "G": 2})
                self.checkpoint(game)
                self._cast_and_resolve(game, creator, source)
                committed, = self._food_entries(game, creator)
                creature_food = next(card for card in game.state.cards.values()
                                     if card.logical_object_id == committed.object_incarnation)
                self.assertTrue(creature_food.is_token)
                self.assertEqual("hand", self._current(game, pony).zone)

                if mode == "previous_turn":
                    self._finish_current_turn(game)
                    self.until(game, lambda current: current.state.active_player == "A"
                               and current.state.phase == "precombat_main")
                    self.assertEqual("battlefield", self._current(game, creature_food).zone)
                self.assertEqual(mode == "caster", bool(self._food_entries(game, "A")))
                self._cast_and_resolve(game, "A", pony)
                self.attack(game, [(self._current(game, pony), "C"), (body, "B")])
                self.resolve_top(game)
                expected = 5 if mode == "caster" else 3
                self.assertEqual((expected, expected), self._stats(game, body))
                self._assert_attacking(game, body, tapped=mode != "caster")
                self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
                self.replay(game, load=True)

    def test_prevented_untap_does_not_remove_the_additional_stats(self):
        from quorune import public_tap_state_sets

        game = self.session(508206427)
        pony = self._pony(game)
        source = self._food_spell(game, "A")
        bear = self.add(game.engine, "Grizzly Bears", ref="untap-prevention-attacker")
        self.prepare(game, mana={"C": 7, "G": 2})
        self.checkpoint(game)
        self._cast_and_resolve(game, "A", source)
        self._cast_and_resolve(game, "A", pony)
        self.attack(game, [(self._current(game, pony), "C"), (bear, "B")])
        original = public_tap_state_sets.set_permanent_tapped
        seen = []

        def suppress_untap(host, ref, *, tapped, **options):
            if ref == bear.ref and not tapped:
                seen.append(host._numeric_stat(bear.object_id, "power"))
                return ref
            return original(host, ref, tapped=tapped, **options)

        with patch.object(public_tap_state_sets, "set_permanent_tapped", side_effect=suppress_untap):
            self.resolve_top(game)
            self.assertEqual([3], seen)
            self.assertEqual((5, 5), self._stats(game, bear))
            self._assert_attacking(game, bear, tapped=True)
            self.replay(game, load=True)
