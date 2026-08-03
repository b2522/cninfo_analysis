"""CNINFO list query adapter. The live response mapping is deliberately defensive."""

from dataclasses import dataclass
from typing import Any

import httpx

QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_BASE_URL = "https://static.cninfo.com.cn/"


@dataclass(frozen=True)
class Announcement:
    announcement_id: str
    stock_code: str
    stock_name: str
    title: str
    announcement_time: int | None
    pdf_url: str | None


def build_query_payload(start_date: str, end_date: str, *, page_num: int) -> dict[str, str]:
    return {
        "pageNum": str(page_num),
        "pageSize": "30",
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }


def map_announcement(item: dict[str, Any]) -> Announcement:
    adjunct_url = item.get("adjunctUrl")
    pdf_url = f"{STATIC_BASE_URL}{adjunct_url.lstrip('/')}" if adjunct_url else None
    return Announcement(
        announcement_id=str(item.get("announcementId") or item.get("id") or ""),
        stock_code=str(item.get("secCode") or ""),
        stock_name=str(item.get("secName") or ""),
        title=str(item.get("announcementTitle") or ""),
        announcement_time=item.get("announcementTime"),
        pdf_url=pdf_url,
    )


class CninfoClient:
    """Small, public-endpoint-only CNINFO client; it never accepts cookies."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0, headers={"X-Requested-With": "XMLHttpRequest"})
        self._owns_client = client is None

    async def list_page(self, start_date: str, end_date: str, page_num: int) -> tuple[list[Announcement], int]:
        response = await self._client.post(QUERY_URL, data=build_query_payload(start_date, end_date, page_num=page_num))
        response.raise_for_status()
        data = response.json()
        announcements = [map_announcement(item) for item in data.get("announcements", [])]
        return announcements, int(data.get("totalpages") or 0)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
