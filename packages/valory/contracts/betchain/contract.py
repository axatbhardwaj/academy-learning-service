# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2023-2024 Valory AG
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

"""This module contains the class to connect to a Betchain."""

from typing import Dict

from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea.crypto.base import LedgerApi
from aea_ledger_ethereum import EthereumApi


PUBLIC_ID = PublicId.from_str("valory/betchain:0.1.0")


class BetChain(Contract):
    """The BettingContract contract."""

    contract_id = PUBLIC_ID

    @classmethod
    def create_bet(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        choice: int,
        value: int,
    ) -> Dict[str, bytes]:
        """Create a bet."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        data = contract_instance.encodeABI("createBet", args=(choice,))
        return {"data": bytes.fromhex(data[2:]), "value": value}

    @classmethod
    def resolve_bet(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        bet_id: int,
        result: int,
        ipfs_hash: str,
    ) -> Dict[str, bytes]:
        """Resolve a bet."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        data = contract_instance.encodeABI("resolveBet", args=(bet_id, result, ipfs_hash))
        return {"data": bytes.fromhex(data[2:])}

    @classmethod
    def get_first_pending_bet(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
    ) -> JSONLike:
        """Get the first pending bet."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        first_pending_bet = contract_instance.functions.getFirstPendingBet().call()
        return dict(data=first_pending_bet)

    @classmethod
    def check_balance(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        account: str,
    ) -> JSONLike:
        """Check the balance of the given account."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        balance_of = getattr(contract_instance.functions, "balanceOf")  # noqa
        token_balance = balance_of(account).call()
        wallet_balance = ledger_api.api.eth.get_balance(account)
        return dict(token=token_balance, wallet=wallet_balance)

    @classmethod
    def get_token_uri(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        token_id: int,
    ) -> JSONLike:
        """Get the token URI."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        token_uri = contract_instance.functions.tokenURI(token_id).call()
        return dict(data=token_uri)

    @classmethod
    def get_bet_details(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
        bet_id: int,
    ) -> JSONLike:
        """Get the details of a bet."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        bet_details = contract_instance.functions.bets(bet_id).call()
        return dict(data=bet_details)

    @classmethod
    def get_total_bets(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
    ) -> JSONLike:
        """Get the total number of bets."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        total_bets = contract_instance.functions.totalBets().call()
        return dict(data=total_bets)

    @classmethod
    def get_resolved_bets(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
    ) -> JSONLike:
        """Get the number of resolved bets."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        resolved_bets = contract_instance.functions.resolvedBets().call()
        return dict(data=resolved_bets)