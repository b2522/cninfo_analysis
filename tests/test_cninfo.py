import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cninfo_miner.cninfo import Announcement, build_query_payload, map_announcement


class CninfoTests(unittest.TestCase):
    def test_builds_form_payload_without_cookie_dependency(self) -> None:
        payload = build_query_payload("2026-08-01", "2026-08-03", page_num=2)
        self.assertEqual(payload["pageNum"], "2")
        self.assertEqual(payload["pageSize"], "30")
        self.assertEqual(payload["seDate"], "2026-08-01~2026-08-03")
        self.assertEqual(payload["column"], "szse")

    def test_maps_api_item_to_stable_announcement(self) -> None:
        item = {
            "announcementId": "abc-1",
            "secCode": "000001",
            "secName": "平安银行",
            "announcementTitle": "关于回购股份的公告",
            "announcementTime": 1785513600000,
            "adjunctUrl": "finalpage/2026-08-01/abc.PDF",
        }
        notice = map_announcement(item)
        self.assertEqual(notice.announcement_id, "abc-1")
        self.assertEqual(notice.stock_code, "000001")
        self.assertEqual(notice.pdf_url, "https://static.cninfo.com.cn/finalpage/2026-08-01/abc.PDF")


if __name__ == "__main__":
    unittest.main()
