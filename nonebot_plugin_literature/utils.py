from pathlib import Path
from typing import List, Union
import xml.etree.ElementTree as ET

import requests

from nonebot_plugin_literature.model import Feed, Entry, Author

response = requests.get("http://example.com/api/some_atom_feed")


async def load_xml(data: Union[str, Path]) -> ET.Element:
    """
    Load XML data from a string or a file path.
    :param data: XML data as a string or a file path.
    :return: The root element of the XML.
    :raises ET.ParseError: If the string data is not well-formed XML.
    :raises FileNotFoundError: If the file path does not exist.
    """
    if isinstance(data, Path) or (isinstance(data, str) and Path(data).is_file()):
        if not Path(data).exists():
            raise FileNotFoundError(f"File not found: {data}")
        return ET.parse(str(data)).getroot()
    else:
        try:
            return ET.fromstring(data)
        except ET.ParseError as e:
            raise ET.ParseError("String data is not well-formed XML.") from e


def find_text(element: ET.Element, path: str, namespaces: dict) -> str:
    """辅助函数，安全地获取XML元素的文本内容，如果找不到则返回空字符串。"""
    found_element = element.find(path, namespaces)
    # 确保即使是None也转换为字符串
    return found_element.text if found_element is not None and found_element.text is not None else ""


def parse_authors(entry_elem: ET.Element, ns: dict) -> List[Author]:
    """解析作者信息。"""
    return [
        Author(
            name=find_text(author_elem, "atom:name", ns),
            affiliation=find_text(author_elem, "arxiv:affiliation", ns),
        )
        for author_elem in entry_elem.findall("atom:author", ns)
    ]


def parse_entry(entry_elem: ET.Element, ns: dict) -> Entry:
    """解析单个条目。"""
    authors = parse_authors(entry_elem, ns)
    links = [link.get("href") for link in entry_elem.findall("atom:link", ns) if link.get("href") is not None]
    categories = [
        category.get("term") for category in entry_elem.findall("atom:category", ns) if category.get("term") is not None
    ]
    return Entry(
        title=find_text(entry_elem, "atom:title", ns),
        id=find_text(entry_elem, "atom:id", ns),
        published=find_text(entry_elem, "atom:published", ns),
        updated=find_text(entry_elem, "atom:updated", ns),
        summary=find_text(entry_elem, "atom:summary", ns),
        authors=authors,
        links=links,
        categories=categories,
        primary_category=find_text(entry_elem, "arxiv:primary_category", ns),
        comment=find_text(entry_elem, "arxiv:comment", ns),
        journal_ref=find_text(entry_elem, "arxiv:journal_ref", ns),
        doi=find_text(entry_elem, "arxiv:doi", ns),
    )


async def atom_parser(data: ET.Element) -> Feed:
    """解析 Atom feed。"""
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
        "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    }

    feed_info = {
        "title": find_text(data, "atom:title", ns),
        "id": find_text(data, "atom:id", ns),
        "updated": find_text(data, "atom:updated", ns),
        "link": find_text(data, "atom:link", ns),
        "total_results": int(find_text(data, "opensearch:totalResults", ns) or 0),
        "start_index": int(find_text(data, "opensearch:startIndex", ns) or 0),
        "items_per_page": int(find_text(data, "opensearch:itemsPerPage", ns) or 0),
    }

    entries = [parse_entry(entry_elem, ns) for entry_elem in data.findall("atom:entry", ns)]

    return Feed(
        title=feed_info["title"],
        id=feed_info["id"],
        updated=feed_info["updated"],
        link=feed_info["link"],
        total_results=feed_info["total_results"],
        start_index=feed_info["start_index"],
        items_per_page=feed_info["items_per_page"],
        entries=entries,
    )
