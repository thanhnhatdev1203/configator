#!/usr/bin/env python3

import logging
import redis, os
import threading
import time

from configator.utils.datatype import str_to_int
from configator.utils.function import build_url
from typing import Any, Callable, List, Tuple, Dict, Optional, Union

DEFAULT_CHANNEL_GROUP = 'configator'
DEFAULT_ENV_PREFIX = 'CONFIGATOR'

LOG = logging.getLogger(__name__)

class RedisClient(object):
    #
    def __init__(self, channel_group=None, env_prefix=None, **connection_kwargs):
        #
        self.CHANNEL_GROUP = channel_group if isinstance(channel_group, str) else DEFAULT_CHANNEL_GROUP
        #
        self.ENV_PREFIX = env_prefix if isinstance(env_prefix, str) else DEFAULT_ENV_PREFIX
        #
        self.__connection_kwargs = connection_kwargs
        #
        self.__update_connection_kwargs_from_env()
        #
        self.__retry_counter = RetryStrategyCounter()
        #
        self.rewind()
    #
    #
    def __update_connection_kwargs_from_env(self):
        #
        env_prefix_lodash = self.ENV_PREFIX + '_'
        #
        host = os.getenv(env_prefix_lodash + 'REDIS_HOST')
        if host:
            self.__connection_kwargs['host'] = host
        #
        if not self.__connection_kwargs.get('host'):
            self.__connection_kwargs['host'] = 'localhost'
        #
        port = os.getenv(env_prefix_lodash + 'REDIS_PORT')
        if port:
            port, err = str_to_int(port)
            if err is None and port > 0:
                self.__connection_kwargs['port'] = port
        if not self.__connection_kwargs.get('port'):
            self.__connection_kwargs['port'] = 6379
        #
        db = os.getenv(env_prefix_lodash + 'REDIS_DB')
        if db:
            db, err = str_to_int(db)
            if err is None and type(db) == type(0):
                self.__connection_kwargs['db'] = db
        if 'db' not in self.__connection_kwargs:
            self.__connection_kwargs['db'] = 0
        #
        username = os.getenv(env_prefix_lodash + 'REDIS_USERNAME')
        if username:
            self.__connection_kwargs['username'] = username
        #
        password = os.getenv(env_prefix_lodash + 'REDIS_PASSWORD')
        if password:
            self.__connection_kwargs['password'] = password
        #
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.log(logging.DEBUG, "redis connection kwargs: %s", str(self.__connection_kwargs))
        #
        self.__logging_url = build_url(self.__connection_kwargs, hide_secret=True)
    #
    ##
    __running = threading.Event()
    #
    def rewind(self):
        self.__running.set()
        return self
    #
    ##
    def connect(self, pinging:bool=True, retrying:bool=False):
        waiting = True
        while waiting and self.__running.is_set():
            try:
                connection = self.__connect(pinging=pinging)
                if retrying:
                    self.__retry_counter.reset()
                waiting = False
                return connection
            except redis.ConnectionError as conn_error:
                self.__close()
                if not retrying:
                    raise conn_error
                delay = self.__retry_counter.delay(self.retry_strategy)
                if LOG.isEnabledFor(logging.ERROR):
                    LOG.log(logging.ERROR, "redis.ConnectionError (%s). Reconnect after %s (seconds)",
                            self.__logging_url, str(delay))
                if delay > 0:
                    time.sleep(delay)
        return None
    #
    #
    def reconnect(self):
        self.__close()
        return self.connect(pinging=True, retrying=True)
    #
    #
    def close(self):
        self.__running.clear()
        self.__close()
    #
    #
    __connection = None
    __connection_lock = threading.RLock()
    #
    #
    def __connect(self, pinging=True):
        with self.__connection_lock:
            if self.__connection is None:
                pool = redis.ConnectionPool(**self.__connection_kwargs)
                conn = redis.Redis(connection_pool=pool)
                if pinging:
                    conn.ping()
                self.__connection = conn
            return self.__connection
    #
    #
    def __close(self):
        with self.__connection_lock:
            if self.__connection is not None:
                self.__connection.close()
                self.__connection = None
    #
    #
    __retry_strategy = None
    #
    @property
    def retry_strategy(self):
        if self.__retry_strategy is None:
            self.__retry_strategy = default_retry_strategy
        return self.__retry_strategy
    #
    @retry_strategy.setter
    def retry_strategy(self, func):
        if callable(func):
            self.__retry_strategy = func
        return func


def default_retry_strategy(attempt=0, total_retry_time=0, **kwargs):
    if attempt:
        delay = min(0.5 * attempt, 5)
    else:
        delay = 1
    # reconnect after
    return delay


class RetryStrategyCounter():
    MIN_DELAY_TIME = 0.1 # 100ms
    #
    __attempt = 0
    __total_retry_time = 0.0
    #
    def delay(self, retry_strategy: Callable[[int,float], float]) -> float:
        if not callable(retry_strategy):
            return 0
        #
        self.__attempt += 1
        #
        delaytime = retry_strategy(self.__attempt, self.__total_retry_time)
        #
        if delaytime < self.MIN_DELAY_TIME:
            delaytime = self.MIN_DELAY_TIME
        #
        self.__total_retry_time = self.__total_retry_time + delaytime
        #
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.log(logging.DEBUG, "Retry after %s (seconds)", str(delaytime))
        #
        return delaytime
    #
    #
    def reset(self):
        changed = False
        if self.__attempt > 0:
            self.__attempt = 0
            changed = True
        if self.__total_retry_time > 0:
            self.__total_retry_time = 0.0
            changed = True
        if changed:
            if LOG.isEnabledFor(logging.DEBUG):
                LOG.log(logging.DEBUG, "Reset the RetryStrategyCounter")
