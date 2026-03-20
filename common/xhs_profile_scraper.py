from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common.config import Settings


class XhsProfileScraper:
    def __init__(self, settings: Settings):
        self.settings = settings
        working_dir = settings.resolve_path(settings.get("xhs_mcp", "working_dir", "."))
        self.cookies_path = Path(working_dir) / "cookies.json"
        self.headless = bool(settings.get("xhs_mcp", "headless", True))

    def is_configured(self) -> bool:
        return self.cookies_path.exists()

    def load_profile_notes(self, user_id: str, limit: int = 10, profile_xsec_token: str = "") -> dict[str, Any]:
        if not user_id.strip():
            return {"user_id": "", "profile_xsec_token": "", "notes": [], "source": "missing_user_id"}
        if not self.is_configured():
            return {
                "user_id": user_id,
                "profile_xsec_token": "",
                "notes": [],
                "source": "missing_cookies",
            }

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        notes_by_id: dict[str, dict[str, Any]] = {}
        profile_info: dict[str, str] = {
            "user_id": user_id,
            "user_name": "",
            "red_id": "",
            "profile_xsec_token": profile_xsec_token.strip(),
        }
        profile_url = self._profile_url(user_id, profile_xsec_token)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(
                locale="zh-CN",
                viewport={"width": 1440, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            )
            context.add_cookies(self._load_cookies())
            page = context.new_page()
            try:
                page.goto(profile_url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2_500)
                for _ in range(max(3, min(8, limit + 1))):
                    batch = page.evaluate(self._extract_notes_script(), user_id)
                    if isinstance(batch, list):
                        for item in batch:
                            if not isinstance(item, dict):
                                continue
                            note_id = str(item.get("id") or "").strip()
                            if note_id:
                                notes_by_id[note_id] = item
                    if len(notes_by_id) >= limit:
                        break
                    page.mouse.wheel(0, 1600)
                    page.wait_for_timeout(1_200)

                extracted_info = page.evaluate(self._extract_profile_info_script(), user_id)
                if isinstance(extracted_info, dict):
                    profile_info.update(
                        {
                            "user_id": str(extracted_info.get("user_id") or user_id),
                            "user_name": str(extracted_info.get("user_name") or ""),
                            "red_id": str(extracted_info.get("red_id") or ""),
                            "profile_xsec_token": str(extracted_info.get("profile_xsec_token") or ""),
                        }
                    )
            except PlaywrightTimeoutError:
                pass
            finally:
                context.close()
                browser.close()

        notes = list(notes_by_id.values())[:limit]
        return {
            "user_id": profile_info["user_id"],
            "user_name": profile_info["user_name"],
            "red_id": profile_info["red_id"],
            "profile_xsec_token": profile_info["profile_xsec_token"],
            "notes": notes,
            "source": "playwright_profile",
        }

    def _load_cookies(self) -> list[dict[str, Any]]:
        raw = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        cookies: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            same_site = str(item.get("sameSite") or "Lax").title()
            if same_site not in {"Strict", "Lax", "None"}:
                same_site = "Lax"
            cookie: dict[str, Any] = {
                "name": str(item.get("name") or ""),
                "value": str(item.get("value") or ""),
                "domain": str(item.get("domain") or ""),
                "path": str(item.get("path") or "/"),
                "httpOnly": bool(item.get("httpOnly", False)),
                "secure": bool(item.get("secure", False)),
                "sameSite": same_site,
            }
            expires = item.get("expires")
            if isinstance(expires, (int, float)) and expires > 0:
                cookie["expires"] = int(expires)
            if cookie["name"] and cookie["domain"]:
                cookies.append(cookie)
        return cookies

    def _profile_url(self, user_id: str, profile_xsec_token: str) -> str:
        token = profile_xsec_token.strip()
        if not token:
            return f"https://www.xiaohongshu.com/user/profile/{user_id}"
        return (
            f"https://www.xiaohongshu.com/user/profile/{user_id}"
            f"?channel_type=web_note_detail_r10"
            f"&parent_page_channel_type=web_profile_board"
            f"&xsec_token={token}"
            f"&xsec_source=pc_note"
        )

    def _extract_notes_script(self) -> str:
        return """
        (userId) => {
          const notes = [];
          const seen = new Set();
          const anchors = Array.from(document.querySelectorAll(`a[href*="/user/profile/${userId}/"]`));
          for (const anchor of anchors) {
            const href = anchor.getAttribute("href") || "";
            const match = href.match(/\\/user\\/profile\\/[^/]+\\/([^?#/]+)\\?[^#]*xsec_token=([^&#]*)/i);
            if (!match) {
              continue;
            }
            const noteId = match[1] || "";
            const xsecToken = decodeURIComponent(match[2] || "");
            if (!noteId || seen.has(noteId)) {
              continue;
            }
            const contentBlock = anchor.nextElementSibling;
            const title = (contentBlock?.firstElementChild?.textContent || "").trim();
            notes.push({
              id: noteId,
              xsecToken,
              noteCard: {
                displayTitle: title
              },
              url: new URL(href, window.location.origin).toString(),
            });
            seen.add(noteId);
          }
          return notes;
        }
        """

    def _extract_profile_info_script(self) -> str:
        return """
        (userId) => {
          const state = window.__INITIAL_STATE__ || {};
          const userInfo = state.user?.userInfo?._value || state.user?.userInfo?._rawValue || {};
          const pageData = state.user?.userPageData?._value || state.user?.userPageData?._rawValue || {};
          const profileLinks = Array.from(document.querySelectorAll(`a[href*="/user/profile/${userId}"]`))
            .map((node) => node.getAttribute("href") || "")
            .filter((href) => href.includes("xsec_token="));
          const profileLink = profileLinks.find((href) => href.includes(`/user/profile/${userId}?`)) || "";
          const tokenMatch = profileLink.match(/[?&]xsec_token=([^&#]*)/i);
          return {
            user_id: String(userInfo.userId || userId || ""),
            user_name: String(pageData.basicInfo?.nickname || userInfo.nickname || ""),
            red_id: String(pageData.basicInfo?.redId || userInfo.redId || ""),
            profile_xsec_token: decodeURIComponent(tokenMatch ? (tokenMatch[1] || "") : ""),
          };
        }
        """
