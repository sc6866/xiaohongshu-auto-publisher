from __future__ import annotations

from common.models import TopicCandidate
from common.utils import deterministic_score

from agents.base import BaseAgent


class TrendScanner(BaseAgent):
    def scan(self, manual_topics: list[str] | None = None) -> list[TopicCandidate]:
        seed_topics = self.settings.get("scanner", "default_seed_topics", [])
        allowed = {item.lower() for item in self.settings.get("scanner", "allowed_competition", ["low", "medium"])}
        min_heat = int(self.settings.get("scanner", "min_heat_score", 60))
        topics: list[TopicCandidate] = []

        if manual_topics:
            for topic in manual_topics:
                category = self._infer_category(topic)
                heat_score = deterministic_score(topic, max(min_heat, 68), 96)
                competition = "low" if heat_score % 4 == 0 else "medium"
                topics.append(
                    TopicCandidate(
                        keyword=topic,
                        heat_score=heat_score,
                        competition=competition,
                        angle_suggestion=self._build_angle(topic, category),
                        sample_notes=self._build_samples(topic, category),
                        source="manual",
                        category=category,
                    )
                )
        else:
            for item in seed_topics:
                keyword = str(item.get("keyword", "")).strip()
                if not keyword:
                    continue
                category = str(item.get("category", self._infer_category(keyword)))
                heat_score = deterministic_score(keyword, 58, 96)
                competition = "low" if heat_score % 3 == 0 else "medium"
                topics.append(
                    TopicCandidate(
                        keyword=keyword,
                        heat_score=heat_score,
                        competition=competition,
                        angle_suggestion=self._build_angle(keyword, category),
                        sample_notes=self._build_samples(keyword, category),
                        category=category,
                    )
                )

        filtered = [
            topic
            for topic in topics
            if topic.competition.lower() in allowed and topic.heat_score >= min_heat
        ]
        filtered.sort(key=lambda topic: topic.heat_score, reverse=True)
        self.logger.info("Scanned %s candidate topics", len(filtered))
        return filtered

    def _infer_category(self, topic: str) -> str:
        normalized = topic.lower()
        if any(token in normalized for token in ("开封", "旅游", "探店", "租车", "攻略", "出行")):
            return "travel"
        if any(token in normalized for token in ("护肤", "美妆", "穿搭")):
            return "beauty"
        if any(token in normalized for token in ("ipad", "学习", "效率", "数码")):
            return "tech"
        if any(token in normalized for token in ("简历", "秋招", "面试", "职场")):
            return "career"
        if any(token in normalized for token in ("卧室", "桌面", "收纳", "租房", "宿舍")):
            return "home"
        return "lifestyle"

    def _build_angle(self, topic: str, category: str) -> str:
        if category == "travel":
            return f"别只写“方便”，把 {topic} 拆成省腿、省时间、少踩坑三条价值线。"
        if category == "beauty":
            return f"围绕预算、肤质和踩坑成本来写 {topic}，少讲空泛种草，多讲真实变化。"
        if category == "tech":
            return f"把 {topic} 做成效率前后对比，用场景和步骤替代功能堆砌。"
        if category == "career":
            return f"把 {topic} 写成避坑清单，优先讲错法、后果和替代方案。"
        if category == "home":
            return f"把 {topic} 做成低预算改造案例，强调前后变化和花费区间。"
        return f"从真实生活场景切入 {topic}，避免空泛总结，多给数字和行动建议。"

    def _build_samples(self, topic: str, category: str) -> list[str]:
        if category == "travel":
            return [
                f"{topic} 值不值，重点讲路线、价格和还车点。",
                f"{topic} 更容易出收藏的角度通常是“少踩坑”，不是“真好玩”。",
            ]
        if category == "beauty":
            return [
                f"{topic} 把适合谁和不适合谁说清楚，更像真人经验。",
                f"{topic} 把预算和实际效果讲明白，比单纯种草更容易被收藏。",
            ]
        if category == "career":
            return [
                f"{topic} 先写常见错误，再给替代方案，冲突感更强。",
                f"{topic} 标题里带“别这样”“少踩坑”通常更有点击欲望。",
            ]
        return [
            f"{topic} 最好从一个真实场景切入，不要上来就总结。",
            f"{topic} 标题、封面、正文都要围绕一个明确收益点展开。",
        ]
