from __future__ import annotations

import re
from pathlib import Path

from common.models import GeneratedContent, ReviewResult
from common.utils import clean_text

from agents.base import BaseAgent


class OriginalityReviewer(BaseAgent):
    AI_PHRASES = (
        "首先",
        "其次",
        "总之",
        "不难发现",
        "综上所述",
        "值得一提的是",
    )

    def __init__(self, settings, db, vector_store):
        super().__init__(settings, db, vector_store)
        self.sensitive_words = self._load_sensitive_words(settings.sensitive_words_path)

    def review(self, content: GeneratedContent) -> ReviewResult:
        score = 92
        issues: list[str] = []
        suggestions: list[str] = []

        title = clean_text(content.title)
        body = clean_text(content.body)
        paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
        first_paragraph = paragraphs[0] if paragraphs else ""
        digits = re.findall(r"\d+", body)

        sensitive_hits = [word for word in self.sensitive_words if word and word in title + body]
        if sensitive_hits:
            score -= 45
            issues.append(f"命中敏感词：{', '.join(sensitive_hits[:5])}")
            suggestions.append("删除敏感表达，换成中性、克制的说法。")

        if len(digits) < 2:
            score -= 18
            issues.append("具体数字不够，缺少真实体感。")
            suggestions.append("补至少两个具体数字，比如价格、时间、次数或点位数。")

        if "我当时以为" not in body or "结果发现" not in body:
            score -= 12
            issues.append("缺少明显转折，真人反差感不够。")
            suggestions.append("加入“我当时以为……结果发现……”的反转句。")

        if not any(phrase in body for phrase in ("不一定适合所有人", "未必适合所有人", "不一定适合你")):
            score -= 10
            issues.append("缺少适合谁/不适合谁的边界提醒。")
            suggestions.append("补一句“不一定适合所有人”，说明适用边界。")

        if not any(marker in first_paragraph for marker in ("别急着", "值不值", "真香", "以为", "结果", "劝")):
            score -= 12
            issues.append("开头不够抓人，前两句冲突感偏弱。")
            suggestions.append("把开头改成更强的冲突句或反差句。")

        if not any(line.strip().startswith(("1.", "2.", "3.")) for line in body.splitlines()):
            score -= 8
            issues.append("正文不够好扫读，缺少清单化结构。")
            suggestions.append("把核心好处或判断改成 3 点清单。")

        if len(paragraphs) < 5:
            score -= 8
            issues.append("段落层次偏少，信息密度不足。")
            suggestions.append("至少拆成 5 段，让逻辑更清楚。")

        if len(title) < 12 or len(title) > 30:
            score -= 6
            issues.append("标题长度不理想，点击预期一般。")
            suggestions.append("把标题控制在 12 到 30 个字，保留冲突和收益点。")

        phrase_hits = [phrase for phrase in self.AI_PHRASES if phrase in body]
        if phrase_hits:
            score -= 8
            issues.append(f"AI 套话偏多：{', '.join(phrase_hits)}")
            suggestions.append("删除“首先/其次/总之”等套话，换成更口语的表达。")

        if "我" not in body:
            score -= 12
            issues.append("第一视角不够强，缺少真人代入感。")
            suggestions.append("强化第一人称和当下情绪。")

        if len(content.tags) < 3:
            score -= 4
            issues.append("标签偏少，不利于完整发布。")
            suggestions.append("补足 3 到 5 个相关标签。")

        if len(body) > int(self.settings.get("content", "max_body_length", 1000)):
            score -= 10
            issues.append("正文过长，超过发布目标长度。")
            suggestions.append("压到 1000 字以内，只保留最能促成收藏的信息。")

        if len(paragraphs) >= 4:
            buckets = {len(paragraph) // 20 for paragraph in paragraphs}
            if len(buckets) <= 2:
                score -= 6
                issues.append("段落长度太平均，读起来有 AI 排版感。")
                suggestions.append("让段落长短更有变化，像真人写作。")

        threshold = int(self.settings.get("quality", "originality_threshold", 70))
        score = max(score, 0)
        result = ReviewResult(
            passed=(score >= threshold and not sensitive_hits),
            score=score,
            issues=issues or ["结构完整，具备发布条件。"],
            suggestions=suggestions or ["维持现在的口语感即可。"],
        )
        self.logger.info("Reviewed content with score %s", score)
        return result

    def _load_sensitive_words(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as file:
            return [line.strip() for line in file if line.strip()]
