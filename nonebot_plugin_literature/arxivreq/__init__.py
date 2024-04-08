""".. include:: ../README.md"""

from __future__ import annotations

import os
import re
import math
import time
import logging
import warnings
from enum import Enum
from calendar import timegm
from typing import Generator
from urllib.request import urlretrieve
from datetime import datetime, timezone

import feedparser

from arxivreq.client import Client
from arxivreq.exception import _classname

logger = logging.getLogger(__name__)

_DEFAULT_TIME = datetime.min


class Result:
    """
    An entry in an arXiv query results feed.

    See [the arXiv API User's Manual: Details of Atom Results
    Returned](https://arxiv.org/help/api/user-manual#_details_of_atom_results_returned).
    """

    entry_id: str
    """A url of the form `https://arxiv.org/abs/{id}`."""
    updated: datetime
    """When the result was last updated."""
    published: datetime
    """When the result was originally published."""
    title: str
    """The title of the result."""
    authors: list[Author]
    """The result's authors."""
    summary: str
    """The result abstract."""
    comment: str
    """The authors' comment if present."""
    journal_ref: str
    """A journal reference if present."""
    doi: str
    """A URL for the resolved DOI to an external resource if present."""
    primary_category: str
    """
    The result's primary arXiv category. See [arXiv: Category
    Taxonomy](https://arxiv.org/category_taxonomy).
    """
    categories: list[str]
    """
    All of the result's categories. See [arXiv: Category
    Taxonomy](https://arxiv.org/category_taxonomy).
    """
    links: list[Link]
    """Up to three URLs associated with this result."""
    pdf_url: str
    """The URL of a PDF version of this result if present among links."""
    _raw: feedparser.FeedParserDict
    """
    The raw feedparser result object if this Result was constructed with
    Result._from_feed_entry.
    """

    def __init__(
        self,
        entry_id: str,
        updated: datetime = _DEFAULT_TIME,
        published: datetime = _DEFAULT_TIME,
        title: str = "",
        authors: list[Author] = [],
        summary: str = "",
        comment: str = "",
        journal_ref: str = "",
        doi: str = "",
        primary_category: str = "",
        categories: list[str] = [],
        links: list[Link] = [],
        _raw: feedparser.FeedParserDict = None,
    ):
        """
        Constructs an arXiv search result item.

        In most cases, prefer using `Result._from_feed_entry` to parsing and
        constructing `Result`s yourself.
        """
        self.entry_id = entry_id
        self.updated = updated
        self.published = published
        self.title = title
        self.authors = authors
        self.summary = summary
        self.comment = comment
        self.journal_ref = journal_ref
        self.doi = doi
        self.primary_category = primary_category
        self.categories = categories
        self.links = links
        # Calculated members
        self.pdf_url = Result._get_pdf_url(links)
        # Debugging
        self._raw = _raw

    def _from_feed_entry(entry: feedparser.FeedParserDict) -> Result:
        """
        Converts a feedparser entry for an arXiv search result feed into a
        Result object.
        """
        if not hasattr(entry, "id"):
            raise Result.MissingFieldError("id")
        # Title attribute may be absent for certain titles. Defaulting to "0" as
        # it's the only title observed to cause this bug.
        # https://github.com/lukasschwab/arxiv.py/issues/71
        # title = entry.title if hasattr(entry, "title") else "0"
        title = "0"
        if hasattr(entry, "title"):
            title = entry.title
        else:
            logger.warning("Result %s is missing title attribute; defaulting to '0'", entry.id)
        return Result(
            entry_id=entry.id,
            updated=Result._to_datetime(entry.updated_parsed),
            published=Result._to_datetime(entry.published_parsed),
            title=re.sub(r"\s+", " ", title),
            authors=[Result.Author._from_feed_author(a) for a in entry.authors],
            summary=entry.summary,
            comment=entry.get("arxiv_comment"),
            journal_ref=entry.get("arxiv_journal_ref"),
            doi=entry.get("arxiv_doi"),
            primary_category=entry.arxiv_primary_category.get("term"),
            categories=[tag.get("term") for tag in entry.tags],
            links=[Result.Link._from_feed_link(link) for link in entry.links],
            _raw=entry,
        )

    def __str__(self) -> str:
        return self.entry_id

    def __repr__(self) -> str:
        return (
            f"{_classname(self)}(entry_id={self.entry_id!r}, "
            f"updated={self.updated!r}, published={self.published!r}, title={self.title!r}, authors={self.authors!r}, "
            f"summary={self.summary!r}, comment={self.comment!r}, journal_ref={self.journal_ref!r}, doi={self.doi!r}, "
            f"primary_category={self.primary_category!r}, categories={self.categories!r}, links={self.links!r})"
        )

    def __eq__(self, other) -> bool:
        if isinstance(other, Result):
            return self.entry_id == other.entry_id
        return False

    def get_short_id(self) -> str:
        """
        Returns the short ID for this result.

        + If the result URL is `"https://arxiv.org/abs/2107.05580v1"`,
        `result.get_short_id()` returns `2107.05580v1`.

        + If the result URL is `"https://arxiv.org/abs/quant-ph/0201082v1"`,
        `result.get_short_id()` returns `"quant-ph/0201082v1"` (the pre-March
        2007 arXiv identifier format).

        For an explanation of the difference between arXiv's legacy and current
        identifiers, see [Understanding the arXiv
        identifier](https://arxiv.org/help/arxiv_identifier).
        """
        return self.entry_id.split("arxiv.org/abs/")[-1]

    def _get_default_filename(self, extension: str = "pdf") -> str:
        """
        A default `to_filename` function for the extension given.
        """
        nonempty_title = self.title if self.title else "UNTITLED"
        return ".".join(
            [
                self.get_short_id().replace("/", "_"),
                re.sub(r"[^\w]", "_", nonempty_title),
                extension,
            ]
        )

    def download_pdf(self, dirpath: str = "./", filename: str = "") -> str:
        """
        Downloads the PDF for this result to the specified directory.

        The filename is generated by calling `to_filename(self)`.
        """
        if not filename:
            filename = self._get_default_filename()
        path = os.path.join(dirpath, filename)
        written_path, _ = urlretrieve(self.pdf_url, path)
        return written_path

    def download_source(self, dirpath: str = "./", filename: str = "") -> str:
        """
        Downloads the source tarfile for this result to the specified
        directory.

        The filename is generated by calling `to_filename(self)`.
        """
        if not filename:
            filename = self._get_default_filename("tar.gz")
        path = os.path.join(dirpath, filename)
        # Bodge: construct the source URL from the PDF URL.
        source_url = self.pdf_url.replace("/pdf/", "/src/")
        written_path, _ = urlretrieve(source_url, path)
        return written_path

    def _get_pdf_url(links: list[Link]) -> str:
        """
        Finds the PDF link among a result's links and returns its URL.

        Should only be called once for a given `Result`, in its constructor.
        After construction, the URL should be available in `Result.pdf_url`.
        """
        pdf_urls = [link.href for link in links if link.title == "pdf"]
        if len(pdf_urls) == 0:
            return None
        elif len(pdf_urls) > 1:
            logger.warning("Result has multiple PDF links; using %s", pdf_urls[0])
        return pdf_urls[0]

    def _to_datetime(ts: time.struct_time) -> datetime:
        """
        Converts a UTC time.struct_time into a time-zone-aware datetime.

        This will be replaced with feedparser functionality [when it becomes
        available](https://github.com/kurtmckee/feedparser/issues/212).
        """
        return datetime.fromtimestamp(timegm(ts), tz=timezone.utc)

    class Author:
        """
        A light inner class for representing a result's authors.
        """

        name: str
        """The author's name."""

        def __init__(self, name: str):
            """
            Constructs an `Author` with the specified name.

            In most cases, prefer using `Author._from_feed_author` to parsing
            and constructing `Author`s yourself.
            """
            self.name = name

        def _from_feed_author(feed_author: feedparser.FeedParserDict) -> Result.Author:
            """
            Constructs an `Author` with the name specified in an author object
            from a feed entry.

            See usage in `Result._from_feed_entry`.
            """
            return Result.Author(feed_author.name)

        def __str__(self) -> str:
            return self.name

        def __repr__(self) -> str:
            return f"{_classname(self)}({self.name!r})"

        def __eq__(self, other) -> bool:
            if isinstance(other, Result.Author):
                return self.name == other.name
            return False

    class Link:
        """
        A light inner class for representing a result's links.
        """

        href: str
        """The link's `href` attribute."""
        title: str
        """The link's title."""
        rel: str
        """The link's relationship to the `Result`."""
        content_type: str
        """The link's HTTP content type."""

        def __init__(
            self,
            href: str,
            title: str | None = None,
            rel: str | None = None,
            content_type: str | None = None,
        ):
            """
            Constructs a `Link` with the specified link metadata.

            In most cases, prefer using `Link._from_feed_link` to parsing and
            constructing `Link`s yourself.
            """
            self.href = href
            self.title = title
            self.rel = rel
            self.content_type = content_type

        def _from_feed_link(feed_link: feedparser.FeedParserDict) -> Result.Link:
            """
            Constructs a `Link` with link metadata specified in a link object
            from a feed entry.

            See usage in `Result._from_feed_entry`.
            """
            return Result.Link(
                href=feed_link.href,
                title=feed_link.get("title"),
                rel=feed_link.get("rel"),
                content_type=feed_link.get("content_type"),
            )

        def __str__(self) -> str:
            return self.href

        def __repr__(self) -> str:
            return (
                f"{_classname(self)}({self.href!r}, "
                f"title={self.title!r}, rel={self.rel!r}, content_type={self.content_type!r})"
            )

        def __eq__(self, other) -> bool:
            if isinstance(other, Result.Link):
                return self.href == other.href
            return False

    class MissingFieldError(Exception):
        """
        An error indicating an entry is unparseable because it lacks required
        fields.
        """

        missing_field: str
        """The required field missing from the would-be entry."""
        message: str
        """Message describing what caused this error."""

        def __init__(self, missing_field):
            self.missing_field = missing_field
            self.message = "Entry from arXiv missing required info"

        def __repr__(self) -> str:
            return f"{_classname(self)}({self.missing_field!r})"


class SortCriterion(Enum):
    """
    A SortCriterion identifies a property by which search results can be
    sorted.

    See [the arXiv API User's Manual: sort order for return
    results](https://arxiv.org/help/api/user-manual#sort).
    """

    Relevance = "relevance"
    LastUpdatedDate = "lastUpdatedDate"
    SubmittedDate = "submittedDate"


class SortOrder(Enum):
    """
    A SortOrder indicates order in which search results are sorted according
    to the specified arxiv.SortCriterion.

    See [the arXiv API User's Manual: sort order for return
    results](https://arxiv.org/help/api/user-manual#sort).
    """

    Ascending = "ascending"
    Descending = "descending"


class Search:
    """
    A specification for a search of arXiv's database.

    To run a search, use `Search.run` to use a default client or `Client.run`
    with a specific client.
    """

    query: str
    """
    A query string.

    This should be unencoded. Use `au:del_maestro AND ti:checkerboard`, not
    `au:del_maestro+AND+ti:checkerboard`.

    See [the arXiv API User's Manual: Details of Query
    Construction](https://arxiv.org/help/api/user-manual#query_details).
    """
    id_list: list[str]
    """
    A list of arXiv article IDs to which to limit the search.

    See [the arXiv API User's
    Manual](https://arxiv.org/help/api/user-manual#search_query_and_id_list)
    for documentation of the interaction between `query` and `id_list`.
    """
    max_results: int | None
    """
    The maximum number of results to be returned in an execution of this
    search. To fetch every result available, set `max_results=None`.

    The API's limit is 300,000 results per query.
    """
    sort_by: SortCriterion
    """The sort criterion for results."""
    sort_order: SortOrder
    """The sort order for results."""

    def __init__(
        self,
        query: str = "",
        id_list: list[str] = [],
        max_results: int | None = None,
        sort_by: SortCriterion = SortCriterion.Relevance,
        sort_order: SortOrder = SortOrder.Descending,
    ):
        """
        Constructs an arXiv API search with the specified criteria.
        """
        self.query = query
        self.id_list = id_list
        # Handle deprecated v1 default behavior.
        self.max_results = None if max_results == math.inf else max_results
        self.sort_by = sort_by
        self.sort_order = sort_order

    def __str__(self) -> str:
        # TODO: develop a more informative string representation.
        return repr(self)

    def __repr__(self) -> str:
        return (
            f"{_classname(self)}(query={self.query!r}, "
            f"id_list={self.id_list!r}, max_results={self.max_results!r}, sort_by={self.sort_by!r}, "
            f"sort_order={self.sort_order!r})"
        )

    def url_args(self) -> dict[str, str]:
        """
        Returns a dict of search parameters that should be included in an API
        request for this search.
        """
        return {
            "search_query": self.query,
            "id_list": ",".join(self.id_list),
            "sortBy": self.sort_by.value,
            "sortOrder": self.sort_order.value,
        }

    def results(self, offset: int = 0) -> Generator[Result, None, None]:
        """
        Executes the specified search using a default arXiv API client. For info
        on default behavior, see `Client.__init__` and `Client.results`.

        **Deprecated** after 2.0.0; use `Client.results`.
        """
        warnings.warn(
            "The 'Search.results' method is deprecated, use 'Client.results' instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return Client().results(self, offset=offset)
