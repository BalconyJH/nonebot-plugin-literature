from __future__ import annotations

import time
import asyncio
import functools
import itertools
from urllib.parse import urlencode
from datetime import datetime, timedelta
from typing import Generator, AsyncGenerator

import httpx
import feedparser

from arxivreq import Result, Search, logger, _classname
from arxivreq.exception import HTTPError, UnexpectedEmptyPageError


class BasicClient:
    """
    Specifies a strategy for fetching results from arXiv's API.

    This class is a basic implementation of the `Client` class.
    """

    query_url_format = "https://export.arxiv.org/api/query?{}"
    """
    The arXiv query API endpoint format.
    """
    page_size: int
    """
    Maximum number of results fetched in a single API request. Smaller pages can
    be retrieved faster, but may require more round-trips.

    The API's limit is 2000 results per page.
    """
    delay_seconds: float
    """
    Number of seconds to wait between API requests.

    [arXiv's Terms of Use](https://arxiv.org/help/api/tou) ask that you "make no
    more than one request every three seconds."
    """
    num_retries: int
    """
    Number of times to retry a failing API request before raising an Exception.
    """

    _last_request_dt: datetime

    def __init__(self, page_size: int = 100, delay_seconds: float = 3.0, num_retries: int = 3):
        """
        Constructs an arXiv API client with the specified options.

        Note: the default parameters should provide a robust request strategy
        for most use cases. Extreme page sizes, delays, or retries risk
        violating the arXiv [API Terms of Use](https://arxiv.org/help/api/tou),
        brittle behavior, and inconsistent results.
        """
        self.page_size = page_size
        self.delay_seconds = delay_seconds
        self.num_retries = num_retries
        self._last_request_dt = None

    def __str__(self) -> str:
        # TODO: develop a more informative string representation.
        return repr(self)

    def __repr__(self) -> str:
        return (
            f"{_classname(self)}"
            f"(page_size={self.page_size!r}, delay_seconds={self.delay_seconds!r}, num_retries={self.num_retries!r})"
        )

    def _format_url(self, search: Search, start: int, page_size: int) -> str:
        """
        Construct a request API for search that returns up to `page_size`
        results starting with the result at index `start`.
        """
        url_args = search.url_args()
        url_args.update(
            {
                "start": str(start),
                "max_results": str(page_size),
            }
        )
        return self.query_url_format.format(urlencode(url_args))

    @staticmethod
    def rate_limiter(func):
        """
        Decorator for enforcing call rate limits based on `delay_seconds`.
        """

        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            if self._last_request_dt is not None:
                required = timedelta(seconds=self.delay_seconds)
                since_last_request = datetime.now() - self._last_request_dt
                if since_last_request < required:
                    to_sleep = (required - since_last_request).total_seconds()
                    await asyncio.sleep(to_sleep)

            self._last_request_dt = datetime.now()
            return await func(self, *args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(self, *args, **kwargs):
            if self._last_request_dt is not None:
                required = timedelta(seconds=self.delay_seconds)
                since_last_request = datetime.now() - self._last_request_dt
                if since_last_request < required:
                    to_sleep = (required - since_last_request).total_seconds()
                    time.sleep(to_sleep)

            self._last_request_dt = datetime.now()
            return func(self, *args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return wrapper
        else:
            return sync_wrapper


class Client(BasicClient):
    _session: httpx.Client

    def __init__(self, page_size: int = 100, delay_seconds: float = 3.0, num_retries: int = 3):
        super().__init__(page_size, delay_seconds, num_retries)
        self._session = httpx.Client()

    def results(self, search: Search, offset: int = 0) -> Generator[Result, None, None]:
        """
        Uses this client configuration to fetch one page of the search results
        at a time, yielding the parsed `Result`s, until `max_results` results
        have been yielded or there are no more search results.

        If all tries fail, raises an `UnexpectedEmptyPageError` or `HTTPError`.

        Setting a nonzero `offset` discards leading records in the result set.
        When `offset` is greater than or equal to `search.max_results`, the full
        result set is discarded.

        For more on using generators, see
        [Generators](https://wiki.python.org/moin/Generators).
        """
        limit = search.max_results - offset if search.max_results else None
        if limit and limit < 0:
            return iter(())
        return itertools.islice(self._results(search, offset), limit)

    def _results(self, search: Search, offset: int = 0) -> Generator[Result, None, None]:
        page_url = self._format_url(search, offset, self.page_size)
        feed = self._parse_feed(page_url, first_page=True)
        if not feed.entries:
            logger.info("Got empty first page; stopping generation")
            return
        total_results = int(feed.feed.opensearch_totalresults)
        logger.info(
            "Got first page: %d of %d total results",
            len(feed.entries),
            total_results,
        )

        while feed.entries:
            for entry in feed.entries:
                try:
                    yield Result._from_feed_entry
                except Result.MissingFieldError as e:
                    logger.warning("Skipping partial result: %s", e)
            offset += len(feed.entries)
            if offset >= total_results:
                break
            page_url = self._format_url(search, offset, self.page_size)
            feed = self._parse_feed(page_url, first_page=False)

    def _parse_feed(self, url: str, first_page: bool = True, _try_index: int = 0) -> feedparser.FeedParserDict:
        """
        Fetches the specified URL and parses it with feedparser.

        If a request fails or is unexpectedly empty, retries the request up to
        `self.num_retries` times.
        """
        try:
            return self.__try_parse_feed(url, first_page=first_page, try_index=_try_index)
        except (
            HTTPError,
            UnexpectedEmptyPageError,
            httpx.ConnectError,
        ) as err:
            if _try_index < self.num_retries:
                logger.debug("Got error (try %d): %s", _try_index, err)
                return self._parse_feed(url, first_page=first_page, _try_index=_try_index + 1)
            logger.debug("Giving up (try %d): %s", _try_index, err)
            raise err

    @BasicClient.rate_limiter
    def __try_parse_feed(
        self,
        url: str,
        first_page: bool,
        try_index: int,
    ) -> feedparser.FeedParserDict:
        """
        Recursive helper for _parse_feed. Enforces `self.delay_seconds`: if that
        number of seconds has not passed since `_parse_feed` was last called,
        sleeps until delay_seconds seconds have passed.
        """
        # If this call would violate the rate limit, sleep until it doesn't.
        logger.info("Requesting page (first: %r, try: %d): %s", first_page, try_index, url)

        resp = self._session.get(url, headers={"user-agent": "arxiv.py/2.1.0"})
        self._last_request_dt = datetime.now()
        if resp.status_code != httpx.codes.OK:
            raise HTTPError(url, try_index, resp.status_code)

        feed = feedparser.parse(resp.content)
        if len(feed.entries) == 0 and not first_page:
            raise UnexpectedEmptyPageError(url, try_index, feed)

        if feed.bozo:
            logger.warning(
                "Bozo feed; consider handling: %s",
                feed.bozo_exception if "bozo_exception" in feed else None,
            )

        return feed


class AsyncClient(BasicClient):
    _async_session: httpx.AsyncClient

    def __init__(self, page_size: int = 100, delay_seconds: float = 3.0, num_retries: int = 3):
        super().__init__(page_size, delay_seconds, num_retries)
        self._async_session = httpx.AsyncClient()

    async def results(self, search: Search, offset: int = 0) -> AsyncGenerator[Result, None]:
        """
        Uses this client configuration to fetch one page of the search results
        at a time, yielding the parsed `Result`s, until `max_results` results
        have been yielded or there are no more search results.

        If all tries fail, raises an `UnexpectedEmptyPageError` or `HTTPError`.

        Setting a nonzero `offset` discards leading records in the result set.
        When `offset` is greater than or equal to `search.max_results`, the full
        result set is discarded.

        For more on using generators, see
        [Generators](https://wiki.python.org/moin/Generators).
        """
        limit = search.max_results - offset if search.max_results else None
        if limit is not None and limit <= 0:
            return

        count = 0
        async for result in self._aresults(search, offset):
            # Asynchronously fetches search results based on `search` criteria and `offset`.
            # Uses an asynchronous generator to yield results from `_aresults`, which
            # fetches results asynchronously.
            # Initializes `count` to 0 to track the number of yielded results. Enters an
            # async loop over `_aresults`. For each result, checks if the yielded result
            # count has reached `limit`. If so, stops iteration.
            # If not at limit, yields the current result and increments `count` by 1.
            # Continues until reaching the specified limit or exhausting `_aresults`.
            if limit is not None and count >= limit:
                break
            yield result
            count += 1

    async def _aresults(self, search: Search, offset: int = 0) -> Generator[Result, None, None]:
        page_url = self._format_url(search, offset, self.page_size)
        feed = self._parse_feed(page_url, first_page=True)
        if not feed.entries:
            logger.info("Got empty first page; stopping generation")
            return
        total_results = int(feed.feed.opensearch_totalresults)
        logger.info(
            "Got first page: %d of %d total results",
            len(feed.entries),
            total_results,
        )

        while feed.entries:
            for entry in feed.entries:
                try:
                    yield Result._from_feed_entry
                except Result.MissingFieldError as e:
                    logger.warning("Skipping partial result: %s", e)
            offset += len(feed.entries)
            if offset >= total_results:
                break
            page_url = self._format_url(search, offset, self.page_size)
            feed = self._parse_feed(page_url, first_page=False)

    @BasicClient.rate_limiter
    async def _try_aparse_feed(
        self,
        url: str,
        first_page: bool,
        try_index: int,
    ) -> feedparser.FeedParserDict:
        """
        Recursive helper for _parse_feed. Enforces `self.delay_seconds`: if that
        number of seconds has not passed since `_parse_feed` was last called,
        sleeps until delay_seconds seconds have passed.
        """
        # If this call would violate the rate limit, sleep until it doesn't.
        if self._last_request_dt is not None:
            required = timedelta(seconds=self.delay_seconds)
            since_last_request = datetime.now() - self._last_request_dt
            if since_last_request < required:
                to_sleep = (required - since_last_request).total_seconds()
                logger.info("Sleeping: %f seconds", to_sleep)
                await asyncio.sleep(to_sleep)

        logger.info("Requesting page (first: %r, try: %d): %s", first_page, try_index, url)

        async with self._async_session.get(url, headers={"user-agent": "arxiv.py/2.1.0"}) as resp:
            self._last_request_dt = datetime.now()
            if resp.status_code != httpx.codes.OK:
                raise HTTPError(url, try_index, resp.status_code)

            feed = feedparser.parse(await resp.aread())
            if len(feed.entries) == 0 and not first_page:
                raise UnexpectedEmptyPageError(url, try_index, feed)

            if feed.bozo:
                logger.warning(
                    "Bozo feed; consider handling: %s",
                    feed.bozo_exception if "bozo_exception" in feed else None,
                )

            return feed
