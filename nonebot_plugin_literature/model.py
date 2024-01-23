from typing import List, Optional

from pydantic import Field, BaseModel


class Author(BaseModel):
    name: str = Field(..., description="The name of the author.")
    affiliation: Optional[str] = Field(None, description="The affiliation of the author (optional).")


class Entry(BaseModel):
    title: str = Field(..., description="Title of the article.")
    id: str = Field(..., description="URL of the article on arXiv.")
    published: str = Field(..., description="Publication date of the article.")
    updated: str = Field(..., description="Last updated date of the article.")
    summary: str = Field(..., description="Abstract of the article.")
    authors: List[Author] = Field(..., description="List of authors of the article.")
    links: List[str] = Field(..., description="URLs associated with the article.")
    categories: List[str] = Field(..., description="Categories of the article.")
    primary_category: str = Field(..., description="Primary category of the article.")
    comment: Optional[str] = Field(None, description="Author's comment.")
    journal_ref: Optional[str] = Field(None, description="Journal reference.")
    doi: Optional[str] = Field(None, description="DOI URL.")


class Feed(BaseModel):
    title: str = Field(..., description="Title of the feed.")
    id: str = Field(..., description="Unique ID of the feed.")
    updated: str = Field(..., description="Last updated time of the feed.")
    link: str = Field(..., description="URL to retrieve the feed.")
    total_results: int = Field(..., description="Total number of search results.")
    start_index: int = Field(..., description="Index of the first returned result.")
    items_per_page: int = Field(..., description="Number of results per page.")
    entries: List[Entry] = Field(..., description="List of entries in the feed.")


# Example usage
example_entry = {
    "title": "Example Article",
    "id": "http://arxiv.org/abs/1234.5678",
    "published": "2024-01-01",
    "updated": "2024-01-02",
    "summary": "An example summary.",
    "authors": [{"name": "John Doe", "affiliation": "University of Example"}],
    "links": ["http://arxiv.org/pdf/1234.5678"],
    "categories": ["math.AC"],
    "primary_category": "math.AC",
    "comment": "12 pages, 3 figures",
    "journal_ref": "J. Ex. Math. 12 (2024) 345-678",
    "doi": "http://dx.doi.org/10.1234/example.5678",
}

example_feed = {
    "title": "ArXiv Query: search_query=all:electron",
    "id": "http://arxiv.org/api/cHxbiOdZaP56ODnBPIenZhzg5f8",
    "updated": "2024-01-07T00:00:00-05:00",
    "link": "http://arxiv.org/api/query?search_query%3Dall%3Aelectron%26id_list%3D%26start%3D0%26max_results%3D1",
    "total_results": 204910,
    "start_index": 0,
    "items_per_page": 1,
    "entries": [example_entry],
}

feed = Feed(**example_feed)
