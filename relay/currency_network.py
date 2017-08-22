import logging
import gevent
import socket
from collections import namedtuple

from relay.logger import getLogger


Trustline = namedtuple('Trustline', 'address creditline_ab creditline_ba interest_ab interest_ba fees_outstanding_a fees_outstanding_b m_time balance_ab')


logger = getLogger('tl_helper', logging.DEBUG)


# Constants
REGISTRY = 'Registry'
TRUSTLINE = 'Trustlines'
TrustlineRequestEvent = 'CreditlineUpdateRequest'
TrustlineUpdatedEvent = 'CreditlineUpdate'
BalanceUpdatedEvent = 'BalanceUpdate'
TransferEvent = 'Transfer'
PathPreparedEvent = 'PathPrepared'
ChequeCashed = 'ChequeCashed'

queryBlock = 'pending'
updateBlock = 'pending'

sync_interval = 300 # 5min
reconnect_interval = 3 # 3s


class CurrencyNetwork:

    def __init__(self, web3, abi, address):
        self._web3 = web3
        self._proxy = web3.eth.contract(abi=abi, address=address)

    @property
    def name(self):
        return self._proxy.call().name().strip('\0')

    @property
    def address(self):
        return self._proxy.address

    @property
    def decimals(self):
        return self._proxy.call().decimals()

    @property
    def symbol(self):
        return self._proxy.call().symbol().strip('\0')

    @property
    def users(self):
        return list(self._proxy.call().getUsers())

    def friends(self, user_address):
        return list(self._proxy.call().getFriends(user_address))

    def trustline(self, a_address, b_address):
        return self._proxy.call().trustline(a_address, b_address)

    def account(self, a_address, b_address):
        return self._proxy.call().getAccountExt(a_address, b_address)

    def spendable(self, a_address):
        return self._proxy.call().spendable(a_address)

    def spendableTo(self, a_address, b_address):
        return self._proxy.call().spendableTo(a_address, b_address)

    def gen_graph_representation(self):
        """Returns the trustlines network as a dict address -> list of Friendships"""
        result = {}
        for user in self.users:
            list = []
            for friend in self.friends(user):
                if user < friend:
                    creditline_ab, creditline_ba, interest_ab, interest_ba, fees_outstanding_a, fees_outstanding_b, mtime, balance_ab = self.account(user, friend)
                    list.append(Trustline(friend, creditline_ab, creditline_ba, interest_ab, interest_ba, fees_outstanding_a, fees_outstanding_b, mtime, balance_ab))
            result[user] = list
        return result

    def _watch_filter(self, eventname, function, params=None):
        while True:
            try:
                filter = self._proxy.on(eventname, params)
                filter.watch(function)
                logger.info('Connected to filter for {}'.format(eventname))
                return filter
            except socket.timeout as err:
                logger.warning('Timeout in filter creation, try to reconnect: ' + str(err))
                gevent.sleep(reconnect_interval)
            except socket.error as err:
                logger.warning('Socketerror in filter creation, try to reconnect:' + str(err))
                gevent.sleep(reconnect_interval)
            except ValueError as err:
                logger.warning('ValueError in filter creation, try to reconnect:' + str(err))
                gevent.sleep(reconnect_interval)

    def start_listen_on(self, eventname, function, params=None):
        def on_exception(filter):
            logger.warning('Filter {} disconnected, trying to reconnect'.format(filter.filter_id))
            gevent.sleep(reconnect_interval)
            filter = self._watch_filter(eventname, function, params)
            filter.link_exception(on_exception)
        if params is None:
            params = {}
        params.setdefault('fromBlock', updateBlock)
        params.setdefault('toBlock', updateBlock)
        filter = self._watch_filter(eventname, function, params)
        filter.link_exception(on_exception)

    def start_listen_on_full_sync(self, function):
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

    def start_listen_on_balance(self, function):
        def log(log_entry):
            function(log_entry['args']['_from'], log_entry['args']['_to'], log_entry['args']['_value'])
        self.start_listen_on(BalanceUpdatedEvent, log)

    def start_listen_on_trustline(self, function):
        def log(log_entry):
            function(log_entry['args']['_creditor'], log_entry['args']['_debtor'], log_entry['args']['_value'])
        self.start_listen_on(TrustlineUpdatedEvent, log)

    def start_listen_on_trustline_request(self):
        def log(log_entry):
            pass
        self.start_listen_on(TrustlineRequestEvent, log)

    def start_listen_on_transfer(self):
        def log(log_entry):
            pass
        self.start_listen_on(TransferEvent, log, {'fromBlock': 'pending', 'toBlock': 'pending' })

    def get_filter(self, event_name, params=None):
        return self._proxy.on(event_name, params).get(False)
