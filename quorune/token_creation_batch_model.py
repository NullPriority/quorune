"""Public committed-instruction facts distinct from individual token entries."""
from dataclasses import dataclass

TOKEN_CREATION_TRIGGER_CAPABILITY='trigger.event.token_creation'
TOKEN_CREATED_EVENT='token.created'
TOKEN_CREATION_BATCH_EVENT='tokens.created'


@dataclass(frozen=True,slots=True)
class TokenCreationBatchFact:
    creator: str
    quantity: int
    creature_quantity: int
    previous_quantity: int

    def __post_init__(self):
        if (type(self.creator) is not str or not self.creator
                or any(type(value) is not int or value<0 for value in
                    (self.quantity,self.creature_quantity,self.previous_quantity))
                or self.creature_quantity>self.quantity):
            raise ValueError('Token creation facts require a known creator and complete nonnegative quantities')

    @property
    def first_creation_this_turn(self):return self.quantity>0 and self.previous_quantity==0

    def to_context(self):
        return {'creator':self.creator,'quantity':self.quantity,'creature_quantity':self.creature_quantity,
            'previous_quantity':self.previous_quantity,'first_creation_this_turn':self.first_creation_this_turn}
