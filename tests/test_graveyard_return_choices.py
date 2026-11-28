"""Optional graveyard returns choose current owned cards after mandatory mill."""
import ast
from copy import deepcopy
from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from inherited_composition_fixture import InheritedNativeFixture
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.object_query import ObjectQueryResult
from quorune.replacement.immutable import FrozenMap
from quorune.rules.graveyard_return_choices import (
    BOUNDED_BATTLEFIELD_RETURN_CAPABILITY, BOUNDED_BATTLEFIELD_RETURN_VERSION,
    CAPABILITY, MECHANIC, OPERATION, GraveyardReturnSelection, graveyard_return_candidates,
    graveyard_return_choice_node_capabilities,
)
from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
from quorune.semantic_choices.graveyard_return import GraveyardReturnChoiceHandler
from quorune.semantic_choices.model import SemanticChoiceError


def row(ref, *, owner='A', controller=None, zone='graveyard', types=('creature',), incarnation=1, token=False,
        mana_value=0):
    return ObjectQueryResult(object_id='object:'+ref,ref=ref,printed_name=ref,owner=owner,
        controller=controller or owner,zone=zone,types=types,token=token,mana_value=mana_value,
        logical_object_id='object:'+ref+'@'+str(incarnation))


def query(rows, refs):
    return SnapshotSemanticChoiceQuery(seat_order=tuple('ABCD'),active_order=tuple('ABCD'),
        object_rows=tuple(rows),materialized_choice_candidates=tuple(refs))


def context(rows, refs, *, actor='A'):
    return SemanticChoiceContext(actor=actor,stack_ref='S1',stack_controller=actor,
        stack_label='Graveyard return witness',source_ref=None,card_ref=None,
        semantic_program_id='fixture:graveyard-choice',semantic_program_version=1,query=query(rows,refs))


def instruction(types=('creature',), player='A'):
    return {'op':OPERATION,'player':player,'selection':GraveyardReturnSelection(types).to_dict()}


class GraveyardReturnChoiceTests(unittest.TestCase):
    def test_owned_card_materialization_uses_type_union_once_and_excludes_other_zones_and_noncards(self):
        rows=(row('old'),row('milled'),row('dual',types=('creature','land')),row('land',types=('land',)),
            row('artifact',types=('artifact',)),row('foreign',owner='B',controller='A'),
            row('hand',zone='hand'),row('exiled',zone='exile'),row('token',token=True),row('spell-copy'))
        cards={r.object_id:SimpleNamespace(is_card_object=not r.token and r.ref!='spell-copy') for r in rows}
        host=SimpleNamespace(state=SimpleNamespace(cards=cards),public_zone_move_object_rows=lambda actor:rows,
            public_zone_move_active_seats=lambda:tuple('ABCD'),public_zone_move_apnap_order=lambda:tuple('CDAB'))
        actual=graveyard_return_candidates(host,'A',instruction(('creature','land')))
        self.assertEqual({'old','milled','dual','land'},set(actual))
        self.assertEqual(1,actual.count('dual'))
        permanent=GraveyardReturnSelection(('artifact','battle','creature','enchantment','land','planeswalker'))
        self.assertIn('battle',permanent.query.types_any)
        self.assertIn('planeswalker',permanent.query.types_any)
        self.assertEqual('owner',permanent.public_set().relation_axis.value)

    def test_optional_prepare_seals_incarnation_and_returns_through_existing_owner(self):
        target=row('old');ctx=context((target,),('old',));handler=GraveyardReturnChoiceHandler()
        prepared=handler.prepare(instruction(),ctx)
        self.assertEqual((0,1,True),(prepared.request.choice.minimum,prepared.request.choice.maximum,prepared.request.choice.optional))
        result=handler.complete(SimpleNamespace(effect=prepared.continuation_effect),{'card':'old'},ctx.query)
        from quorune.semantic_runtime.intents import ReturnGraveyardCardToOwnerHandIntent
        self.assertIsInstance(result.intents[0],ReturnGraveyardCardToOwnerHandIntent)
        self.assertEqual(('A','old'),(result.intents[0].actor,result.intents[0].object_ref))
        self.assertEqual({'ref':'old','object_id':'object:old','zone_change_counter':1},dict(result.intents[0].target_snapshot))
        for response in ({},{'card':None},{'card':''}):
            self.assertFalse(handler.complete(SimpleNamespace(effect=prepared.continuation_effect),response,ctx.query).intents)

    def test_empty_set_auto_continues_and_malformed_instruction_or_identity_rejects(self):
        handler=GraveyardReturnChoiceHandler()
        prepared=handler.prepare(instruction(),context((),()))
        self.assertIsNone(prepared.request)
        self.assertIsNotNone(prepared.auto_continue)
        for change in ({'player':'B'},{'targets':['old']},{'selection':{'schema_version':True,'types_any':['creature']}},
            {'selection':{'schema_version':1,'types_any':'creature'}},
            {'selection':{'schema_version':1,'types_any':['creature','land','instant']}}):
            with self.subTest(change=change),self.assertRaises(SemanticChoiceError):
                handler.prepare({**instruction(),**change},context((),()))
        for identity in ('opaque','object:old@01','object:old@-1',True):
            with self.assertRaises(SemanticChoiceError):
                handler.prepare(instruction(),context((replace(row('old'),logical_object_id=identity),),('old',)))

    def test_stale_owner_zone_type_incarnation_and_withdrawn_candidates_reject(self):
        handler=GraveyardReturnChoiceHandler();original=row('old');ctx=context((original,),('old',))
        saved=SimpleNamespace(effect=handler.prepare(instruction(),ctx).continuation_effect)
        for changed in (replace(original,logical_object_id='object:old@2'),replace(original,owner='B'),
            replace(original,zone='exile'),replace(original,types=('instant',)),replace(original,token=True)):
            with self.subTest(changed=changed),self.assertRaises(SemanticChoiceError):
                handler.complete(saved,{'card':'old'},query((changed,),('old',)))
        for response in ({'card':'foreign'},{'card':True},{'card':['old']}):
            with self.assertRaises(SemanticChoiceError):handler.complete(saved,response,ctx.query)
        with self.assertRaises(SemanticChoiceError):handler.complete(saved,{'card':'old'},query((original,),()))
        for change in ({'_choice_actor':'B'},{'_legal_logical_ids':{}},{'_legal_refs':('old','old')},{'_stack_label':False}):
            with self.assertRaises(SemanticChoiceError):handler.complete(
                SimpleNamespace(effect=FrozenMap({**dict(saved.effect),**change})),{'card':None},ctx.query)

    def test_bounded_battlefield_choice_keeps_exact_current_mana_value_and_incarnation(self):
        version = BOUNDED_BATTLEFIELD_RETURN_VERSION
        spec = GraveyardReturnSelection(("creature",), schema_version=version, mana_value_max=2)
        selected = row("selected", mana_value=2)
        oversized = replace(selected, mana_value=3)
        effect = {"op": OPERATION, "player": "A", "selection": spec.to_dict()}
        handler = GraveyardReturnChoiceHandler()
        initial = context((selected,), (selected.ref,))
        prepared = handler.prepare(effect, initial)
        self.assertEqual((0, 1, True), (prepared.request.choice.minimum, prepared.request.choice.maximum,
                                      prepared.request.choice.optional))
        saved = SimpleNamespace(effect=prepared.continuation_effect)
        completion = handler.complete(saved, {"card": selected.ref}, initial.query)
        self.assertFalse(completion.intents)
        self.assertEqual("move_public_zone_set", completion.prepend_effects[0]["op"])
        self.assertEqual("battlefield", completion.prepend_effects[0]["set"]["destination"])
        self.assertFalse(handler.complete(saved, {"card": None}, initial.query).prepend_effects)
        for stale in (oversized, replace(selected, logical_object_id="object:selected@2"), replace(selected, owner="B")):
            with self.subTest(stale=stale), self.assertRaises(SemanticChoiceError):
                handler.complete(saved, {"card": selected.ref}, query((stale,), (selected.ref,)))
        self.assertFalse(spec.matches(oversized))
        for maximum in (True, -1, 21, "2"):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                GraveyardReturnSelection(("creature",), schema_version=version, mana_value_max=maximum)
        with self.assertRaises(ValueError):
            GraveyardReturnSelection.from_dict({**spec.to_dict(), "schema_version": 1})

    def test_bounded_battlefield_mana_value_query_omission_is_detected(self):
        from unittest.mock import PropertyMock
        from quorune.object_predicate import ObjectQuerySpec

        selected = row("too-expensive", mana_value=3)
        spec = GraveyardReturnSelection(("creature",), schema_version=BOUNDED_BATTLEFIELD_RETURN_VERSION,
                                        mana_value_max=2)

        def witness():
            self.assertFalse(spec.matches(selected))

        witness()
        with patch.object(GraveyardReturnSelection, "query", new_callable=PropertyMock,
                          return_value=ObjectQuerySpec(zones=("graveyard",), types_any=("creature",), token=False)):
            with self.assertRaises(AssertionError):
                witness()

    def test_bounded_battlefield_choice_and_mill_authority_fail_closed_independently(self):
        from quorune.compiler.graveyard_return_choice_templates import graveyard_return_choice_template
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from quorune.rules.evidence import _capability_validation_snapshot, validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_granted_skulk import record

        text = "Mill three cards, then you may return a creature card with mana value 2 or less from your graveyard to the battlefield."
        profile = "commander_review"
        card = record(text, "Instant")
        active = load_default_capability_registry()
        ir = compile_oracle_card(card, capability_registry=active, capability_profile=profile)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual(("mill", OPERATION), tuple(effect["op"] for effect in node.effects))
        self.assertIsNone(node.target_schema)
        self.assertIn(BOUNDED_BATTLEFIELD_RETURN_CAPABILITY, node.capability_dependencies)
        for unsupported in (text.replace("a creature", "target creature"),
                            text.replace("your graveyard", "an opponent's graveyard"),
                            text.replace("2 or less", "X or less")):
            with self.subTest(unsupported=unsupported):
                rejected = compile_oracle_card(record(unsupported, "Instant"),
                    capability_registry=active, capability_profile=profile)
                self.assertIsNone(graveyard_return_choice_template(unsupported))
                self.assertTrue(all(BOUNDED_BATTLEFIELD_RETURN_CAPABILITY not in node.capability_dependencies
                                    for face in rejected.faces for node in face.nodes))
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        for missing in (BOUNDED_BATTLEFIELD_RETURN_CAPABILITY, "zone.mill.fixed"):
            changed = deepcopy(raw)
            target = next(row for row in changed["capabilities"] if row["id"] == missing)
            target.update(status="blocked", blockers=["Scoped withdrawal"])
            registry = CapabilityRegistry(changed)
            with _capability_validation_snapshot():
                index = build_index(registry_value=changed, declaration_source=declarations,
                                    discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            with self.subTest(missing=missing):
                self.assertNotEqual("exact", compile_oracle_card(card,
                    capability_registry=registry, capability_profile=profile).status)
                alternative = ("Mill three cards." if missing == BOUNDED_BATTLEFIELD_RETURN_CAPABILITY
                               else "You may return a creature card from your graveyard to your hand.")
                control = compile_oracle_card(record(alternative, "Instant"),
                    capability_registry=registry, capability_profile=profile)
                self.assertEqual("exact", control.status, control.material_residuals)

    def test_compiler_orders_mill_then_untargeted_choice_and_withdrawal_preserves_plain_mill(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for subject in ('creature','land','creature or land','permanent'):
            text='Mill three cards, then you may return a '+subject+' card from your graveyard to your hand.'
            ir=compile_oracle_card(record(text,'Instant'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0]
            self.assertIsNone(node.target_schema)
            self.assertEqual(('mill',OPERATION),tuple(effect['op'] for effect in node.effects))
            self.assertEqual('$controller',node.effects[-1]['player'])
            self.assertIn(CAPABILITY,node.capability_dependencies)
        from quorune.compiler.graveyard_return_choice_templates import graveyard_return_choice_template
        for text in ('You may return up to one target creature card from your graveyard to your hand.',
            'You may return a creature card milled this way from your graveyard to your hand.',
            'You may return two creature cards from your graveyard to your hand.',
            'You may return a creature card from a graveyard to your hand.'):
            self.assertIsNone(graveyard_return_choice_template(text))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['choice owner withdrawn'])
        blocked=CapabilityRegistry(raw)
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        index=build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        blocked.mark_evidence_verified(validate_capability_evidence_index(index,registry=blocked))
        self.assertNotEqual('exact',compile_oracle_card(record('You may return a creature card from your graveyard to your hand.','Instant'),
            capability_registry=blocked,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(record('Mill three cards.','Instant'),
            capability_registry=blocked,capability_profile='commander_review').status)

    def test_mandatory_creature_or_planeswalker_is_closed_and_cannot_decline(self):
        from quorune.compiler.graveyard_return_choice_templates import graveyard_return_choice_template
        from quorune.rules.graveyard_return_choices import mandatory_creature_or_planeswalker_return_effect

        text = "Mill three cards, then return a creature or planeswalker card from your graveyard to your hand."
        compiled = graveyard_return_choice_template(text)
        self.assertIsNone(compiled[2])
        choice = mandatory_creature_or_planeswalker_return_effect()
        self.assertEqual(({"op": "mill", "player": "$controller", "count": 3}, choice), compiled[1])
        self.assertIsNone(graveyard_return_choice_template(text.replace("a creature", "a target creature")))
        self.assertIsNone(graveyard_return_choice_template(text.replace("from your", "milled this way from your")))
        with self.assertRaises(ValueError):
            GraveyardReturnSelection(("creature",), schema_version=choice["selection"]["schema_version"])

        walker = row("walker", types=("planeswalker",))
        ctx = context((walker,), ("walker",))
        handler = GraveyardReturnChoiceHandler()
        prepared = handler.prepare({**choice, "player": "A"}, ctx)
        option = prepared.request.choice
        self.assertEqual((1, 1, False), (option.minimum, option.maximum, option.optional))
        self.assertEqual(5, choice["selection"]["schema_version"])
        retained = {**choice, "player": "A", "selection": {**choice["selection"], "schema_version": 4}}
        retained_offer = handler.prepare(retained, ctx)
        self.assertEqual(option, retained_offer.request.choice)
        self.assertEqual(4, retained_offer.continuation_effect["selection"]["schema_version"])
        for malformed in (
            {"schema_version": 4, "types_any": ["creature"]},
            {"schema_version": 4, "types_any": ["creature", "planeswalker"], "mana_value_max": 2},
            {"schema_version": 4, "types_any": ["creature", "planeswalker"], "mana_value_max": None},
            {"schema_version": 5, "types_any": ["creature"], "mana_value_max": 2},
            {"schema_version": 5, "types_any": ["creature", "planeswalker"], "mana_value_max": None},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                GraveyardReturnSelection.from_dict(malformed)
        continuation = SimpleNamespace(effect=prepared.continuation_effect)
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {}, ctx.query)
        result = handler.complete(continuation, {"card": walker.ref}, ctx.query)
        self.assertEqual(walker.ref, result.intents[0].object_ref)
        empty = handler.prepare({**choice, "player": "A"}, context((), ()))
        self.assertIsNone(empty.request)
        self.assertIsNotNone(empty.auto_continue)

    def test_runtime_infers_and_validates_choice_independently(self):
        from quorune.card_programs.runtime_requirements import program_dynamic_runtime_requirements
        from quorune.semantics import SemanticProgram
        effect=instruction(player='$controller')
        program=SemanticProgram(key='fixture:graveyard-choice',label='Graveyard choice',effects=[effect],coverage=[MECHANIC])
        required,blockers=program_dynamic_runtime_requirements(program)
        self.assertIn(CAPABILITY,required)
        self.assertFalse(blockers)
        self.assertFalse(graveyard_return_choice_node_capabilities(effects=(effect,),
            target_schema={'zones':['graveyard']},mechanic_ids=(MECHANIC,)))
        program.effects=[{**effect,'_runtime_source':{}}]
        _required,blockers=program_dynamic_runtime_requirements(program)
        self.assertTrue(any('graveyard_return_choice' in blocker for blocker in blockers))

    def test_optional_and_incarnation_guard_implementation_mutants_are_killed(self):
        import quorune.semantic_choices.graveyard_return as implementation
        real_choice=implementation.ObjectChoice
        with patch.object(implementation,'ObjectChoice',side_effect=lambda **kwargs:
                real_choice(**{**kwargs,'minimum':1,'optional':False})):
            with self.assertRaises(AssertionError):self.test_optional_prepare_seals_incarnation_and_returns_through_existing_owner()
        tree=ast.parse(textwrap.dedent(inspect.getsource(GraveyardReturnChoiceHandler.complete)))
        class RemoveIncarnationGuard(ast.NodeTransformer):
            def visit_Compare(self,node):
                if ast.unparse(node)=='row.logical_object_id != identities[selected]':return ast.copy_location(ast.Constant(False),node)
                return self.generic_visit(node)
        tree=ast.fix_missing_locations(RemoveIncarnationGuard().visit(tree));namespace=dict(vars(implementation))
        exec(compile(tree,'<graveyard-incarnation-mutant>','exec'),namespace)
        with patch.object(GraveyardReturnChoiceHandler,'complete',namespace['complete']):
            original=row('old');ctx=context((original,),('old',));handler=GraveyardReturnChoiceHandler()
            saved=SimpleNamespace(effect=handler.prepare(instruction(),ctx).continuation_effect)
            with self.assertRaises(AssertionError):
                with self.assertRaises(SemanticChoiceError):
                    handler.complete(saved,{'card':'old'},query((replace(original,logical_object_id='object:old@2'),),('old',)))


class GraveyardReturnChoiceNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'graveyard-salvage.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/source-explore-cards.json',ROOT/'tests/fixtures/graveyard-salvage-cards.json',
            ROOT/'tests/fixtures/scryfall-exact-lists.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Graveyard choice',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    from test_crime import CrimeNativeTests as _crime
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _actions
    from casualty_fixture import CasualtyFixture as _casualty
    from test_explore_event_triggers import ExploreEventNativeTests as _explore_actions
    session=_crime.session
    start=_crime.start
    cast=_crime.cast
    priority=_crime.priority_to
    assert_closed=_crime.assert_closed
    add=_actions.add
    checkpoint=_actions.checkpoint
    resolve=_actions.resolve
    replay=_actions.replay
    casualty_action=_casualty.casualty_action
    cast_casualty=_casualty.cast_casualty
    resolve_top=_explore_actions.resolve_top

    def library(self,session,names,seat='A'):
        for oid in list(session.state.players[seat].zones['library']):
            session.engine.move_card(oid,'exile',reason='Prepared salvage library')
        return [self.add(session.engine,name,seat=seat,zone='library',ref='SALVAGE_'+seat+'_TOP_'+str(i))
            for i,name in enumerate(names)]

    def options(self,session):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind)
        ctx=session.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual(OPERATION,ctx['operation'])
        return {row['id'] for row in ctx['objects']}

    def choose(self,session,card=None,invalid=()):
        from quorune.record import authoritative_state_hash
        command={'action_id':'choose','card':card.ref if card is not None else None}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        for ref in invalid:
            self.assertFalse(session.act('pilot:A',{'action_id':'choose','card':ref}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        return session

    def activate_tyvar_minus_two(self, session, tyvar):
        from quorune.record import authoritative_state_hash

        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"] == f"activate:{tyvar.ref}:ab3")
        self.assertFalse(action.get("target_schema"))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", {"action_id": action["id"]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.cards[tyvar.object_id].counters["loyalty"])
        self.assertEqual([], session.state.stack[-1].targets)
        return action["id"]

    def test_actual_tyvar_mills_and_can_return_old_or_new_current_creature(self):
        from quorune.record import authoritative_state_hash

        for from_library in (False, True):
            with self.subTest(from_library=from_library):
                session = self.session(946101 + from_library)
                tyvar = self.add(session.engine, "Tyvar, Jubilant Brawler", zone="hand")
                old = self.add(session.engine, "Dryad Arbor", zone="graveyard")
                expensive = self.add(session.engine, "Tireless Provisioner", zone="graveyard")
                foreign = self.add(session.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="tyvar-foreign")
                new, rock, sorcery = self.library(session, ("Grizzly Bears", "Sol Ring", "Divination"))
                self.assert_closed(session, tyvar)
                self.start(session)
                self.cast(session, tyvar)
                self.resolve_top(session)
                self.assertEqual(3, session.state.cards[tyvar.object_id].counters["loyalty"])
                action = self.activate_tyvar_minus_two(session, tyvar)
                session = self.replay(session, load=True)
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                self.assertEqual({old.ref, new.ref}, self.options(session))
                self.assertTrue(all(session.state.cards[p.object_id].zone == "graveyard" for p in (new, rock, sorcery)))
                self.assertEqual(0, len(session.state.players["A"].zones["library"]))
                chosen = new if from_library else old
                prior_incarnation = session.state.cards[chosen.object_id].logical_object_id
                session = self.choose(session, chosen, (foreign.ref, expensive.ref, rock.ref, sorcery.ref))
                self.assertIsNone(self.resolve(session))
                returned = session.state.cards[chosen.object_id]
                self.assertEqual(("battlefield", "A", False), (returned.zone, returned.controller, returned.tapped))
                self.assertNotEqual(prior_incarnation, returned.logical_object_id)
                self.priority(session, "A")
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:A", {"action_id": action}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                for seat in "ABC":
                    self.assertNotIn("CRIME_PRIVATE_D_HAND", json.dumps(session.packet("pilot:" + seat, full=True)))
                self.replay(session, load=True)

    def test_actual_tyvar_short_mill_and_empty_library_preserve_decline_after_replacement(self):
        for cards in (("Grizzly Bears",), ()):
            with self.subTest(cards=cards):
                session = self.session(946105 + len(cards))
                tyvar = self.add(session.engine, "Tyvar, Jubilant Brawler", zone="hand")
                old = self.add(session.engine, "Birds of Paradise", zone="graveyard")
                self.add(session.engine, "Dauthi Voidwalker", seat="B")
                milled = self.library(session, cards)
                self.assert_closed(session, tyvar)
                self.start(session)
                self.cast(session, tyvar)
                self.resolve_top(session)
                self.activate_tyvar_minus_two(session, tyvar)
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                self.assertEqual({old.ref}, self.options(session))
                for exiled in milled:
                    self.assertEqual("exile", session.state.cards[exiled.object_id].zone)
                session = self.choose(session, None, tuple(card.ref for card in milled))
                self.assertIsNone(self.resolve(session))
                self.assertEqual("graveyard", session.state.cards[old.object_id].zone)
                self.assertEqual(1, session.state.cards[tyvar.object_id].counters["loyalty"])
                self.replay(session, load=True)

    def test_actual_short_mill_can_return_preexisting_union_card_without_target_or_crime(self):
        session=self.session(646001)
        spell=self.add(session.engine,'Grapple with the Past',zone='hand',ref='SALVAGE_GRAPPLE')
        old=self.add(session.engine,'Dryad Arbor',zone='graveyard',ref='SALVAGE_OLD_DUAL')
        artifact=self.add(session.engine,'Sol Ring',zone='graveyard',ref='SALVAGE_WRONG_TYPE')
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='SALVAGE_FOREIGN')
        top,=self.library(session,['Forest'])
        for card in (spell,old,foreign):self.assert_closed(session,card)
        self.start(session);self.cast(session,spell)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual({old.ref,top.ref},self.options(session))
        self.assertEqual(0,len(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard',session.state.cards[top.object_id].zone)
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(session.state);move=session.engine.move_card
        def fail_after_move(object_id,destination,**kwargs):
            result=move(object_id,destination,**kwargs)
            if object_id==old.object_id and destination=='hand':raise GameRuleError('injected after salvage movement')
            return result
        with patch.object(session.engine,'move_card',side_effect=fail_after_move):
            failed=session.act('pilot:A',{'action_id':'choose','card':old.ref})
        self.assertFalse(failed.ok);self.assertIn('injected after salvage movement',failed.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.choose(session,old,(foreign.ref,artifact.ref))
        self.resolve(session)
        self.assertEqual('hand',session.state.cards[old.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[top.object_id].zone)
        from quorune.rules.crime import crime_count_this_turn
        self.assertEqual(0,crime_count_this_turn(session.state,'A'))
        for viewer in 'ABC':self.assertTrue('CRIME_PRIVATE_D_HAND' not in json.dumps(session.packet('pilot:'+viewer,full=True)),viewer)
        self.replay(session,load=True)

    def test_actual_decline_and_empty_creature_set_keep_milling_and_permanent_scope(self):
        session=self.session(646002)
        source=self.add(session.engine,'Acolyte of Affliction',zone='hand',ref='SALVAGE_ACOLYTE')
        later=self.add(session.engine,'Corpse Churn',zone='hand',ref='SALVAGE_EMPTY_CHURN')
        artifact=self.add(session.engine,'Sol Ring',zone='graveyard',ref='SALVAGE_PERMANENT_ARTIFACT')
        land=self.add(session.engine,'Forest',zone='graveyard',ref='SALVAGE_PERMANENT_LAND')
        theft=self.add(session.engine,'Act of Treason',seat='B',zone='hand',ref='SALVAGE_SOURCE_CONTROL')
        self.add(session.engine,'Vedalken Orrery',seat='B',ref='SALVAGE_B_FLASH')
        milled=self.library(session,['Divination','Divination'])
        for card in (source,later,theft):self.assert_closed(session,card)
        self.start(session);session.state.players['B'].mana_pool.update(C=4,R=3);self.checkpoint(session)
        other_library=len(session.state.players['B'].zones['library'])
        self.cast(session,source);self.resolve_top(session)
        self.priority(session,'B');self.cast(session,theft,(source.ref,),actor='B');self.resolve_top(session)
        self.assertEqual('B',session.state.cards[source.object_id].controller)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual(['A'],session.state.pending_decision.actors)
        self.assertEqual(other_library,len(session.state.players['B'].zones['library']))
        self.assertEqual({artifact.ref,land.ref},self.options(session))
        session=self.choose(session,None,tuple(card.ref for card in milled));self.resolve(session)
        self.assertTrue(all(session.state.cards[card.object_id].zone=='graveyard' for card in milled))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.cast(session,later);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[later.object_id].zone)
        self.assertEqual(0,len(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard',session.state.cards[artifact.object_id].zone)
        self.replay(session,load=True)

    def test_actual_casualty_copy_chooses_again_after_each_mill_and_respects_cost_owner(self):
        session=self.session(646003)
        spell=self.add(session.engine,'Dig Up the Body',zone='hand',ref='SALVAGE_CASUALTY')
        cost=self.add(session.engine,'Grizzly Bears',ref='SALVAGE_BORROWED_COST');cost.owner='B'
        cards=self.library(session,['Sol Ring','Grizzly Bears','Forest','Ornithopter'])
        self.assert_closed(session,spell);self.assert_closed(session,cost)
        self.start(session);self.cast_casualty(session,spell,cost)
        self.assertEqual('graveyard',session.state.cards[cost.object_id].zone)
        self.assertIn(cost.object_id,session.state.players['B'].zones['graveyard'])
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual({cards[3].ref},self.options(session))
        session=self.choose(session,cards[3],(cost.ref,));self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual({cards[1].ref},self.options(session))
        session=self.choose(session,cards[1],(cost.ref,cards[3].ref));self.resolve(session)
        self.assertEqual(('hand','hand'),tuple(session.state.cards[cards[i].object_id].zone for i in (1,3)))
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        self.assertEqual(0,len(session.state.players['A'].zones['library']))
        from quorune.rules.crime import crime_count_this_turn
        self.assertEqual(0,crime_count_this_turn(session.state,'A'))
        self.replay(session,load=True)

class TakenumaNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _actions

    session = _actions.session
    add = _actions.add
    ready = _actions.ready
    checkpoint = _actions.checkpoint
    resolve = _actions.resolve
    replay = _actions.replay

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "takenuma.sqlite3"
        fixtures = ("bound-effect-program-cards.json", "scryfall-exact-lists.json",
                    "graveyard-exile-replacement-cards.json", "fixed-emblem-event-cards.json")
        build_fixture_database([ROOT / "tests" / "fixtures" / name for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Takenuma witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
                                 DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def channel_offer(self, session, source, generic):
        self.ready(session, source, {"C": 3, "B": 1})
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [entry for entry in actions if entry["id"] == f"activate:{source.ref}:ab2"]
        mana = {"B": 1, "GENERIC": generic} if generic else {"B": 1}
        self.assertEqual({"discard_self": 1, "m": mana}, action["cost_summary"])
        self.assertEqual("hand", action["from"])
        self.assertFalse(action.get("target_schema"))
        return action

    def act(self, session, action, **fields):
        result = session.act("pilot:A", {"action_id": action, **fields})
        self.assertTrue(result.ok, result.summary)

    def test_channel_price_and_choices_from_the_settled_graveyard_replay(self):
        from quorune.record import authoritative_state_hash
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime

        session = self.session(646040)
        engine = session.engine
        source = self.add(engine, "Takenuma, Abandoned Mire", zone="hand")
        walker = self.add(engine, "Vraska, Golgari Queen", zone="graveyard")
        creature = self.add(engine, "Deathrite Shaman", zone="library")
        self.add(engine, "Forest", zone="library", ref="takenuma-filler-a")
        self.add(engine, "Forest", zone="library", ref="takenuma-filler-b")
        self.add(engine, "Zimone and Dina", seat="B", ref="takenuma-foreign-legend")
        self.add(engine, "Vraska, Golgari Queen", ref="takenuma-legendary-planeswalker")
        land = self.add(engine, "Takenuma, Abandoned Mire", ref="takenuma-legendary-land")
        land.tapped = True
        foreign = self.add(engine, "Deathrite Shaman", seat="B", zone="graveyard", ref="takenuma-foreign-card")
        self.channel_offer(session, source, generic=3)
        self.add(engine, "Zimone and Dina", ref="takenuma-owned-legend")
        engine.create_token("A", name="Legendary witness", characteristics={
            "type_line": "Legendary Creature — Elf", "power": "1", "toughness": "1",
        })
        action = self.channel_offer(session, source, generic=1)
        program = compile_best_available_card_program(
            self.db, self.db.lookup(source.printed_name), semantic_registry=engine.semantics,
            capability_registry=self.registry, capability_profile="commander_review",
        )
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        before_source = source.zone_change_counter
        before_library = len(session.state.players["A"].zones["library"])
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": action["id"], "pay": "manual", "payment": {"C": 2}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.act(session, action["id"], pay="manual", payment={"C": 1, "B": 1})
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(before_source + 1, session.state.cards[source.object_id].zone_change_counter)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("B", 0))
        self.assertEqual(2, session.state.players["A"].mana_pool.get("C", 0))
        self.assertFalse(session.state.stack[-1].targets)
        decision = self.resolve(session)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertEqual(before_library - 3, len(session.state.players["A"].zones["library"]))
        ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(OPERATION, ctx["operation"])
        self.assertEqual({creature.ref, walker.ref}, {row["id"] for row in ctx["objects"]})
        before = authoritative_state_hash(session.state)
        for command in ({"action_id": "choose"}, {"action_id": "choose", "card": foreign.ref}):
            self.assertFalse(session.act("pilot:A", command).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        restored = self.replay(session, load=True)
        for selected, branch in ((creature, restored), (walker, session)):
            self.act(branch, "choose", card=selected.ref)
            self.resolve(branch)
            self.assertEqual("hand", branch.state.cards[selected.object_id].zone)
            self.assertEqual(before_library - 3, len(branch.state.players["A"].zones["library"]))
            self.assertEqual(before_source + 1, branch.state.cards[source.object_id].zone_change_counter)
            self.replay(branch)

    def test_channel_settles_empty_and_redirected_mill_without_a_choice(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected):
                session = self.session(646041 + redirected)
                engine = session.engine
                source = self.add(engine, "Takenuma, Abandoned Mire", zone="hand")
                if redirected:
                    self.add(engine, "Rest in Peace", seat="B")
                milled = [
                    self.add(engine, name, zone="library", ref=f"takenuma-top-{index}")
                    for index, name in enumerate(
                        ("Deathrite Shaman", "Forest", "Forest") if redirected else ("Forest",) * 3
                    )
                ]
                action = self.channel_offer(session, source, generic=3)
                self.checkpoint(session)
                self.act(session, action["id"], pay="auto")
                self.assertIsNone(self.resolve(session))
                zone = "exile" if redirected else "graveyard"
                for card in (source, *milled):
                    self.assertEqual(zone, session.state.cards[card.object_id].zone)
                self.replay(session)


class EachGraveyardReturnNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {
                "Generic Bound Commander", "Generic Bound Plains", "Generic Bound Growth", "Grizzly Bears", "Unsummon",
            },
            "attached-destruction-set-cards.json": {"Doomed Traveler"},
            "counted-library-search-cards.json": {"Jace Beleren"},
            "each-graveyard-mill-return-cards.json": {"Breach the Multiverse"},
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((ROOT / "tests/fixtures" / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names, filename
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        database = folder / "each-graveyard-return.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Milling and public entry", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_breach_compiles_all_three_closed_stages_in_printed_order(self):
        from quorune.oracle_ir import compile_oracle_card
        from test_bound_effect_programs import record

        material = (
            "Each player mills ten cards. For each player, choose a creature or planeswalker card in that player's graveyard. "
            "Put those cards onto the battlefield under your control. Then each creature you control becomes a Phyrexian "
            "in addition to its other types."
        )
        ir = compile_oracle_card(record(material, "Sorcery"), capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(material, node.text)
        self.assertIsNone(node.target_schema)
        self.assertEqual(("mill", "choose_option", "add_subtype"), tuple(effect["op"] for effect in node.effects))
        for text in (material.replace("under your control", "under their owners' control"),
                     material.replace("in addition to its other types", "until end of turn")):
            self.assertNotEqual("exact", compile_oracle_card(record(text, "Sorcery"),
                                                           capability_registry=self.registry).status)

    def _breach_world(self, *, eligible, seed):
        game = self.session(seed)
        breach = self.add(game.engine, "Breach the Multiverse", zone="hand")
        existing = self.add(game.engine, "Grizzly Bears", ref="breach-existing")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="breach-private")
        selected = ()
        alternative = None
        if eligible:
            own = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="breach-own-old")
            foreign = self.add(game.engine, "Grizzly Bears", seat="C", zone="graveyard", ref="breach-foreign-old")
            fresh = self.add(game.engine, "Jace Beleren", seat="B", zone="library", ref="breach-fresh-walker")
            alternative = self.add(game.engine, "Doomed Traveler", zone="library", ref="breach-alternative")
            self.assertEqual(fresh.object_id, game.state.players["B"].zones["library"][-1])
            selected = (own, fresh, foreign)
        self.assert_closed(game, breach)
        self.prepare(game, mana={"C": 12, "B": 4, "G": 2})
        self.checkpoint(game)
        before = {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.cast(game, "A", breach)
        decision = self.resolve(game)
        for seat, total in before.items():
            self.assertEqual(total - 10, len(game.state.players[seat].zones["library"]))
        return game, decision, selected, alternative, existing, private

    def _choose_return(self, game, selected, alternative):
        from quorune.record import authoritative_state_hash

        decision = game.packet("pilot:A", full=True)["decision"]
        groups = decision["ctx"]["graveyard_groups"]
        self.assertEqual({"A", "B", "C"}, set(groups))
        self.assertIn(alternative.ref, groups["A"])
        self.assertIn(selected[1].ref, groups["B"])
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "cards": [card.ref for card in selected]}
        invalid = {"action_id": "choose", "cards": [selected[0].ref, alternative.ref, selected[1].ref]}
        self.assertFalse(game.act("pilot:A", invalid).ok)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_breach_mills_then_chooses_and_reanimates_one_card_per_graveyard(self):
        import json

        game, decision, selected, alternative, existing, private = self._breach_world(eligible=True, seed=7011301)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in selected))
        game = self.replay(game, load=True)
        self._choose_return(game, selected, alternative)

        cards = tuple(game.state.cards[card.object_id] for card in selected)
        self.assertTrue(all(card.zone == "battlefield" and card.controller == "A" for card in cards))
        self.assertEqual(("A", "B", "C"), tuple(card.owner for card in cards))
        self.assertEqual(1, len({card.zone_timestamp for card in cards}))
        self.assertEqual("graveyard", game.state.cards[alternative.object_id].zone)
        for card in (cards[0], cards[2], game.state.cards[existing.object_id]):
            self.assertIn("phyrexian", game.engine._effective_type_parts(card)[1])
        self.assertNotIn("phyrexian", game.engine._effective_type_parts(cards[1])[1])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_breach_locks_the_creature_only_subtype_even_without_a_return(self):
        from dataclasses import replace
        from quorune.continuous_effect_model import ContinuousEffectDuration, ContinuousOperation
        from quorune.continuous_effects import CharacteristicState, evaluate_continuous_effects
        from quorune.object_predicate import ObjectQuerySpec

        game, decision, _, _, existing, _ = self._breach_world(eligible=False, seed=6110201)
        self.assertIsNone(decision)
        self.assertIn("phyrexian", game.engine._effective_type_parts(existing)[1])
        grant, = [effect for effect in game.state.continuous_effects
                  if effect.duration is ContinuousEffectDuration.ZONE_OBJECT]
        context = {"zone": "battlefield", "object_id": existing.object_id,
                   "logical_object_id": existing.logical_object_id}
        noncreature = evaluate_continuous_effects(CharacteristicState("Bear", card_types={"Artifact"}), [grant],
                                                 context=context)
        creature = evaluate_continuous_effects(CharacteristicState("Bear", card_types={"Creature"}), [grant],
                                               context=context)
        self.assertNotIn("Phyrexian", noncreature.characteristics["subtypes"])
        self.assertIn("Phyrexian", creature.characteristics["subtypes"])
        later_animation = replace(
            grant, effect_id="later-animation", timestamp=grant.timestamp + 1,
            applies=ObjectQuerySpec(zones=("battlefield",)),
            operations=(ContinuousOperation("add_types", ("Creature",), field="card_types"),),
        )
        animated = evaluate_continuous_effects(
            CharacteristicState("Bear", card_types={"Artifact"}), [grant, later_animation], context=context,
        )
        self.assertIn("Phyrexian", animated.characteristics["subtypes"])
        self.assertLess(animated.applied_effects.index("later-animation"), animated.applied_effects.index(grant.effect_id))
        after_return = evaluate_continuous_effects(CharacteristicState("Bear", card_types={"Creature"}), [grant],
            context={**context, "logical_object_id": existing.object_id + "@99"})
        self.assertNotIn("Phyrexian", after_return.characteristics["subtypes"])
        self.replay(game, load=True)

    def test_breach_after_mill_return_and_subtype_omission_mutants_are_killed(self):
        from unittest.mock import patch
        from quorune import zone_object_subtype_grants
        from quorune.rules import graveyard_return_choices

        real_move = graveyard_return_choices.each_graveyard_return_move
        game, _, selected, alternative, _, _ = self._breach_world(eligible=True, seed=7011302)
        with patch.object(graveyard_return_choices, "each_graveyard_return_move",
                          lambda cards, **kwargs: real_move(cards[:-1], **kwargs)):
            self._choose_return(game, selected, alternative)
        self.assertEqual("graveyard", game.state.cards[selected[-1].object_id].zone)
        with self.assertRaises(AssertionError):
            self.assertEqual("battlefield", game.state.cards[selected[-1].object_id].zone)
        with patch.object(zone_object_subtype_grants, "commit_controlled_creature_subtype_addition", return_value=()):
            omitted, _, _, _, existing, _ = self._breach_world(eligible=False, seed=6110202)
        self.assertNotIn("phyrexian", omitted.engine._effective_type_parts(existing)[1])
        with self.assertRaises(AssertionError):
            self.assertIn("phyrexian", omitted.engine._effective_type_parts(existing)[1])


class DifferentNameGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "different-name-graveyard-return.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "bound-effect-program-cards.json",
                                fixtures / "graveyard-salvage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Different-name return", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def start_ultimatum(self, game, spell):
        self.prepare(game, mana={"W": 2, "B": 3, "G": 2})
        self.assert_closed(game, spell)
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertFalse(game.state.stack[-1].targets)
        return self.resolve(game)

    def save_replay(self, game, name):
        from hashlib import file_digest
        from quorune.record import authoritative_state_hash, replay_record

        parent = os.environ.get("QUORUNE_DIFFERENT_NAME_RETURN_RECORD_PARENT")
        if parent is None:
            self.replay(game, load=True)
            return
        destination = Path(parent) / name
        game.save(destination)
        result = replay_record(destination, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        state_hash = authoritative_state_hash(game.state)
        self.assertEqual(state_hash, result["final_state_hash"])
        with (destination / "manifest.json").open("rb") as handle:
            manifest_hash = file_digest(handle, "sha256").hexdigest()
        destination.with_suffix(".receipt.json").write_text(
            json.dumps({"manifest_sha256": manifest_hash, "final_state_sha256": state_hash,
                        "commands": len(game.commands)}, indent=2) + "\n", encoding="utf-8",
        )

    def test_actual_ultimatum_chooses_distinct_front_names_then_returns_one_group(self):
        from quorune.record import authoritative_state_hash
        from quorune.zone_transitions import ZoneTransitionOwner

        game = self.session(44001577)
        engine = game.engine
        spell = self.add(engine, "Eerie Ultimatum", zone="hand")
        forest = self.add(engine, "Forest", zone="graveyard", ref="ultimatum-first-forest")
        duplicate = self.add(engine, "Forest", zone="graveyard", ref="ultimatum-second-forest")
        bear = self.add(engine, "Grizzly Bears", zone="graveyard")
        aura = self.add(engine, "Pacifism", zone="graveyard")
        modal = self.add(engine, "Tangled Florahedron", zone="graveyard")
        sorcery = self.add(engine, "Bala Ged Recovery", zone="graveyard")
        foreign = self.add(engine, "Generic Bound Plains", seat="D", zone="graveyard")
        existing = self.add(engine, "Grizzly Bears", seat="B", ref="ultimatum-existing-bear")
        subject = (forest, duplicate, bear, aura, modal)
        preceding = {card.object_id: card.logical_object_id for card in subject}
        pending = self.start_ultimatum(game, spell)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        owner = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual(OPERATION, owner["ctx"]["operation"])
        cards = {row["id"]: row["name"] for row in owner["ctx"]["objects"]}
        self.assertEqual({card.ref for card in subject}, set(cards))
        self.assertEqual("Tangled Florahedron", cards[modal.ref])
        self.assertIn("subset_maxima", json.dumps(owner))
        self.assertNotIn(sorcery.ref, cards)
        self.assertNotIn(foreign.ref, cards)
        self.save_replay(game, "eerie-ultimatum-pending-game-record")
        before = authoritative_state_hash(game.state)
        for principal, refs in (("A", [forest.ref, duplicate.ref]), ("A", [sorcery.ref]),
                                ("A", [foreign.ref]), ("B", [forest.ref])):
            result = game.act("pilot:" + principal, {"action_id": "choose", "cards": refs})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        selected = (forest, bear, aura, modal)
        result = game.act("pilot:A", {"action_id": "choose", "cards": [card.ref for card in selected]})
        self.assertTrue(result.ok, result.summary)
        pending = game.state.pending_decision
        self.assertEqual(("aura.entry", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(["graveyard"] * len(selected),
                         [game.state.cards[card.object_id].zone for card in selected])
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": "choose", "aura_target": bear.ref})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.save_replay(game, "eerie-ultimatum-aura-pending-game-record")
        observe = ZoneTransitionOwner._publish_simultaneous_zone_batch
        batches = []

        def record_batch(owner, snapshots, *args, **kwargs):
            batch = {card.object_id for card, _ in snapshots if card.object_id in preceding}
            if batch:
                batches.append(batch)
            return observe(owner, snapshots, *args, **kwargs)

        with patch.object(ZoneTransitionOwner, "_publish_simultaneous_zone_batch", record_batch):
            result = game.act("pilot:A", {"action_id": "choose", "aura_target": existing.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([{card.object_id for card in selected}], batches)
        returned = [game.state.cards[card.object_id] for card in selected]
        self.assertEqual({("battlefield", "A")}, {(card.zone, card.controller) for card in returned})
        self.assertEqual(1, len({card.zone_timestamp for card in returned if card.object_id != aura.object_id}))
        self.assertTrue(all(card.logical_object_id != preceding[card.object_id] for card in returned))
        self.assertEqual(existing.object_id, game.state.cards[aura.object_id].attached_to)
        self.assertEqual("Tangled Florahedron", game.state.cards[modal.object_id].active_face)
        self.assertEqual(["graveyard"] * 4, [game.state.cards[card.object_id].zone
                         for card in (spell, duplicate, sorcery, foreign)])
        self.save_replay(game, "eerie-ultimatum-batch-game-record")

    def test_actual_ultimatum_empty_decline_and_aura_with_no_existing_subject(self):
        for index, scenario in enumerate(("empty", "decline", "aura")):
            with self.subTest(scenario=scenario):
                game = self.session(44001578 + index)
                spell = self.add(game.engine, "Eerie Ultimatum", zone="hand")
                if scenario != "empty":
                    bear = self.add(game.engine, "Grizzly Bears", zone="graveyard")
                if scenario == "aura":
                    aura = self.add(game.engine, "Pacifism", zone="graveyard")
                pending = self.start_ultimatum(game, spell)
                if scenario == "empty":
                    self.assertNotEqual("semantic.choice", getattr(pending, "kind", None))
                else:
                    self.assertEqual("semantic.choice", pending.kind)
                    choices = [bear.ref, aura.ref] if scenario == "aura" else []
                    result = game.act("pilot:A", {"action_id": "choose", "cards": choices})
                    self.assertTrue(result.ok, result.summary)
                    if scenario == "aura":
                        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
                        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
                    else:
                        self.assertEqual("graveyard", game.state.cards[bear.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.save_replay(game, f"eerie-ultimatum-{scenario}-game-record")

    def test_ultimatum_whole_binding_requires_the_original_distinct_name_instruction(self):
        from test_controller_creature_keyword_absence import register_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.different_name_graveyard_return import CAPABILITY as DISTINCT_NAMES

        profile = "commander_review"
        card = self.db.lookup("Eerie Ultimatum", fuzzy=False)
        binding, registry = register_card(self.db, self.registry, card.name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = registry.programs_for_oracle(card.oracle_id)
        deps = tuple(value for value in program.capability_dependencies if value != DISTINCT_NAMES)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=profile).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=profile)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{DISTINCT_NAMES}", result["blockers"])
        changed = replace(program, event="step.begin")
        self.assertFalse(bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile=profile,
        )["strict"])
        for text in (card.oracle_text.replace("different names", "the same name"),
                     card.oracle_text.replace("permanent cards", "permanent cards with mana value 2 or less"),
                     card.oracle_text.replace("your graveyard", "each graveyard")):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile=profile,
            ).status)
        with patch("quorune.compiler.graveyard_return_choice_templates.different_name_permanent_return_effect",
                   return_value={"op": OPERATION, "player": "$controller", "selection": {"schema_version": 99}}):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=profile,
            ).status)
