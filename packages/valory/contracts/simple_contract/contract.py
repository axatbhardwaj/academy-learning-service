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

"""This module contains the class to connect to an ERC20 token contract."""

from typing import Dict, List

from aea.common import JSONLike
from aea.configurations.base import PublicId
from aea.contracts.base import Contract
from aea.crypto.base import LedgerApi
from aea_ledger_ethereum import EthereumApi
from eth_typing import BlockIdentifier


PUBLIC_ID = PublicId.from_str("valory/simple_contract:0.1.0")


class TotalSupplyReader(Contract):
    """A simple contract to read token balances."""

    contract_id = PUBLIC_ID

    @classmethod
    def get_total_supply(
        cls,
        ledger_api: EthereumApi,
        contract_address: str,
    ) -> JSONLike:
        """Get the total supply of the token."""
        contract_instance = cls.get_instance(ledger_api, contract_address)
        total_supply = getattr(contract_instance.functions, "totalSupply")  # noqa
        token_total_supply = total_supply().call()
        return dict(total_supply=token_total_supply)
