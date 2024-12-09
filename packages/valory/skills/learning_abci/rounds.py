# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2024 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""This package contains the rounds of LearningAbciApp."""

from enum import Enum
from typing import Dict, FrozenSet, Optional, Set, Tuple

from packages.valory.skills.abstract_round_abci.base import (
    AbciApp,
    AbciAppTransitionFunction,
    AppState,
    BaseSynchronizedData,
    CollectSameUntilThresholdRound,
    CollectionRound,
    DegenerateRound,
    DeserializedCollection,
    EventToTimeout,
    get_name,
)
from packages.valory.skills.learning_abci.payloads import (
    DataPullPayload,
    DecisionMakingPayload,
    TxPreparationPayload,
)


class Event(Enum):
    """LearningAbciApp Events"""

    DONE = "done"
    ERROR = "error"
    TRANSACT = "transact"
    NO_MAJORITY = "no_majority"
    ROUND_TIMEOUT = "round_timeout"


class SynchronizedData(BaseSynchronizedData):
    """
    Class to represent the synchronized data.

    This data is replicated by the tendermint application, so all the agents share the same data.
    """

    def _get_deserialized(self, key: str) -> DeserializedCollection:
        """Strictly get a collection and return it deserialized."""
        serialized = self.db.get_strict(key)
        return CollectionRound.deserialize_collection(serialized)

    @property
    def price(self) -> Optional[float]:
        """Get the token price."""
        return self.db.get("price", None)

    @property
    def price_ipfs_hash(self) -> Optional[str]:
        """Get the price_ipfs_hash."""
        return self.db.get("price_ipfs_hash", None)

    @property
    def native_balance(self) -> Optional[float]:
        """Get the native balance."""
        return self.db.get("native_balance", None)

    @property
    def erc20_balance(self) -> Optional[float]:
        """Get the erc20 balance."""
        return self.db.get("erc20_balance", None)

    @property
    def participant_to_data_round(self) -> DeserializedCollection:
        """Agent to payload mapping for the DataPullRound."""
        return self._get_deserialized("participant_to_data_round")

    @property
    def most_voted_tx_hash(self) -> Optional[float]:
        """Get the token most_voted_tx_hash."""
        return self.db.get("most_voted_tx_hash", None)

    @property
    def participant_to_tx_round(self) -> DeserializedCollection:
        """Get the participants to the tx round."""
        return self._get_deserialized("participant_to_tx_round")

    @property
    def tx_submitter(self) -> str:
        """Get the round that submitted a tx to transaction_settlement_abci."""
        return str(self.db.get_strict("tx_submitter"))

    @property
    def base_holders(self) -> int:
        """Get the base holders."""
        return int(self.db.get("base_holders", 0))

    @property
    def arbitrum_holders(self) -> int:
        """Get the arbitrum holders."""
        return int(self.db.get("arbitrum_holders", 0))

    @property
    def bet_details_ipfs_hash(self) -> Optional[str]:
        """Get the bet details ipfs hash."""
        return self.db.get("bet_details_ipfs_hash", None)

    @property
    def winner(self) -> str:
        """Get the winner."""
        return str(self.db.get("winner", None))

    @property
    def prize_amount(self) -> int:
        """Get the prize amount."""
        return int(self.db.get("prize_amount", 0))

    @property
    def participant_to_decision_making_round(self) -> DeserializedCollection:
        """Get the participants to the decision making round."""
        return self._get_deserialized("participant_to_decision_making_round")

    @property
    def result(self) -> str:
        """Get the result."""
        return str(self.db.get("result", ""))

    @property
    def bet_id(self) -> int:
        """Get the bet id."""
        return int(self.db.get("bet_id", 0))


class DataPullRound(CollectSameUntilThresholdRound):
    """DataPullRound"""

    payload_class = DataPullPayload
    synchronized_data_class = SynchronizedData
    done_event = Event.DONE
    no_majority_event = Event.NO_MAJORITY

    # Collection key specifies where in the synchronized data the agento to payload mapping will be stored
    collection_key = get_name(SynchronizedData.participant_to_data_round)

    # Selection key specifies how to extract all the different objects from each agent's payload
    # and where to store it in the synchronized data. Notice that the order follows the same order
    # from the payload class.
    selection_key = (
        get_name(SynchronizedData.base_holders),
        get_name(SynchronizedData.arbitrum_holders),
        get_name(SynchronizedData.bet_details_ipfs_hash),
        get_name(SynchronizedData.bet_id)
    )

    # Event.ROUND_TIMEOUT  # this needs to be referenced for static checkers


class DecisionMakingRound(CollectionRound):
    """DecisionMakingRound"""

    payload_class = DecisionMakingPayload
    synchronized_data_class = SynchronizedData

    collection_key = get_name(SynchronizedData.participant_to_decision_making_round)
    selection_key = (get_name(SynchronizedData.result),
                     get_name(SynchronizedData.prize_amount))

    def end_block(self) -> Optional[Tuple[BaseSynchronizedData, Event]]:
        """Process the end of the block."""
        if not self.collection:
            return None

        # Get first payload since we don't need consensus
        payload = next(iter(self.collection.values()))

        # Update synchronized data
        synchronized_data = self.synchronized_data.update(
            result=payload.result,
            prize_amount=int(payload.prize_amount)
        )

        # Determine event based on prize amount
        event = Event.TRANSACT if int(payload.prize_amount) > 0 else Event.DONE

        return synchronized_data, event


class TxPreparationRound(CollectSameUntilThresholdRound):
    """TxPreparationRound"""

    payload_class = TxPreparationPayload
    synchronized_data_class = SynchronizedData
    done_event = Event.DONE
    no_majority_event = Event.NO_MAJORITY
    collection_key = get_name(SynchronizedData.participant_to_tx_round)
    selection_key = (
        get_name(SynchronizedData.tx_submitter),
        get_name(SynchronizedData.most_voted_tx_hash),
    )

    # Event.ROUND_TIMEOUT  # this needs to be referenced for static checkers


class FinishedDecisionMakingRound(DegenerateRound):
    """FinishedDecisionMakingRound"""


class FinishedTxPreparationRound(DegenerateRound):
    """FinishedLearningRound"""


class LearningAbciApp(AbciApp[Event]):
    """LearningAbciApp"""

    initial_round_cls: AppState = DataPullRound
    initial_states: Set[AppState] = {
        DataPullRound,
    }
    transition_function: AbciAppTransitionFunction = {
        DataPullRound: {
            Event.NO_MAJORITY: DataPullRound,
            Event.ROUND_TIMEOUT: DataPullRound,
            Event.DONE: DecisionMakingRound,
        },
        DecisionMakingRound: {
            Event.NO_MAJORITY: DecisionMakingRound,
            Event.ROUND_TIMEOUT: DecisionMakingRound,
            Event.DONE: FinishedDecisionMakingRound,
            Event.ERROR: FinishedDecisionMakingRound,
            Event.TRANSACT: TxPreparationRound,
        },
        TxPreparationRound: {
            Event.NO_MAJORITY: TxPreparationRound,
            Event.ROUND_TIMEOUT: TxPreparationRound,
            Event.DONE: FinishedTxPreparationRound,
        },
        FinishedDecisionMakingRound: {},
        FinishedTxPreparationRound: {},
    }
    final_states: Set[AppState] = {
        FinishedDecisionMakingRound,
        FinishedTxPreparationRound,
    }
    event_to_timeout: EventToTimeout = {}
    cross_period_persisted_keys: FrozenSet[str] = frozenset()
    db_pre_conditions: Dict[AppState, Set[str]] = {
        DataPullRound: set(),
    }
    db_post_conditions: Dict[AppState, Set[str]] = {
        FinishedDecisionMakingRound: set(),
        FinishedTxPreparationRound: {get_name(SynchronizedData.most_voted_tx_hash)},
    }
