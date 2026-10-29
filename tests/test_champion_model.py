"""Independent CR702.72/607 identity and untargeted-choice distinctions."""
from dataclasses import replace, FrozenInstanceError
import json
import unittest

from quorune.champion_model import ChampionSpec, champion_candidates
from quorune.compiler.champion_templates import champion_spec
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.linked_ability_exile_model import LinkedAbilityExile, linked_exile_objects
from quorune.linked_exile_return_model import ExiledObjectIdentity
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult


class ChampionModelTests(unittest.TestCase):
    def row(self, ref, *, types=('creature',), subtypes=('kithkin',), controller='A', owner='B'):
        return ObjectQueryResult('object:'+ref, ref, 'Model permanent', owner, controller,
            'battlefield', types=types, subtypes=subtypes, logical_object_id='object:'+ref+':0')

    def test_subtype_alternatives_do_not_impose_a_creature_type(self):
        creature = self.row('creature')
        kindred = self.row('kindred', types=('kindred','enchantment'))
        source = ContinuousObjectIdentity('source','source:0')
        self.assertEqual((creature,kindred), champion_candidates((kindred,creature),
            spec=champion_spec('Champion a Kithkin'), actor='A', source=source))
        self.assertEqual((creature,), champion_candidates((kindred,creature),
            spec=champion_spec('Champion a creature'), actor='A', source=source))
        choices = (self.row('tree',subtypes=('treefolk',)),self.row('warrior',subtypes=('warrior',)),
                   self.row('unrelated',subtypes=('elf',)))
        self.assertEqual(('tree','warrior'), tuple(row.ref for row in champion_candidates(choices,
            spec=champion_spec('Champion a Treefolk or Warrior'), actor='A', source=source)))

    def test_another_means_another_incarnation_and_control_is_not_ownership(self):
        old = self.row('source')
        returned = replace(old,logical_object_id='object:source:2')
        controlled = self.row('borrowed', owner='C')
        enemy = self.row('enemy',controller='B',owner='A')
        phased = replace(self.row('phased'),phased_out=True)
        protected = replace(self.row('protected'),keywords=('hexproof','shroud','protection from everything'))
        source = ContinuousObjectIdentity(old.object_id,old.logical_object_id)
        selected = champion_candidates((old,returned,controlled,enemy,phased,protected),
            spec=champion_spec('Champion a creature'),actor='A',source=source)
        self.assertEqual(('borrowed','protected','source'),tuple(row.ref for row in selected))
        self.assertEqual('object:source:2',selected[-1].logical_object_id)

    def test_spec_codec_rejects_unknown_qualifiers_and_versions(self):
        spec = champion_spec('Champion an Elemental')
        self.assertEqual(spec, ChampionSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        for text in ('Champion another creature','Champion a Creature you own',
                     'Champion an unrepresentedkind','Champion a Goblin or Goblin'):
            self.assertIsNone(champion_spec(text))
        for changes in ({'schema_version':True},{'schema_version':2},{'unknown':True}):
            with self.assertRaises(ValueError):ChampionSpec.from_dict({**spec.to_dict(),**changes})
        with self.assertRaises(ValueError):
            ChampionSpec(ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),controller='A'))


class LinkedAbilityExileModelTests(unittest.TestCase):
    def test_pair_and_source_and_actual_exile_incarnation_all_must_match(self):
        source = ContinuousObjectIdentity('source','source:0')
        one = ExiledObjectIdentity('one','O1','one:1')
        two = ExiledObjectIdentity('two','O2','two:1')
        records = (LinkedAbilityExile(source,'pair:first',two), LinkedAbilityExile(source,'pair:first',one),
                   LinkedAbilityExile(source,'pair:other',one),
                   LinkedAbilityExile(ContinuousObjectIdentity('source','source:2'),'pair:first',one))
        self.assertEqual((one,two),linked_exile_objects(records,source=source,
            ability_pair='pair:first',current_exiled=(two,one)))
        self.assertEqual((two,),linked_exile_objects(records,source=source,
            ability_pair='pair:first',current_exiled=(two,replace(one,logical_object_id='one:3'))))
        self.assertEqual((),linked_exile_objects(records,source=source,
            ability_pair='unrelated',current_exiled=(one,two)))

    def test_link_codec_is_closed_frozen_and_detached(self):
        link = LinkedAbilityExile(ContinuousObjectIdentity('source','source:0'),
            'pair:first',ExiledObjectIdentity('one','O1','one:1'))
        raw = json.loads(json.dumps(link.to_dict()))
        self.assertEqual(link,LinkedAbilityExile.from_dict(raw))
        raw['source']['logical_object_id']='source:2'
        self.assertEqual('source:0',link.source.logical_object_id)
        with self.assertRaises(FrozenInstanceError):link.ability_pair='changed'
        for changes in ({'schema_version':True},{'schema_version':2},{'ability_pair':''},{'unknown':1}):
            with self.assertRaises(ValueError):LinkedAbilityExile.from_dict({**link.to_dict(),**changes})

    def test_ambiguous_current_exile_facts_do_not_choose_an_incarnation(self):
        source=ContinuousObjectIdentity('source','source:0')
        one=ExiledObjectIdentity('one','O1','one:1')
        with self.assertRaises(ValueError):linked_exile_objects((),source=source,
            ability_pair='pair:first',current_exiled=(one,replace(one,logical_object_id='one:3')))
