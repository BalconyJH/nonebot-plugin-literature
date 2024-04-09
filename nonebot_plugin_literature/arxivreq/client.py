from __future__ import annotations

import asyncio
import functools
from urllib.parse import urlencode
from datetime import datetime, timedelta
from typing import Callable, AsyncGenerator, cast

import httpx
import feedparser

from arxivreq import Result, Search, logger
from arxivreq.exception import HTTPError, UnexpectedEmptyPageError


class BasicClient:
    """
    Specifies a strategy for fetching results from arXiv's API.
    This class provides an asynchronous implementation to interact with the arXiv API.

    :var query_url_format: The arXiv query API endpoint format.
    :type query_url_format: str
    """

    query_url_format: str = "https://export.arxiv.org/api/query?{}"

    def __init__(self, page_size: int = 100, delay_seconds: float = 3.0, num_retries: int = 3) -> None:
        """
        Initializes a BasicClient instance with the specified parameters.

        :param page_size: Maximum number of results fetched in a single API request.
        :type page_size: int
        :param delay_seconds: Number of seconds to wait between API requests.
        :type delay_seconds: float
        :param num_retries: Number of times to retry a failing API request before giving up.
        :type num_retries: int
        :note: The default parameters should provide a robust request strategy for most use cases.
               Extreme page sizes, delays, or retries risk violating the arXiv API Terms of Use.
        """
        self.page_size: int = page_size
        self.delay_seconds: float = delay_seconds
        self.num_retries: int = num_retries
        self._last_request_dt: datetime | None = None

    def __str__(self) -> str:
        """
        Returns a string representation of the BasicClient instance.

        :return: A string that represents the BasicClient instance.
        :rtype: str
        """
        return (
            f"{self.__class__.__name__}(page_size={self.page_size}, "
            f"delay_seconds={self.delay_seconds}, num_retries={self.num_retries})"
        )

    async def _format_url(self, search: "Search", start: int, page_size: int) -> str:
        """
        Formats the URL for a query to the arXiv API.

        :param search: The search object defining the query parameters.
        :type search: Search
        :param start: The index of the first result to return.
        :type start: int
        :param page_size: The number of results to return.
        :type page_size: int
        :return: The formatted URL as a string.
        :rtype: str
        """
        url_args = search.url_args()
        url_args.update({"start": str(start), "max_results": str(page_size)})
        return self.query_url_format.format(urlencode(url_args))

    async def results(self, search: Search, offset: int = 0) -> AsyncGenerator[Result, None]:
        """
        Asynchronously fetches one page of the search results at a time, yielding
        the parsed `Result`s, until `max_results` results have been yielded or
        there are no more search results.
        """
        limit = search.max_results - offset if search.max_results else None
        if limit is not None and limit < 0:
            return
        async for result in self._results(search, offset):
            if limit is not None:
                if limit <= 0:
                    break
                yield result
                limit -= 1
            else:
                yield result

    async def _results(self, search: Search, offset: int = 0) -> AsyncGenerator[Result, None]:
        page_url = await self._format_url(search, offset, self.page_size)
        feed = await self._parse_feed(page_url, first_page=True)
        feed_dict = cast(dict, feed.feed)
        if not feed.entries:
            logger.info("Got empty first page; stopping generation")
            return
        total_results = int(feed_dict.get("opensearch_totalresults", 0))
        logger.info(
            "Got first page: %d of %d total results",
            len(feed.entries),
            total_results,
        )

        while feed.entries:
            for entry in feed.entries:
                try:
                    yield Result._from_feed_entry(entry)
                except Result.MissingFieldError as e:
                    logger.warning("Skipping partial result: %s", e)
            offset += len(feed.entries)
            if offset >= total_results:
                break
            page_url = await self._format_url(search, offset, self.page_size)
            feed = await self._parse_feed(page_url, first_page=False)

    async def _parse_feed(self, url: str, first_page: bool = True, _try_index: int = 0) -> feedparser.FeedParserDict:
        """
        Asynchronously fetches the specified URL and parses it with feedparser.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()  # This will raise an exception for 4xx/5xx responses
                content = response.content
        except (httpx.HTTPStatusError, httpx.ConnectError) as err:
            logger.debug("Got network error (try %d): %s", _try_index, err)
            if _try_index < self.num_retries:
                await asyncio.sleep(self.delay_seconds)  # Respect the delay between retries
                return await self._parse_feed(url, first_page=first_page, _try_index=_try_index + 1)
            else:
                logger.debug("Giving up (try %d): %s", _try_index, err)
                raise
        except Exception as err:
            logger.debug("Unexpected error: %s", err)
            raise

        # Parse the content using feedparser
        feed = feedparser.parse(content)
        return feed

    @staticmethod
    def rate_limiter(func: Callable) -> Callable:
        """
        A decorator to enforce a delay between requests to the arXiv API.

        :param func: The function to decorate with rate limiting.
        :type func: Callable
        :return: The decorated function with enforced delay.
        :rtype: Callable
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

        return wrapper


class AsyncClient(BasicClient):
    """
    An asynchronous client for fetching results from arXiv's API.
    """

    def __init__(self, page_size: int = 100, delay_seconds: float = 3.0, num_retries: int = 3):
        """
        Initializes an AsyncClient instance with the specified parameters.

        :param page_size: Maximum number of results fetched in a single API request.
        :type page_size: int
        :param delay_seconds: Number of seconds to wait between API requests.
        :type delay_seconds: float
        :param num_retries: Number of times to retry a failing API request before giving up.
        :type num_retries: int
        :note: The default parameters should provide a robust request strategy for most use cases.
               Extreme page sizes, delays, or retries risk violating the arXiv API Terms of Use.
        """
        super().__init__(page_size, delay_seconds, num_retries)
        self._async_client: httpx.AsyncClient = httpx.AsyncClient()

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

    async def _aresults(self, search: Search, offset: int = 0) -> AsyncGenerator[Result, None]:
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
            for _ in feed.entries:
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

        async with self._async_client as session:
            resp = await session.get(url, headers={"user-agent": "arxiv.py/2.1.0"})
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
