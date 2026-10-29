"""Exile links survive their source while retaining exact zone incarnations."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.errors import GameRuleError
from quorune.linked_ability_exile_model import LINKED_ABILITY_EXILE_ANNOTATION
from quorune.linked_ability_exile_state import (
    record_linked_ability_exile, current_linked_ability_exile, return_linked_ability_exile,
)
from quorune.linked_exile_return_model import ExiledObjectIdentity
from quorune.model import CardInstance


class LinkedAbilityExileStateTests(unittest.TestCase):
    def host(self):
        first = CardInstance('first','F1','model-first','First card','B','B','exile')
        second = CardInstance('second','F2','model-second','Second card','C','C','exile')
        return SimpleNamespace(state=SimpleNamespace(cards={row.object_id:row for row in (first,second)}))

    def identity(self, card):
        return ExiledObjectIdentity(card.object_id,card.ref,card.logical_object_id)

    def test_source_absence_and_early_return_do_not_create_a_duration(self):
        host = self.host(); source=ContinuousObjectIdentity('absent-source','source:0')
        first=host.state.cards['first']; identity=self.identity(first)
        self.assertEqual((),current_linked_ability_exile(host,source=source,ability_pair='pair:one'))
        record_linked_ability_exile(host,source=source,ability_pair='pair:one',objects=(identity,))
        self.assertEqual((identity,),current_linked_ability_exile(host,source=source,ability_pair='pair:one'))
        self.assertEqual('exile',first.zone)
        self.assertEqual((),current_linked_ability_exile(host,source=source,ability_pair='pair:other'))
        self.assertEqual((),current_linked_ability_exile(host,
            source=ContinuousObjectIdentity('absent-source','source:2'),ability_pair='pair:one'))

    def test_late_invalid_member_rejects_before_any_link_write(self):
        host=self.host();source=ContinuousObjectIdentity('source','source:0')
        first,second=host.state.cards.values(); second.zone='graveyard'
        with self.assertRaises(GameRuleError):record_linked_ability_exile(host,source=source,
            ability_pair='pair:one',objects=(self.identity(first),self.identity(second)))
        self.assertNotIn(LINKED_ABILITY_EXILE_ANNOTATION,first.annotations)

    def test_copied_entry_links_multiple_cards_and_does_not_follow_reentry(self):
        host=self.host();source=ContinuousObjectIdentity('source','source:0')
        first,second=host.state.cards.values()
        for card in (first,second):record_linked_ability_exile(host,source=source,
            ability_pair='pair:one',objects=(self.identity(card),))
        self.assertEqual(2,len(current_linked_ability_exile(host,source=source,ability_pair='pair:one')))
        first.zone_change_counter += 2
        self.assertEqual((self.identity(second),),current_linked_ability_exile(host,source=source,ability_pair='pair:one'))

    def test_linked_return_delegates_owner_control_and_actual_identities(self):
        host=self.host();source=ContinuousObjectIdentity('source','source:0')
        first=host.state.cards['first'];identity=self.identity(first)
        record_linked_ability_exile(host,source=source,ability_pair='pair:one',objects=(identity,))
        with patch('quorune.linked_ability_exile_state.return_linked_exiled_objects',return_value=['F1']) as returned:
            self.assertEqual(['F1'],return_linked_ability_exile(host,source=source,
                ability_pair='pair:one',actor='A',reason='return ability resolved'))
        effect=returned.call_args.args[1]
        self.assertEqual([identity.to_dict()],effect['objects'])
        self.assertEqual('owner',effect['spec']['controller'])
        self.assertEqual('immediate',effect['spec']['timing'])

    def test_cross_object_link_payload_is_rejected(self):
        host=self.host();source=ContinuousObjectIdentity('source','source:0')
        first,second=host.state.cards.values()
        record_linked_ability_exile(host,source=source,ability_pair='pair:one',objects=(self.identity(first),))
        second.annotations[LINKED_ABILITY_EXILE_ANNOTATION]=first.annotations[LINKED_ABILITY_EXILE_ANNOTATION]
        with self.assertRaises(GameRuleError):current_linked_ability_exile(host,source=source,ability_pair='pair:one')

    def test_empty_recording_still_validates_the_source_and_pair(self):
        host=self.host()
        for source,pair in ((None,'pair:one'),(ContinuousObjectIdentity('source','source:0'),'')):
            with self.assertRaises(GameRuleError):record_linked_ability_exile(host,source=source,ability_pair=pair,objects=())
