import logging
import socket
from collections import namedtuple
from typing import List, Dict

import gevent

from .proxy import Proxy, reconnect_interval, sorted_events
from relay.logger import get_logger

from .currency_network_events import (
    BlockchainEvent,
    CurrencyNetworkEvent,
    CreditlineUpdateEventType,
    CreditlineRequestEventType,
    TrustlineUpdateEventType,
    TrustlineRequestEventType,
    BalanceUpdateEventType,
    TransferEventType,
    from_to_types,
    event_builders,
)


class Trustline(namedtuple('Trustline',
                           ['address', 'creditline_ab', 'creditline_ba', 'interest_ab', 'interest_ba',
                            'fees_outstanding_a', 'fees_outstanding_b', 'm_time', 'balance_ab'])):
    __slots__ = ()

    def __new__(cls, address, creditline_ab=0, creditline_ba=0, interest_ab=0, interest_ba=0, fees_outstanding_a=0,
                fees_outstanding_b=0, m_time=0, balance_ab=0):
        return super(Trustline, cls).__new__(cls, address, creditline_ab, creditline_ba, interest_ab, interest_ba,
                                             fees_outstanding_a, fees_outstanding_b, m_time, balance_ab)


logger = get_logger('currency network', logging.DEBUG)


class CurrencyNetworkProxy(Proxy):

    event_builders = event_builders
    event_types = list(event_builders.keys())

    standard_event_types = [TransferEventType,
                            CreditlineRequestEventType,
                            CreditlineUpdateEventType,
                            TrustlineRequestEventType,
                            TrustlineUpdateEventType]

    def __init__(self, web3, abi, address: str) -> None:
        super().__init__(web3, abi, address)
        self.name = self._proxy.call().name().strip('\0')  # type: str
        self.decimals = self._proxy.call().decimals()  # typ: str
        self.symbol = self._proxy.call().symbol().strip('\0')  # type: str

    @property
    def users(self) -> List[str]:
        return list(self._proxy.call().getUsers())

    def friends(self, user_address: str) -> List[str]:
        return list(self._proxy.call().getFriends(user_address))

    def account(self, a_address: str, b_address: str):
        return self._proxy.call().getAccount(a_address, b_address)

    def spendable(self, a_address: str):
        return self._proxy.call().spendable(a_address)

    def spendableTo(self, a_address: str, b_address: str):
        return self._proxy.call().spendableTo(a_address, b_address)

    def gen_graph_representation(self) -> Dict[str, List[Trustline]]:
        """Returns the trustlines network as a dict address -> list of Friendships"""
        result = {}
        for user in self.users:
            list = []
            for friend in self.friends(user):
                if user < friend:
                    (creditline_ab,
                     creditline_ba,
                     interest_ab,
                     interest_ba,
                     fees_outstanding_a,
                     fees_outstanding_b,
                     mtime,
                     balance_ab) = self.account(user, friend)
                    list.append(Trustline(friend,
                                          creditline_ab,
                                          creditline_ba,
                                          interest_ab,
                                          interest_ba,
                                          fees_outstanding_a,
                                          fees_outstanding_b,
                                          mtime,
                                          balance_ab))
            result[user] = list
        return result

    def start_listen_on_full_sync(self, function, sync_interval: float):
        def sync():
            while True:
                try:
                    function(self.gen_graph_representation())
                    gevent.sleep(sync_interval)
                except socket.timeout as err:
                    logger.warning('Full sync failed because of timeout, try again: ' + str(err))
                    gevent.sleep(reconnect_interval)
                except socket.error as err:
                    logger.warning('Full sync failed because of error, try again: ' + str(err))
                    gevent.sleep(reconnect_interval)

        gevent.Greenlet.spawn(sync)

    def start_listen_on_balance(self, f) -> None:
        def log(log_entry):
            f(self._build_event(log_entry))
        self.start_listen_on(BalanceUpdateEventType, log)

    def start_listen_on_creditline(self, f) -> None:
        def log_creditline(log_entry):
            f(self._build_event(log_entry))

        self.start_listen_on(CreditlineUpdateEventType, log_creditline)

    def start_listen_on_creditline_request(self, f) -> None:
        def log_creditline_request(log_entry):
            f(self._build_event(log_entry))

        self.start_listen_on(CreditlineRequestEventType, log_creditline_request)

    def start_listen_on_trustline(self, f) -> None:
        def log_trustline(log_entry):
            f(self._build_event(log_entry))

        self.start_listen_on(TrustlineUpdateEventType, log_trustline)

    def start_listen_on_trustline_request(self, f) -> None:
        def log_trustline_request(log_entry):
            f(self._build_event(log_entry))

        self.start_listen_on(TrustlineRequestEventType, log_trustline_request)

    def start_listen_on_transfer(self, f) -> None:
        def log(log_entry):
            f(self._build_event(log_entry))
        self.start_listen_on(TransferEventType, log)

    def get_network_events(self, event_name: str, user_address: str=None, from_block: int=0) -> List[BlockchainEvent]:
        if user_address is None:
            result = self.get_events(event_name, from_block=from_block)
        else:

            filter1 = {from_to_types[event_name][0]: user_address}
            filter2 = {from_to_types[event_name][1]: user_address}

            list_1 = self.get_events(event_name, filter_=filter1, from_block=from_block)
            list_2 = self.get_events(event_name, filter_=filter2, from_block=from_block)
            result = list_1 + list_2
            for event in result:
                if isinstance(event, CurrencyNetworkEvent):
                    event.user = user_address
                else:
                    raise ValueError('Expected a CurrencyNetworkEvent')
        return sorted_events(result)

    def get_all_network_events(self, user_address: str = None, from_block: int = 0) -> List[BlockchainEvent]:
        all_events = []  # type: List[BlockchainEvent]
        for type in self.standard_event_types:    # FIXME takes too long.
                                                    # web3.py currently doesn't support getAll() to retrieve all events
            all_events = all_events + self.get_network_events(type, user_address, from_block)
        return sorted_events(all_events)

    def estimate_gas_for_transfer(self, sender, receiver, value, max_fee, path):
        return self._proxy.estimateGas({'from': sender}).transfer(receiver, value, max_fee, path)
