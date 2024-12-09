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

"""This package contains round behaviours of LearningAbciApp."""

import json
from abc import ABC
from pathlib import Path
from tempfile import mkdtemp
from typing import Dict, Generator, Optional, Set, Tuple, Type, cast

from packages.valory.contracts.erc20.contract import ERC20
from packages.valory.contracts.gnosis_safe.contract import (
    GnosisSafeContract,
    SafeOperation,
)

from packages.valory.contracts.betchain.contract import BetChain as BettingContract

from packages.valory.contracts.multisend.contract import (
    MultiSendContract,
    MultiSendOperation,
)
from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.protocols.ledger_api import LedgerApiMessage
from packages.valory.skills.abstract_round_abci.base import AbstractRound
from packages.valory.skills.abstract_round_abci.behaviours import (
    AbstractRoundBehaviour,
    BaseBehaviour,
)
from packages.valory.skills.abstract_round_abci.io_.store import SupportedFiletype
from packages.valory.skills.learning_abci.models import (
    CoingeckoSpecs,
    Params,
    SharedState,
)
from packages.valory.skills.learning_abci.payloads import (
    DataPullPayload,
    DecisionMakingPayload,
    TxPreparationPayload,
)
from packages.valory.skills.learning_abci.rounds import (
    DataPullRound,
    DecisionMakingRound,
    Event,
    LearningAbciApp,
    SynchronizedData,
    TxPreparationRound,
)
from packages.valory.skills.transaction_settlement_abci.payload_tools import (
    hash_payload_to_hex,
)
from packages.valory.skills.transaction_settlement_abci.rounds import TX_HASH_LENGTH


# Define some constants
ZERO_VALUE = 0
HTTP_OK = 200
GNOSIS_CHAIN_ID = "gnosis"
EMPTY_CALL_DATA = b"0x"
SAFE_GAS = 0
VALUE_KEY = "value"
TO_ADDRESS_KEY = "to_address"
METADATA_FILENAME = "metadata.json"


class LearningBaseBehaviour(BaseBehaviour, ABC):  # pylint: disable=too-many-ancestors
    """Base behaviour for the learning_abci behaviours."""

    @property
    def params(self) -> Params:
        """Return the params. Configs go here"""
        return cast(Params, super().params)

    @property
    def synchronized_data(self) -> SynchronizedData:
        """Return the synchronized data. This data is common to all agents"""
        return cast(SynchronizedData, super().synchronized_data)

    @property
    def local_state(self) -> SharedState:
        """Return the local state of this particular agent."""
        return cast(SharedState, self.context.state)

    @property
    def coingecko_specs(self) -> CoingeckoSpecs:
        """Get the Coingecko api specs."""
        return self.context.coingecko_specs

    @property
    def metadata_filepath(self) -> str:
        """Get the temporary filepath to the metadata."""
        return str(Path(mkdtemp()) / METADATA_FILENAME)

    def get_sync_timestamp(self) -> float:
        """Get the synchronized time from Tendermint's last block."""
        now = cast(
            SharedState, self.context.state
        ).round_sequence.last_round_transition_timestamp.timestamp()

        return now
    
    def get_bet_details_from_ipfs(self) -> Generator[None, None, Optional[Dict]]:
        """Get bet details from IPFS"""
        ipfs_hash = self.synchronized_data.bet_details_ipfs_hash
        if not ipfs_hash:
            self.context.logger.error("No IPFS hash available")
            return None
            
        try:
            bet_details = yield from self.get_from_ipfs(
                ipfs_hash=ipfs_hash,
                filetype=SupportedFiletype.JSON
            )
            
            if not bet_details or "bet_details" not in bet_details:
                self.context.logger.error("Invalid bet details format")
                return None
                
            return bet_details["bet_details"]
        except Exception as e:
            self.context.logger.error(f"Failed to get bet details from IPFS: {e}")
            return None



class DataPullBehaviour(LearningBaseBehaviour):  # pylint: disable=too-many-ancestors
    """This behaviours pulls token prices from API endpoints and reads the native balance of an account"""

    matching_round: Type[AbstractRound] = DataPullRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""

        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            sender = self.context.agent_address
            bet_ipfs_hash = None  # Initialize hash variable
            bet_id = None  # Initialize bet_id variable
            
            # Get total and resolved bets
            total_bets = yield from self.get_total_bets()
            if total_bets is None:
                self.context.logger.error("Failed to get total bets.")
            else:
                self.context.logger.info(f"Total bets: {total_bets}")

            resolved_bets = yield from self.get_resolved_bets()
            if resolved_bets is None:
                self.context.logger.error("Failed to get resolved bets.")
            else:
                self.context.logger.info(f"Resolved bets: {resolved_bets}")

            # Calculate the first pending bet using total and resolved bets
            if total_bets is not None and resolved_bets is not None:
                if resolved_bets < total_bets:
                    bet_id = resolved_bets + 1  # Set the bet_id
                    self.context.logger.info(f"First pending bet: {bet_id}")
                    
                    # Only get bet details if we have a valid pending bet
                    bet_details = yield from self.get_bet_details(bet_id)
                    if bet_details is not None:
                        bet_ipfs_hash = yield from self.store_bet_details_to_ipfs(bet_details)

            # Get the number of token holders
            token_holders = yield from self.get_token_holders()
            arbitrum_holders = token_holders.get("arbitrum", 0)
            base_holders = token_holders.get("base", 0)

            # Prepare the payload to be shared with other agents
            payload = DataPullPayload(
                sender=sender,
                arbitrum_holders=arbitrum_holders,
                base_holders=base_holders,
                bet_details_ipfs_hash=bet_ipfs_hash,
                bet_id=bet_id  # Add bet_id to payload
            )

            # Send the payload to all agents and mark the behaviour as done
            with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
                yield from self.send_a2a_transaction(payload)
                yield from self.wait_until_round_end()

            self.set_done()

    def get_token_holders(self) -> Generator[None, None, Optional[Dict[str, int]]]:
        """Get the number of token holders from Blockscout for both Arbitrum and Base"""

        holders = {"arbitrum": 0, "base": 0}

        # URLs and headers for both APIs
        urls = {
            "arbitrum": "https://arbitrum.blockscout.com/api/v2/tokens/0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
            "base": "https://base.blockscout.com/api/v2/tokens/0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
        }
        headers = {"accept": "application/json"}

        for network, url in urls.items():
            # Make the HTTP request to Blockscout API
            response = yield from self.get_http_response(
                method="GET", url=url, headers=headers
            )

            # Handle HTTP errors
            if response.status_code != HTTP_OK:
                self.context.logger.error(
                    f"Error while pulling the number of holders from Blockscout ({network}): {response.body}"
                )
                continue

            # Load the response
            api_data = json.loads(response.body)
            holders[network] = int(api_data["holders"])

        return holders

    def get_bet_details(self, bet_id: int) -> Generator[None, None, Optional[Dict]]:
        """Get the details of a bet from the BettingContract."""
        self.context.logger.info(
            f"Getting bet details for bet ID {bet_id} from contract {self.params.betchain_contract_address}"
        )

        # Use the contract api to interact with the BettingContract
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self.params.betchain_contract_address,
            contract_id=str(BettingContract.contract_id),
            contract_callable="get_bet_details",
            bet_id=bet_id,
            chain_id=GNOSIS_CHAIN_ID,
        )

        # Check that the response is what we expect
        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(
                f"Error while retrieving bet details for bet ID {bet_id}: {response_msg}"
            )
            return None

        bet_details = response_msg.state.body.get("data", None)

        # Ensure that the bet details are not None
        if bet_details is None:
            self.context.logger.error(
                f"Error while retrieving bet details for bet ID {bet_id}: {response_msg}"
            )
            return None

        self.context.logger.info(f"Bet details for bet ID {bet_id}: {bet_details}")
        return bet_details

    def get_total_bets(self) -> Generator[None, None, Optional[int]]:
        """Get the total number of bets from the BettingContract."""
        self.context.logger.info(
            f"Getting total bets from contract {self.params.betchain_contract_address}"
        )

        # Use the contract api to interact with the BettingContract
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self.params.betchain_contract_address,
            contract_id=str(BettingContract.contract_id),
            contract_callable="get_total_bets",
            chain_id=GNOSIS_CHAIN_ID,
        )

        # Check that the response is what we expect
        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(
                f"Error while retrieving total bets: {response_msg}"
            )
            return None

        total_bets = response_msg.state.body.get("data", None)

        # Ensure that the total bets is not None
        if total_bets is None:
            self.context.logger.error(
                f"Error while retrieving total bets: {response_msg}"
            )
            return None

        self.context.logger.info(f"Total bets: {total_bets}")
        return total_bets

    def get_resolved_bets(self) -> Generator[None, None, Optional[int]]:
        """Get the number of resolved bets from the BettingContract."""
        self.context.logger.info(
            f"Getting resolved bets from contract {self.params.betchain_contract_address}"
        )

        # Use the contract api to interact with the BettingContract  
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self.params.betchain_contract_address,
            contract_id=str(BettingContract.contract_id),
            contract_callable="get_resolved_bets", 
            chain_id=GNOSIS_CHAIN_ID,
        )

        # Check that the response is what we expect
        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(
                f"Error while retrieving resolved bets: {response_msg}"
            )
            return None

        resolved_bets = response_msg.state.body.get("data", None)

        # Ensure that the resolved bets is not None
        if resolved_bets is None:
            self.context.logger.error(
                f"Error while retrieving resolved bets: {response_msg}"
            )
            return None

        self.context.logger.info(f"Resolved bets: {resolved_bets}")
        return resolved_bets

    def store_bet_details_to_ipfs(self, bet_details: Dict) -> Generator[None, None, Optional[str]]:
        """Store bet details in IPFS"""
        # Create metadata object with timestamp and bet details
        metadata = {
            "timestamp": self.get_sync_timestamp(),
            "bet_details": bet_details
        }
        
        # Store metadata in IPFS
        ipfs_hash = yield from self.send_to_ipfs(
            filename=self.metadata_filepath,
            obj=metadata, 
            filetype=SupportedFiletype.JSON
        )
        
        if ipfs_hash:
            self.context.logger.info(
                f"Bet details stored in IPFS: https://gateway.autonolas.tech/ipfs/{ipfs_hash}"
            )
        else:
            self.context.logger.error("Failed to store bet details in IPFS")
            
        return ipfs_hash


class DecisionMakingBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """DecisionMakingBehaviour"""

    matching_round: Type[AbstractRound] = DecisionMakingRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""
        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            sender = self.context.agent_address
            
            # Get bet details from IPFS
            bet_details = yield from self.get_bet_details_from_ipfs()
            if bet_details is None:
                # Handle error case
                payload = DecisionMakingPayload(
                    sender=sender,
                    event=Event.ERROR.value,
                    result="lose",  # Default to lose on error
                    prize_amount=0
                )
            else:
                # Calculate result and prize
                result, prize_amount = self.determine_winner_and_prize(bet_details)
                
                # Create proper payload object
                payload = DecisionMakingPayload(
                    sender=sender,
                    event=Event.TRANSACT.value if prize_amount > 0 else Event.DONE.value,
                    result=result,  # Using result instead of winner
                    prize_amount=str(prize_amount)  # Convert to string for payload
                )

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def determine_winner_and_prize(self, bet_details: list) -> Tuple[str, int]:
        """Determine result and calculate prize amount based on holder counts."""
        
        # Get holder counts from synchronized data
        arbitrum_holders = self.synchronized_data.arbitrum_holders
        base_holders = self.synchronized_data.base_holders
        
        # Get user's choice and bet amount from bet details list
        user_choice = bet_details[0] if bet_details else 0
        bet_amount = bet_details[1] if len(bet_details) > 1 else 0
        
        # Convert choice to chain selection (1 for arbitrum, 2 for base)
        user_selected = "arbitrum" if user_choice == 1 else "base"
        
        # Determine actual winner based on holder counts
        actual_winner = "arbitrum" if arbitrum_holders > base_holders else "base"
        
        # Determine if user won or lost
        result = "win" if user_selected == actual_winner else "lose"
        
        # Calculate prize amount
        holder_difference = abs(arbitrum_holders - base_holders)
        prize_multiplier = holder_difference / 1000  # 1 wei per 1000 holder difference
        prize_amount = int(bet_amount * prize_multiplier) if result == "win" else 0
        
        self.context.logger.info(
            f"User chose {user_selected}, actual winner was {actual_winner}, "
            f"result is {result}, prize amount is {prize_amount} wei"
        )
        
        return result, prize_amount


class TxPreparationBehaviour(
    LearningBaseBehaviour
):  # pylint: disable=too-many-ancestors
    """TxPreparationBehaviour"""

    matching_round: Type[AbstractRound] = TxPreparationRound

    def async_act(self) -> Generator:
        """Do the act, supporting asynchronous execution."""
        with self.context.benchmark_tool.measure(self.behaviour_id).local():
            sender = self.context.agent_address

        # Get prize amount from synchronized data
            try:
                prize_amount = int(self.synchronized_data.prize_amount)
                if prize_amount < 0:
                    self.context.logger.error("Invalid prize amount")
                    return None
            except (ValueError, TypeError):
                self.context.logger.error("Failed to parse prize amount")
                return None

            # Get prize transfer data
            prize_tx = yield from self.get_prize_transfer_tx()
            if not prize_tx:
                self.context.logger.error("Failed to prepare prize transfer tx")
                return None

            # Get bet resolution data
            resolve_tx = yield from self.get_resolve_bet_tx()
            if not resolve_tx:
                self.context.logger.error("Failed to prepare resolve bet tx")
                return None

            # Combine transactions for multisend
            multi_send_txs = [prize_tx, resolve_tx]

            # Get multisend tx data
            multisend_msg = yield from self.get_contract_api_response(
                performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
                contract_address=self.params.multisend_address,
                contract_id=str(MultiSendContract.contract_id),
                contract_callable="get_tx_data",
                multi_send_txs=multi_send_txs,
                chain_id=GNOSIS_CHAIN_ID,
            )

            if multisend_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
                self.context.logger.error(f"Invalid multisend response: {multisend_msg}")
                return None

            multisend_data = multisend_msg.raw_transaction.body.get("data")
            cast(str, multisend_data)
      
            multisend_data = bytes.fromhex(multisend_data[2:])

            tx_hash = yield from self._build_safe_tx_hash(
                to_address=self.params.multisend_address,
                value=prize_amount,
                data=multisend_data,
                operation=SafeOperation.DELEGATE_CALL.value
            )

            if not tx_hash:
                self.context.logger.error("Failed to build safe tx hash")
                return None

            payload = TxPreparationPayload(
                sender=sender,
                tx_hash=tx_hash,
            )

        with self.context.benchmark_tool.measure(self.behaviour_id).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def get_prize_transfer_tx(self) -> Generator[None, None, Optional[Dict]]:
        """Get prize transfer transaction data."""
        self.context.logger.info("Preparing prize transfer transaction")

        try:
            prize_amount = int(self.synchronized_data.prize_amount)
            if prize_amount < 0:
                self.context.logger.error("Invalid prize amount")
                return None
        except (ValueError, TypeError):
            self.context.logger.error("Failed to parse prize amount")
            return None

        # Get winner address from bet details
        bet_details = yield from self.get_bet_details_from_ipfs()
        winner_address = bet_details[0] if bet_details else None
        if not winner_address:
            self.context.logger.error("Failed to get winner address")
            return None

        return {
            "operation": MultiSendOperation.CALL,
            "to": winner_address,
            "value": prize_amount,
            "data": b""
        }

    def get_resolve_bet_tx(self) -> Generator[None, None, Optional[Dict]]:
        """Get bet resolution transaction data."""
        self.context.logger.info("Preparing bet resolution transaction")

        # Validate bet ID and result
        bet_id = self.synchronized_data.bet_id
        if bet_id is None:
            self.context.logger.error("No bet ID available")
            return None

        result = 1 if self.synchronized_data.result == "win" else 0
        ipfs_hash = self.synchronized_data.bet_details_ipfs_hash

        # Prepare contract call
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.params.betchain_contract_address,
            contract_id=str(BettingContract.contract_id),
            contract_callable="resolve_bet",
            bet_id=bet_id,
            result=result,
            ipfs_hash=ipfs_hash,
            chain_id=GNOSIS_CHAIN_ID,
        )

        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"Could not get resolve bet hash. "
                f"Expected: {ContractApiMessage.Performative.RAW_TRANSACTION.value}, "
                f"Actual: {response_msg.performative.value}"
            )
            return None

        self.context.logger.info(f"Resolve bet response msg is {response_msg}")
        tx_data = cast(bytes, response_msg.raw_transaction.body.get("data"))
        self.context.logger.info(f"Resolve bet tx data is {tx_data}")

        if not tx_data:
            self.context.logger.error("No transaction data received")
            return None

        return {
            "operation": MultiSendOperation.CALL,
            "to": self.params.betchain_contract_address, 
            "value": 0,
            "data": tx_data.hex()
        }

    def get_tx_hash(self) -> Generator[None, None, Optional[str]]:
        """Get the transaction hash for resolving the bet"""
        self.context.logger.info("Preparing bet resolution transaction")
        
        # Get the resolve bet data
        resolve_bet_data_hex = yield from self.get_resolve_bet_data()
        if resolve_bet_data_hex is None:
            return None
            
        # Prepare safe transaction for bet resolution
        safe_tx_hash = yield from self._build_safe_tx_hash(
            to_address=self.params.betchain_contract_address,
            value=0,  # No value transfer needed for resolution
            data=bytes.fromhex(resolve_bet_data_hex)
        )
        
        self.context.logger.info(f"Bet resolution transaction hash: {safe_tx_hash}")
        return safe_tx_hash

    def get_native_transfer_safe_tx_hash(self) -> Generator[None, None, Optional[str]]:
        """Prepare a native safe transaction"""

        # Transaction data
        # This method is not a generator, therefore we don't use yield from
        data = self.get_native_transfer_data()

        # Prepare safe transaction
        safe_tx_hash = yield from self._build_safe_tx_hash(**data)
        self.context.logger.info(f"Native transfer hash is {safe_tx_hash}")

        return safe_tx_hash

    def get_native_transfer_data(self) -> Dict:
        """Get the native transaction data"""
        # Send 1 wei to the recipient
        data = {VALUE_KEY: 1, TO_ADDRESS_KEY: self.params.transfer_target_address}
        self.context.logger.info(f"Native transfer data is {data}")
        return data

    def get_erc20_transfer_safe_tx_hash(self) -> Generator[None, None, Optional[str]]:
        """Prepare an ERC20 safe transaction"""

        # Transaction data
        data_hex = yield from self.get_erc20_transfer_data()

        # Check for errors
        if data_hex is None:
            return None

        # Prepare safe transaction
        safe_tx_hash = yield from self._build_safe_tx_hash(
            to_address=self.params.transfer_target_address, data=bytes.fromhex(data_hex)
        )

        self.context.logger.info(f"ERC20 transfer hash is {safe_tx_hash}")

        return safe_tx_hash

    def get_erc20_transfer_data(self) -> Generator[None, None, Optional[str]]:
        """Get the ERC20 transaction data"""

        self.context.logger.info("Preparing ERC20 transfer transaction")

        # Use the contract api to interact with the ERC20 contract
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.params.olas_token_address,
            contract_id=str(ERC20.contract_id),
            contract_callable="build_transfer_tx",
            recipient=self.params.transfer_target_address,
            amount=1,
            chain_id=GNOSIS_CHAIN_ID,
        )

        # Check that the response is what we expect
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"Error while retrieving the balance: {response_msg}"
            )
            return None

        data_bytes: Optional[bytes] = response_msg.raw_transaction.body.get(
            "data", None
        )

        # Ensure that the data is not None
        if data_bytes is None:
            self.context.logger.error(
                f"Error while preparing the transaction: {response_msg}"
            )
            return None

        data_hex = data_bytes.hex()
        self.context.logger.info(f"ERC20 transfer data is {data_hex}")
        return data_hex

    def get_multisend_safe_tx_hash(self) -> Generator[None, None, Optional[str]]:
        """Get a multisend transaction hash for resolve bet and native transfer"""
        multi_send_txs = []

        # Get resolve bet transaction data
        resolve_bet_data_hex = yield from self.get_resolve_bet_data()
        if resolve_bet_data_hex is None:
            self.context.logger.error("Could not get resolve bet transaction data")
            return None

        # Add resolve bet transaction
        multi_send_txs.append({
            "operation": MultiSendOperation.CALL,
            "to": self.params.betchain_contract_address,
            "value": ZERO_VALUE,
            "data": bytes.fromhex(resolve_bet_data_hex),
        })

        # Get winner prize transfer hash
        winner_tx_hash = yield from self.get_winner_transfer_tx_hash()
        if winner_tx_hash is not None:
            bet_details = yield from self.get_bet_details_from_ipfs()
            if bet_details:
                # Add winner prize transfer transaction
                multi_send_txs.append({
                    "operation": MultiSendOperation.CALL,
                    "to": bet_details[2],  # winner address
                    "value": int(self.synchronized_data.prize_amount),
                    "data": EMPTY_CALL_DATA,
                })

        # Prepare multisend transaction
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.params.multisend_address,
            contract_id=str(MultiSendContract.contract_id),
            contract_callable="get_tx_data",
            multi_send_txs=multi_send_txs,
            chain_id=GNOSIS_CHAIN_ID,
        )

        if contract_api_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"Could not get Multisend tx hash. "
                f"Expected: {ContractApiMessage.Performative.RAW_TRANSACTION.value}, "
                f"Actual: {contract_api_msg.performative.value}"
            )
            return None

        # Extract multisend data and strip 0x prefix
        multisend_data = cast(str, contract_api_msg.raw_transaction.body["data"])[2:]
        self.context.logger.info(f"Multisend data is {multisend_data}")

        # Prepare Safe transaction using multisend
        safe_tx_hash = yield from self._build_safe_tx_hash(
            to_address=self.params.multisend_address,
            value=ZERO_VALUE,
            data=bytes.fromhex(multisend_data),
            operation=SafeOperation.DELEGATE_CALL.value,
        )
        return safe_tx_hash

    def _build_safe_tx_hash(
        self,
        to_address: str,
        value: int = ZERO_VALUE,
        data: bytes = EMPTY_CALL_DATA,
        operation: int = SafeOperation.CALL.value,
    ) -> Generator[None, None, Optional[str]]:
        """Build safe transaction hash."""
        
        self.context.logger.info(f"Building Safe tx hash for {to_address}")

        # Get safe transaction hash from contract
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,
            contract_address=self.synchronized_data.safe_contract_address,
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="get_raw_safe_transaction_hash",
            to_address=to_address,
            value=value,
            data=data,
            safe_tx_gas=SAFE_GAS,
            chain_id=GNOSIS_CHAIN_ID,
            operation=operation,
        )

        if response_msg.performative != ContractApiMessage.Performative.STATE:
            self.context.logger.error(f"Invalid response: {response_msg}")
            return None

        # Get hash from response
        safe_tx_hash = response_msg.state.body.get("tx_hash")
        self.context.logger.info(f"Raw safe_tx_hash: {safe_tx_hash}")
        
        if not safe_tx_hash:
            self.context.logger.error("No tx hash in response")
            return None

        cast(str, safe_tx_hash)
        
        if safe_tx_hash.startswith("0x"):
            safe_tx_hash = safe_tx_hash[2:]
            #     # Convert hex string to bytes
            # safe_tx_hash_bytes = bytes.fromhex(safe_tx_hash.zfill(64))  # Ensure 32 bytes (64 hex chars)
    
            # Generate final hash
            tx_hash = hash_payload_to_hex(
                safe_tx_hash=safe_tx_hash,
                ether_value=value,
                safe_tx_gas=SAFE_GAS,
                to_address=to_address,
                data=data,
                operation=operation,
            )
            self.context.logger.info(f"Generated tx hash: {tx_hash}")
            return tx_hash

    def get_resolve_bet_data(self) -> Generator[None, None, Optional[str]]:
        """Get the resolve bet transaction data"""
        self.context.logger.info("Preparing resolve bet transaction")
        
        # Get bet ID and result from synchronized data
        bet_id = self.synchronized_data.bet_id
        result = 1 if self.synchronized_data.result == "win" else 0
        ipfs_hash = self.synchronized_data.bet_details_ipfs_hash

        if not ipfs_hash:
            self.context.logger.error("No IPFS hash available for bet resolution")
            return None

        # Use the contract api to interact with the BettingContract
        response_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,
            contract_address=self.params.betchain_contract_address,
            contract_id=str(BettingContract.contract_id),
            contract_callable="resolve_bet",
            bet_id=bet_id,
            result=result,
            ipfs_hash=ipfs_hash,
            chain_id=GNOSIS_CHAIN_ID,
        )

        # Check response
        if response_msg.performative != ContractApiMessage.Performative.RAW_TRANSACTION:
            self.context.logger.error(
                f"Error while preparing resolve bet transaction: {response_msg}"
            )
            return None

        data_bytes: Optional[bytes] = response_msg.raw_transaction.body.get("data", None)
        if data_bytes is None:
            self.context.logger.error("No data returned for resolve bet transaction")
            return None

        data_hex = data_bytes.hex()
        self.context.logger.info(f"Resolve bet data is {data_hex}")
        return data_hex
    
    def get_winner_transfer_tx_hash(self) -> Generator[None, None, Optional[str]]:
        """Get transaction hash for transferring prize to winner"""
        try:
            prize_amount = int(self.synchronized_data.prize_amount)
            if prize_amount < 0:
                self.context.logger.error("Invalid prize amount")
                return None
        except (ValueError, TypeError):
            self.context.logger.error("Failed to parse prize amount")
            return None

        bet_details = yield from self.get_bet_details_from_ipfs()
        
        if not bet_details or prize_amount <= 0:
            self.context.logger.error("No prize amount or bet details available")
            return None
            
        # Get winner address from bet details
        winner_address = bet_details[2] if len(bet_details) > 2 else None
        if not winner_address:
            self.context.logger.error("No winner address found in bet details")
            return None

        # Prepare safe transaction for prize transfer
        safe_tx_hash = yield from self._build_safe_tx_hash(
            to_address=winner_address,
            value=prize_amount,
            data=EMPTY_CALL_DATA
        )
        
        self.context.logger.info(
            f"Prize transfer hash: {safe_tx_hash}, "
            f"Amount: {prize_amount}, "
            f"To: {winner_address}"
        )
        return safe_tx_hash
class LearningRoundBehaviour(AbstractRoundBehaviour):
    """LearningRoundBehaviour"""

    initial_behaviour_cls = DataPullBehaviour
    abci_app_cls = LearningAbciApp  # type: ignore
    behaviours: Set[Type[BaseBehaviour]] = [  # type: ignore
        DataPullBehaviour,
        DecisionMakingBehaviour,
        TxPreparationBehaviour,
    ]
