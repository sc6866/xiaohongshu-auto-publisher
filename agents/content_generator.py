from __future__ import annotations

import itertools
import json
import re
from typing import Any

from common.glm_text_client import GlmTextClient
from common.models import GeneratedContent, ReviewResult
from common.qwen_text_client import QwenTextClient
from common.utils import clean_text, deterministic_score

from agents.base import BaseAgent


PRICE_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*元(?:/\s*(?:天|次|晚))?")
COUNT_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:分钟|小时|次|天|个|公里|站)")
PLACE_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,10}(?:景区|古城|夜市|大街|小巷|书店街|城墙|河园|公园)")

PERSONA_LIBRARY: dict[str, list[dict[str, str]]] = {
    "travel": [
        {"identity": "第一次做本地攻略的周末出行党", "scene": "周六上午 9 点，手机里存了 4 个想去的点", "emotion": "又怕折腾，又想把一天玩值"},
        {"identity": "带着长辈一起逛城的轻旅游用户", "scene": "中午 12 点前后，已经走得有点累", "emotion": "不想让同行的人继续硬撑"},
    ],
    "beauty": [
        {"identity": "预算有限但不想乱买的学生党", "scene": "周三晚上 10 点，洗完脸还在看测评", "emotion": "怕花了钱却越用越乱"},
        {"identity": "刚开始建立护肤步骤的新手", "scene": "宿舍桌前，手边摆着好几样没用明白的东西", "emotion": "想变好看，但更怕踩雷"},
    ],
    "home": [
        {"identity": "住出租屋、预算卡得很紧的打工人", "scene": "下班回家后只剩一点点精力收拾房间", "emotion": "想改善环境，又不想花冤枉钱"},
        {"identity": "想让桌面更顺手的宿舍党", "scene": "晚上整理桌面时，东西总是越放越乱", "emotion": "有点烦，但还不想放弃"},
    ],
    "career": [
        {"identity": "第一次参加秋招的应届生", "scene": "投递前一晚，还在来回改简历", "emotion": "怕自己犯的是最基础的错"},
        {"identity": "工作经验不多但想提高命中率的求职者", "scene": "面试邀约一直不稳定的阶段", "emotion": "既着急，又不敢乱改"},
    ],
    "tech": [
        {"identity": "想把 iPad 真正用起来的学生", "scene": "晚自习结束后，准备整理学习流程", "emotion": "不想设备只剩下追剧功能"},
        {"identity": "常常在多个 app 间来回切的效率党", "scene": "周一早上准备新一周任务的时候", "emotion": "很想提效，但最怕步骤太碎"},
    ],
    "lifestyle": [
        {"identity": "最近在认真优化生活细节的普通人", "scene": "一个看起来很普通但很容易踩坑的时刻", "emotion": "嘴上说随便，心里其实很在意结果"},
        {"identity": "对新方法半信半疑的体验派", "scene": "准备尝试前一直在翻别人经验", "emotion": "想少走弯路，也想自己试明白"},
    ],
}


class ContentGenerator(BaseAgent):
    def __init__(self, settings, db, vector_store):
        super().__init__(settings, db, vector_store)
        self.qwen_text_client = QwenTextClient(settings)
        self.glm_text_client = GlmTextClient(settings)

    def generate(self, topic: str) -> GeneratedContent:
        retrievals = self._load_retrievals(topic)
        content = self._build_content(topic, retrievals)
        self.logger.info("Generated content for topic '%s'", topic)
        return content

    def generate_from_image_brief(
        self,
        image_brief: dict[str, Any],
        angle: str | None = None,
        style_strength: str | None = None,
    ) -> GeneratedContent:
        return self._generate_image_article(
            image_brief=image_brief,
            angle=angle,
            style_strength=style_strength,
            revision_focus=None,
        )

    def rewrite_image_article(
        self,
        image_brief: dict[str, Any],
        content: GeneratedContent,
        review: ReviewResult,
        angle: str | None = None,
        style_strength: str | None = None,
    ) -> GeneratedContent:
        revised = self._generate_image_article(
            image_brief=image_brief,
            angle=angle,
            style_strength=style_strength,
            revision_focus=review.suggestions,
        )
        revised.review_history = list(content.review_history)
        return revised

    def _generate_image_article(
        self,
        image_brief: dict[str, Any],
        angle: str | None,
        style_strength: str | None,
        revision_focus: list[str] | None,
    ) -> GeneratedContent:
        topic = str(image_brief.get("topic") or "图片灵感内容")
        content_mode = str(image_brief.get("content_mode") or "lifestyle_note")
        summary = str(image_brief.get("summary") or "")
        keywords = [str(item) for item in image_brief.get("keywords", []) if str(item).strip()]
        visible_text = [str(item) for item in image_brief.get("visible_text", []) if str(item).strip()]
        facts = image_brief.get("facts", {}) if isinstance(image_brief.get("facts"), dict) else {}

        llm_result = self._generate_image_article_with_llm(
            topic=topic,
            content_mode=content_mode,
            summary=summary,
            keywords=keywords,
            visible_text=visible_text,
            facts=facts,
            angle=angle,
            style_strength=style_strength,
            revision_focus=revision_focus,
        )
        if llm_result is not None:
            return llm_result

        if content_mode == "travel_guide":
            return self._generate_travel_from_image(topic, summary, keywords, visible_text, facts, angle)
        if content_mode == "product_review":
            return self._generate_product_review_from_image(topic, summary, keywords, visible_text, facts, angle)
        return self._generate_lifestyle_from_image(topic, summary, keywords, visible_text, facts, angle)

    def rewrite(self, topic: str, content: GeneratedContent, review: ReviewResult) -> GeneratedContent:
        retrievals = self._load_retrievals(topic)
        revised = self._build_content(
            topic,
            retrievals,
            revision_focus=review.suggestions,
            variation_seed=len(content.review_history) + 1,
        )
        revised.review_history = list(content.review_history)
        return revised

    def _load_retrievals(self, topic: str) -> list[dict[str, object]]:
        top_k = int(self.settings.get("content", "retrieval_top_k", 5))
        topic_rows = self.db.list_knowledge_sources(topic=topic, limit=top_k)
        if topic_rows:
            return [
                {
                    "score": 0.0,
                    "metadata": {
                        "source_url": row["source_url"],
                        "tags": row["tags_json"],
                        "topic": row["topic"],
                    },
                    "text": row["body"],
                }
                for row in topic_rows
            ]

        retrievals = self.vector_store.search(topic, top_k=top_k)
        if retrievals:
            return retrievals

        fallback = self.db.list_knowledge_sources(limit=top_k)
        return [
            {
                "score": 0.0,
                "metadata": {
                    "source_url": row["source_url"],
                    "tags": row["tags_json"],
                    "topic": row["topic"],
                },
                "text": row["body"],
            }
            for row in fallback
        ]

    def _build_content(
        self,
        topic: str,
        retrievals: list[dict[str, object]],
        revision_focus: list[str] | None = None,
        variation_seed: int = 0,
    ) -> GeneratedContent:
        category = self._infer_category(topic, retrievals)
        persona = self._pick_persona(topic, category, variation_seed)
        snippets = [str(item.get("text") or "") for item in retrievals[:3] if item.get("text")]
        sources = [
            str(item.get("metadata", {}).get("source_url"))
            for item in retrievals[:3]
            if isinstance(item.get("metadata"), dict) and item.get("metadata", {}).get("source_url")
        ]
        facts = self._extract_facts(snippets, category)
        tags = self._build_tags(topic, category, retrievals)

        return GeneratedContent(
            title=self._compose_title(topic, category, facts, variation_seed),
            body=self._compose_body(topic, category, persona, facts, revision_focus),
            tags=tags,
            referenced_sources=sources,
            persona=persona,
        )

    def _infer_category(self, topic: str, retrievals: list[dict[str, object]]) -> str:
        joined = " ".join([topic, *self._collect_retrieval_tags(retrievals)]).lower()
        if any(token in joined for token in ("开封", "旅游", "探店", "租车", "攻略", "出行", "三轮")):
            return "travel"
        if any(token in joined for token in ("护肤", "美妆", "穿搭")):
            return "beauty"
        if any(token in joined for token in ("ipad", "学习", "效率", "数码")):
            return "tech"
        if any(token in joined for token in ("简历", "秋招", "面试", "职场")):
            return "career"
        if any(token in joined for token in ("卧室", "桌面", "收纳", "租房", "宿舍")):
            return "home"
        return "lifestyle"

    def _collect_retrieval_tags(self, retrievals: list[dict[str, object]]) -> list[str]:
        tags: list[str] = []
        for item in retrievals:
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            raw_tags = metadata.get("tags")
            if isinstance(raw_tags, str):
                try:
                    raw_tags = json.loads(raw_tags)
                except json.JSONDecodeError:
                    raw_tags = [raw_tags]
            if isinstance(raw_tags, list):
                tags.extend(str(tag) for tag in raw_tags)
        return tags

    def _pick_persona(self, topic: str, category: str, variation_seed: int) -> dict[str, str]:
        library = PERSONA_LIBRARY.get(category, PERSONA_LIBRARY["lifestyle"])
        index = deterministic_score(f"{topic}-{variation_seed}", 0, len(library) - 1)
        return library[index]

    def _extract_facts(self, snippets: list[str], category: str) -> dict[str, list[str]]:
        joined = "\n".join(clean_text(snippet) for snippet in snippets if snippet)
        prices = self._unique_matches(PRICE_PATTERN.findall(joined))
        counts = self._unique_matches(COUNT_PATTERN.findall(joined))
        places = self._unique_matches(PLACE_PATTERN.findall(joined))

        if not prices:
            prices = ["35元/天", "60元/天"] if category == "travel" else ["69元", "158元"]
        if not counts:
            counts = ["3次", "20分钟"] if category != "travel" else ["1天", "3个点位"]
        if category == "travel":
            places = self._sanitize_places(places) or ["景点", "夜市", "古城小街"]

        return {
            "prices": prices[:3],
            "counts": counts[:4],
            "places": places[:4],
        }

    def _unique_matches(self, matches: list[str]) -> list[str]:
        result: list[str] = []
        for match in matches:
            value = clean_text(match)
            if value and value not in result:
                result.append(value)
        return result

    def _sanitize_places(self, places: list[str]) -> list[str]:
        cleaned: list[str] = []
        for place in places:
            if any(token in place for token in ("第一次", "谁说", "这才是", "年轻人", "满大街")):
                continue
            if 2 <= len(place) <= 8 and place not in cleaned:
                cleaned.append(place)
        return cleaned

    def _build_tags(self, topic: str, category: str, retrievals: list[dict[str, object]]) -> list[str]:
        tags = [topic]

        if category == "travel":
            for tag in ("开封旅游", "本地出行", "租车攻略", "旅行避坑"):
                if tag not in tags and len(tags) < 5:
                    tags.append(tag)
        elif category == "beauty":
            for tag in ("学生党", "护肤分享", "不踩雷", "预算内升级"):
                if tag not in tags and len(tags) < 5:
                    tags.append(tag)
        elif category == "career":
            for tag in ("求职避坑", "简历优化", "秋招", "经验分享"):
                if tag not in tags and len(tags) < 5:
                    tags.append(tag)
        elif category == "home":
            for tag in ("租房改造", "桌面收纳", "低预算", "生活灵感"):
                if tag not in tags and len(tags) < 5:
                    tags.append(tag)
        elif category == "tech":
            for tag in ("效率工具", "iPad", "学习方法", "数码技巧"):
                if tag not in tags and len(tags) < 5:
                    tags.append(tag)

        for tag in self._collect_retrieval_tags(retrievals):
            if tag not in tags and len(tags) < 5:
                tags.append(tag)

        defaults = self.settings.get("content", "default_tags", [])
        for tag in itertools.chain(defaults):
            if tag not in tags and len(tags) < 5:
                tags.append(tag)
        return tags[:5]

    def _compose_title(self, topic: str, category: str, facts: dict[str, list[str]], variation_seed: int) -> str:
        count = facts["counts"][0]

        if category == "travel":
            city = "开封" if "开封" in topic else "本地"
            thing = "租电动三轮车" if "三轮" in topic else topic
            candidates = [
                f"来{city}别急着打车，{thing}真的很省腿",
                f"{city}{thing}值不值？我试了{count}后只想说真省心",
                f"{city}{thing}攻略：先问这4件事，再决定租不租",
            ]
        elif category == "beauty":
            candidates = [
                f"{topic}别乱抄作业，我试到{count}才知道关键在哪",
                f"{topic}不是越贵越好，我用两档预算试明白了",
                f"{topic}想少踩坑，先看我删掉了哪一步",
            ]
        elif category == "career":
            candidates = [
                f"{topic}别这样写，我改了{count}后命中率才上来",
                f"{topic}最容易扣分的，不是你以为的那一项",
                f"{topic}想过筛，先把这3个错误删掉",
            ]
        else:
            candidates = [
                f"{topic}：我试了{count}后才知道，最该改的是这一点",
                f"{topic}别急着照抄，我最后留下来的只有这3点",
                f"{topic}真的不是越多越好，做减法反而更顺手",
            ]

        index = deterministic_score(f"{topic}-title-{variation_seed}", 0, len(candidates) - 1)
        return candidates[index]

    def _compose_body(
        self,
        topic: str,
        category: str,
        persona: dict[str, str],
        facts: dict[str, list[str]],
        revision_focus: list[str] | None,
    ) -> str:
        if category == "travel":
            body = self._compose_travel_body(topic, persona, facts, revision_focus)
        else:
            body = self._compose_generic_body(topic, category, persona, facts, revision_focus)

        max_length = int(self.settings.get("content", "max_body_length", 1000))
        return clean_text(body[:max_length])

    def _compose_travel_body(
        self,
        topic: str,
        persona: dict[str, str],
        facts: dict[str, list[str]],
        revision_focus: list[str] | None,
    ) -> str:
        price_a, price_b = self._travel_price_range(topic, facts["prices"])
        count_a, count_b = self._travel_counts(topic, facts["counts"])
        places = self._travel_places(topic)
        focus = " ".join(revision_focus or [])
        extra_prompt = "开头我就想把话说重一点：" if "开头" in focus or "钩子" in focus else ""
        emojis = self._emoji_pack("travel")

        return f"""
        {extra_prompt}{emojis["hook"]} 来开封之前，我真的以为“租电动三轮车”只是图个新鲜，真要在城里跑还是打车更省心。结果我自己跑了 {count_a}，还把路线拉到了 {count_b}，才发现它最香的不是便宜，是省腿、省时间，而且整天的节奏会轻松很多。

        我这次的状态很典型：{persona["identity"]}，{persona["scene"]}。如果你跟我一样，一天里想跑 {places} 这种多个点位，或者同行里有长辈、小朋友，真的会很在意中间那种“别再来回折腾”的感觉。

        {emojis["data"]} 热门笔记里反复提到的价格大多落在 {price_a} 到 {price_b}。我后面越看越明白，低价不是第一位，车况、还车点和中途出问题怎么办，才是决定体验的关键。

        {emojis["list"]} 我觉得它最值的 3 个好处：
        1. 点位切换更自由。临时想拐去一条小街或者换个吃饭点，不用重新叫车。
        2. 预算更好控。一天要是来回跑，打车很容易越算越高，租车反而更容易心里有数。
        3. 带东西更轻松。水、外套、伴手礼都能先放车上，不用一路拎着。

        {emojis["turn"]} 我当时以为只要问到便宜价格就够了，结果发现真正影响体验的，是出发前 5 分钟有没有把电量、刹车、续航和还车点问清楚。这个细节没确认，后面再便宜也容易手忙脚乱。

        {emojis["warn"]} 但它不一定适合所有人。如果你只打算在一个景区慢慢逛，或者完全不熟路、又只想轻装拍照，那步行加短程打车可能更省心。要是你想把一天玩得更满、又不想被来回等车打断节奏，电动三轮车是真的友好。

        {emojis["tip"]} 我自己的建议很简单，先问 4 件事：多少钱一天、能不能中途换电、出了问题找谁、最后在哪还车。把这 4 个问题问完，再决定租不租，基本就不会太踩坑。
        """

    def _travel_places(self, topic: str) -> str:
        if "开封" in topic:
            return "清明上河园、龙亭公园、夜市"
        return "景点、夜市和古城小街"

    def _travel_price_range(self, topic: str, prices: list[str]) -> tuple[str, str]:
        if "开封" in topic:
            return "35元/天", "60元/天"
        if len(prices) >= 2 and prices[0] != prices[1]:
            return prices[0], prices[1]
        if prices:
            return prices[0], "100元/天"
        return "39元/天", "69元/天"

    def _travel_counts(self, topic: str, counts: list[str]) -> tuple[str, str]:
        if "开封" in topic:
            return "1天", "3个点位"
        if len(counts) >= 2:
            return counts[0], counts[1]
        if counts:
            return counts[0], "3个点位"
        return "1天", "3个点位"

    def _compose_generic_body(
        self,
        topic: str,
        category: str,
        persona: dict[str, str],
        facts: dict[str, list[str]],
        revision_focus: list[str] | None,
    ) -> str:
        price_a = facts["prices"][0]
        price_b = facts["prices"][1] if len(facts["prices"]) > 1 else "158元"
        count_a = facts["counts"][0]
        count_b = facts["counts"][1] if len(facts["counts"]) > 1 else "20分钟"
        benefits = self._generic_benefits(category)
        focus = " ".join(revision_focus or [])
        hook = "我先说结论之前，想先把反差讲出来。" if "钩子" in focus or "开头" in focus else ""
        emojis = self._emoji_pack(category)

        return f"""
        {hook}{emojis["hook"]} 我是那种会为了一个小问题反复试很多遍的人。最近折腾 {topic} 的时候，我就是 {persona["identity"]}，{persona["scene"]}，当时最明显的感觉是：{persona["emotion"]}。

        {emojis["data"]} 我前后试了 {count_a}，预算从 {price_a} 拉到 {price_b}，最后才发现真正影响结果的，往往不是你一开始最在意的那一项。

        {emojis["turn"]} 我当时以为只要照着热门内容把步骤堆满就够了，结果发现真正决定体验的，是顺序、取舍和有没有把动作做小。很多东西不是越多越好，而是越稳越好。

        {emojis["list"]} 我最后留下来的 3 个判断：
        1. {benefits[0]}
        2. {benefits[1]}
        3. {benefits[2]}

        最直接的变化是，原本我要花 {count_b} 才能收住，现在反而更容易一次做对。这个体感差别，只有自己试过才会明白。

        {emojis["warn"]} 但它不一定适合所有人。如果你现在还没有明确目标，或者只是临时起意想跟风照搬，那大概率会越做越乱。更适合的是那种已经踩过一点坑、想把流程真正做顺的人。

        {emojis["tip"]} 如果你问我最值得先改哪一步，我会说先删掉最花力气但最不稳定的那一步，再看结果有没有变好。很多时候，先做减法，比继续叠加更有用。
        """

    def _generic_benefits(self, category: str) -> list[str]:
        if category == "beauty":
            return [
                "先把预算和肤质对上，比盲目加步骤更省钱。",
                "把真正有体感的 2 到 3 个动作留下来，皮肤状态反而更稳。",
                "明确适合谁和不适合谁，能少掉很多无效尝试。",
            ]
        if category == "career":
            return [
                "先删掉最容易扣分的表述，比继续堆经历更有效。",
                "把结果写具体，比空泛写努力和认真更能打动人。",
                "让每一段都有明确作用，简历读起来才不会散。",
            ]
        if category == "home":
            return [
                "先改最影响日常顺手度的那一块，体感提升最快。",
                "预算压低一点反而更容易坚持，不会做完一次就停。",
                "让收纳和动线一起变顺，比只追求好看更耐用。",
            ]
        if category == "tech":
            return [
                "流程少一点但固定下来，效率提升会比换一堆工具更明显。",
                "把常用动作集中到一个入口，比来回切 app 更稳。",
                "先解决最卡手的一个点，后面的使用频率自然会起来。",
            ]
        return [
            "先找最影响体验的那个小环节，通常比整体推翻重来更有效。",
            "有数字、有前后对比，才更像真人经验，不像空话。",
            "说清楚适合谁和不适合谁，内容反而更有信任感。",
        ]

    def _emoji_pack(self, category: str) -> dict[str, str]:
        if category == "travel":
            return {"hook": "🚲", "data": "💰", "list": "✅", "turn": "😅", "warn": "⚠️", "tip": "📝"}
        if category == "beauty":
            return {"hook": "🧴", "data": "💸", "list": "✅", "turn": "😵", "warn": "⚠️", "tip": "📝"}
        if category == "tech":
            return {"hook": "📱", "data": "⏱️", "list": "✅", "turn": "😮", "warn": "⚠️", "tip": "📝"}
        if category == "career":
            return {"hook": "📄", "data": "📌", "list": "✅", "turn": "😵", "warn": "⚠️", "tip": "📝"}
        if category == "home":
            return {"hook": "🏠", "data": "💰", "list": "✅", "turn": "😮", "warn": "⚠️", "tip": "📝"}
        return {"hook": "✨", "data": "📌", "list": "✅", "turn": "😮", "warn": "⚠️", "tip": "📝"}

    def _generate_product_review_from_image(
        self,
        topic: str,
        summary: str,
        keywords: list[str],
        visible_text: list[str],
        facts: dict[str, Any],
        angle: str | None,
    ) -> GeneratedContent:
        persona = self._pick_persona(topic, "lifestyle", 0)
        product_name = self._coerce_text(facts.get("product_type")) or (keywords[0] if keywords else topic)
        audience = self._coerce_text(facts.get("audience")) or "预算敏感、但想买得更稳的人"
        selling_points = self._coerce_list_or_default(
            facts.get("selling_points"),
            ["省腿省时", "更自由", "路线和还车点更清楚"],
        )
        selling_points = self._pad_list(
            selling_points,
            ["省腿省时", "更自由", "路线和还车点更清楚"],
            minimum=3,
        )
        risk_points = self._coerce_list_or_default(
            facts.get("risk_points"),
            ["隐性收费", "还车点不方便", "路线绕路"],
        )
        risk_points = self._pad_list(
            risk_points,
            ["隐性收费", "还车点不方便", "路线绕路"],
            minimum=3,
        )
        location = self._coerce_text(facts.get("location")) or topic
        price = self._coerce_text(facts.get("price")) or "35元到60元"
        scene = self._coerce_text(facts.get("scene")) or "景点之间来回跑"
        title = self._short_image_title(topic, fallback=f"{product_name}到底值不值")
        if self._looks_like_service_review(product_name, scene, location):
            body = clean_text(
                f"""
                🛍️ 这次我在 {location} 真正试了一次 {product_name}，先说结论：如果你当天打算多跑几个点位，它确实会比一路临时打车更省腿，也更容易控预算。

                我当时的状态很典型，{persona["scene"]}，心里想的是“别再把时间浪费在等车和走回头路上了”。结果真跑下来以后，最有体感的不是新鲜感，而是中途切换点位的时候，人没那么累，节奏也不会一直被打断。

                💰 我看到的信息里，大家最关心的还是价格和省不省事。像这类服务，通常会把 {price}、路线、还车点写得很醒目，但真正决定体验的，是你有没有提前把规则问细。

                ✅ 我自己觉得最值的 3 个点：
                1. {selling_points[0]}，尤其是像 {scene} 这种场景，差别会特别明显。
                2. {selling_points[1]}，临时改路线或者带着同行的人时会轻松很多。
                3. {selling_points[2]}，只要出发前问明白，后面基本不会太慌。

                😅 我当时以为只要价格合适就能直接定，结果发现真正容易踩坑的是这些细节：{risk_points[0]}、{risk_points[1]}、还有 {risk_points[2]}。这些不问清楚，前面省下来的时间，后面很可能又补回去了。

                ⚠️ 但它不一定适合所有人。如果你当天就打算慢慢逛一个点，或者完全不想操心路线，那更轻量的出行方式可能反而省心。更适合的是 {audience}。

                📝 我最后的建议是，别只问“多少钱”，至少把规则、使用范围、临时变化怎么处理这几件事问完再决定。把这些细节问清楚，这类体验基本就能从“怕踩坑”变成“真的省心”。
                """
            )
        else:
            body = clean_text(
                f"""
                🛍️ 这次我是真的把 {product_name} 放进日常里试了几次，先说结论：如果你刚好也被 {scene} 这种小问题反复卡住，它不是那种买完立刻封神的东西，但确实能把过程变顺一点。

                我自己的状态很典型，{persona["scene"]}，当时最想解决的就是“别再为了这种小事来回折腾”。结果试下来以后，我最明显的感受不是惊艳，而是顺手。那种原本懒得弄、最后越积越烦的小问题，终于没那么磨人了。

                💰 价格这块我会先看 {price} 是不是和实际体验匹配。很多商品第一眼会把卖点写得很满，但真正值不值，还是要看你用起来有没有体感，而不是文案看着热闹。

                ✅ 我自己觉得比较有用的 3 个点：
                1. {selling_points[0]}，这个是我第一次用完就能感觉到的。
                2. {selling_points[1]}，不是那种夸张变化，但会让你后面更愿意继续用。
                3. {selling_points[2]}，如果你跟我一样在意细节，这一点会比想象中更重要。

                😅 我当时以为只要卖点对胃口就能直接下单，结果真正需要留神的还是这些地方：{risk_points[0]}、{risk_points[1]}、还有 {risk_points[2]}。尤其是图片里没重点说的部分，往往才最影响到手体验。

                ⚠️ 但它不一定适合所有人。如果你对这类问题本来就不敏感，或者现有方案已经够用，那未必需要急着换。更适合的是 {audience}，或者已经被同一个细节反复烦到的人。

                📝 我自己的建议是，别只看第一眼有没有被种草，最好先看 3 件事：使用场景合不合适、最容易翻车的点有没有被说清楚、买回来之后你会不会真的愿意常用。把这 3 个问题想明白，再决定值不值得入手。
                """
        )
        tags = [topic, "真人测评", "购物避坑", "值不值得买", "经验分享"]
        return GeneratedContent(
            title=f"{title}值不值？我试完后的真实感受",
            body=body[: int(self.settings.get("content", "max_body_length", 1000))],
            tags=tags,
            referenced_sources=[str(item) for item in facts.get("images", []) if str(item).strip()],
            persona=persona,
        )

    def _generate_travel_from_image(
        self,
        topic: str,
        summary: str,
        keywords: list[str],
        visible_text: list[str],
        facts: dict[str, Any],
        angle: str | None,
    ) -> GeneratedContent:
        persona = self._pick_persona(topic, "travel", 0)
        place = self._coerce_text(facts.get("location")) or (keywords[0] if keywords else topic)
        scene = self._coerce_text(facts.get("scene")) or "适合边走边逛"
        visible_hint = "、".join(visible_text[:3]) or "路线、时间和踩坑点"
        title = f"{self._short_image_title(place, fallback=place)}一日怎么安排更顺"
        body = clean_text(
            f"""
            🧳 如果你来 {place} 是想少走回头路、一天多跑几个点，我会更建议你提前把路线顺好再出门。光看图片会觉得哪里都想去，但真正到了现场，最累的往往不是景点本身，而是来回折腾。

            我这次的体感很明确，{persona["identity"]} 的那种赶行程焦虑真的会出现。尤其是像 {scene} 这种玩法，看着轻松，实际一旦路线没排好，中途就很容易又热又乱。

            📍 我会先盯住 3 件事：什么时候去人少、点位之间怎么接、哪些地方值得停，哪些地方其实路过就够。像图里这种信息点，{visible_hint}，都比单纯说“很出片”更有用。

            ✅ 如果你是第一次去，我会这样安排：
            1. 上午先去最想看的主点，趁体力还在的时候把需要排队和步行多的地方先走掉。
            2. 中间留一段吃饭和休息时间，不要把路线排得太满，不然后面拍照和逛街都会变成赶任务。
            3. 傍晚再去更适合氛围感和夜景的地方，体感会比中午硬冲舒服很多。

            😅 我当时以为旅行攻略最重要的是“推荐什么”，结果发现大家真正想收藏的，反而是“怎么走更省时间、哪里容易踩坑、什么时候最舒服”。这几个信息一补齐，整篇内容就会立刻实用很多。

            ⚠️ 但这个安排不一定适合所有人。如果你是纯拍照型行程，节奏可以更松；如果你带长辈或小朋友，路线就更要保守一点，宁可少去一个点，也别把人走崩。

            📝 我自己的经验是，旅行攻略别写成“景点清单”，而是写成“真实的一天怎么过”。把时间、顺路关系和体感说清楚，哪怕只有 3 个点，读者也会觉得这篇是真的能拿去用。
            """
        )
        tags = [topic, "旅行攻略", "拍照打卡", "出行建议", "经验分享"]
        return GeneratedContent(
            title=title,
            body=body[: int(self.settings.get("content", "max_body_length", 1000))],
            tags=tags,
            referenced_sources=[str(item) for item in facts.get("images", []) if str(item).strip()],
            persona=persona,
        )

    def _generate_lifestyle_from_image(
        self,
        topic: str,
        summary: str,
        keywords: list[str],
        visible_text: list[str],
        facts: dict[str, Any],
        angle: str | None,
    ) -> GeneratedContent:
        persona = self._pick_persona(topic, "lifestyle", 0)
        hook = keywords[0] if keywords else topic
        scene = self._coerce_text(facts.get("scene")) or "一个很普通但很有体感的瞬间"
        title = f"{self._short_image_title(hook, fallback=hook)}让我改掉了一个习惯"
        body = clean_text(
            f"""
            ✨ 这张图其实很像我最近生活里一个特别普通、但真的有体感变化的瞬间。不是那种一夜之间变厉害的故事，而是慢慢发现“原来这样做，日子真的会顺一点”。

            我当时的状态就是 {persona["identity"]}，场景也很像 {scene}。一开始我以为只是一个小调整，结果坚持几次之后，反而是那种最容易烦躁的小问题先被解决了。

            📌 这种内容最适合写真实细节。比如我什么时候开始这样做、前后差别到底在哪、哪一步是我原本以为没必要、后来才发现最关键的。

            ✅ 我最后留下来的 3 个感受：
            1. 不是每次都要做很多，先把最影响体感的那一步改掉就很有用。
            2. 真正能坚持下来的方法，一定是顺手的，而不是看起来很厉害的。
            3. 说清楚适合谁、不适合谁，比一味夸“好用”更有参考价值。

            😮 我当时以为这种变化没什么好写的，结果后来才发现，大家最想看的恰恰就是这种真实又不夸张的过程。不是大道理，而是“我试过，确实有点用”。

            ⚠️ 但它不一定适合所有人。如果你现在还没到真的想调整的时候，硬抄大概率坚持不住。更适合的是已经被同一个小问题困扰过几次，愿意慢慢试的人。

            📝 所以我现在更愿意把这类内容写成真实记录：少一点模板感，多一点具体时刻。哪怕只有一个小变化，只要讲清楚前后差别，读者也会更愿意停下来。
            """
        )
        tags = [topic, "图片灵感", "真实体验", "内容创作", "经验分享"]
        return GeneratedContent(
            title=title,
            body=body[: int(self.settings.get("content", "max_body_length", 1000))],
            tags=tags,
            referenced_sources=[str(item) for item in facts.get("images", []) if str(item).strip()],
            persona=persona,
        )

    def _coerce_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            return "、".join(cleaned)
        return str(value).strip()

    def _coerce_list_or_default(self, value: Any, default: list[str]) -> list[str]:
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            if cleaned:
                return cleaned
        return default

    def _pad_list(self, value: list[str], fallback: list[str], minimum: int) -> list[str]:
        result = [item for item in value if item]
        for item in fallback:
            if item not in result:
                result.append(item)
            if len(result) >= minimum:
                break
        if not result:
            result = list(fallback)
        return result[: max(minimum, len(result))]

    def _short_image_title(self, text: str, fallback: str) -> str:
        cleaned = re.sub(r"[，。！？：；、\s]+", "", text or "")
        if not cleaned:
            cleaned = fallback
        return cleaned[:12]

    def _looks_like_service_review(self, product_name: str, scene: str, location: str) -> bool:
        joined = " ".join([product_name, scene, location]).lower()
        return any(
            token in joined
            for token in (
                "租",
                "租赁",
                "三轮",
                "出行",
                "接驳",
                "路线",
                "还车",
                "景点",
                "旅游",
                "交通",
                "服务",
            )
        )

    def _generate_image_article_with_llm(
        self,
        topic: str,
        content_mode: str,
        summary: str,
        keywords: list[str],
        visible_text: list[str],
        facts: dict[str, Any],
        angle: str | None,
        style_strength: str | None,
        revision_focus: list[str] | None,
    ) -> GeneratedContent | None:
        writer = self._select_writer_client()
        if writer is None or not writer.is_configured():
            return None

        system_prompt = self._image_writer_system_prompt()
        prompt = self._image_writer_user_prompt(
            topic=topic,
            content_mode=content_mode,
            summary=summary,
            keywords=keywords,
            visible_text=visible_text,
            facts=facts,
            angle=angle,
            style_strength=style_strength,
            revision_focus=revision_focus,
        )
        try:
            payload = writer.generate_json(prompt=prompt, system_prompt=system_prompt)
            return self._generated_content_from_llm_payload(
                payload=payload,
                topic=topic,
                facts=facts,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Image article writer failed, fallback to template: %s", exc)
            return None

    def _select_writer_client(self) -> Any | None:
        provider = str(self.settings.get("writer", "provider", "qwen")).strip().lower()
        ordered = {
            "qwen": self.qwen_text_client,
            "glm": self.glm_text_client,
        }
        chosen = ordered.get(provider)
        if chosen and chosen.is_configured():
            return chosen
        for name in ("qwen", "glm"):
            client = ordered[name]
            if client.is_configured():
                return client
        return chosen or self.qwen_text_client

    def _image_writer_system_prompt(self) -> str:
        return (
            "你是资深中文小红书内容操盘手和代写作者。"
            "你的任务是直接写成可以发布的小红书标题和正文，而不是解释思路。"
            "严禁写任何类似“我会把它写成”“如果我要写”“这张图适合写成”之类的元叙述。"
            "正文必须像真人发笔记，口语化、有体感、有场景，不要写成分析报告。"
            "请严格只返回 JSON，不要返回 markdown。"
            'JSON 结构必须是 {"title":"","body":"","tags":[""],"persona":{"identity":"","scene":"","emotion":""}}。'
            "标题控制在 20 字内。"
            "正文要求："
            "1. 直接进入体验和结论，不要讲写作过程。"
            "2. 至少包含 2 个具体细节或数字。"
            "3. 至少包含 1 个“我当时以为…结果…”的转折。"
            "4. 必须有 1 句“不一定适合所有人”或同义边界提醒。"
            "5. 允许少量 emoji，但不要堆砌。"
            "6. 分段自然，不要每段长度过于整齐。"
            "7. tags 输出 3 到 5 个。"
        )

    def _image_writer_user_prompt(
        self,
        topic: str,
        content_mode: str,
        summary: str,
        keywords: list[str],
        visible_text: list[str],
        facts: dict[str, Any],
        angle: str | None,
        style_strength: str | None,
        revision_focus: list[str] | None,
    ) -> str:
        revision_text = "；".join(revision_focus or [])
        mode_hint = {
            "product_review": "真人测评",
            "travel_guide": "旅行攻略",
            "lifestyle_note": "生活方式笔记",
        }.get(content_mode, "真实体验笔记")
        strength = self._normalize_style_strength(style_strength)
        return (
            f"请基于下面的图片分析结果，写一篇真正可发的小红书 {mode_hint}。\n"
            f"主题: {topic}\n"
            f"写作角度: {angle or '自动判断最合适角度'}\n"
            f"爆款风格强度: {strength}\n"
            f"摘要: {summary}\n"
            f"关键词: {json.dumps(keywords, ensure_ascii=False)}\n"
            f"图片可见文字: {json.dumps(visible_text[:12], ensure_ascii=False)}\n"
            f"结构化事实: {json.dumps(facts, ensure_ascii=False)}\n"
            f"如果这是重写任务，需要优先修复的问题: {revision_text or '无，直接写最优版本'}\n"
            "写作要求补充：\n"
            "1. 如果是商品/服务测评，要重点写真实感受、适合谁、踩坑点和值不值得。\n"
            "2. 如果是旅行攻略，要重点写路线、体感、时间安排和避坑提醒。\n"
            "3. 绝对不要把分析过程、推理过程、模板思路写进正文。\n"
            "4. 正文要像真人直接发出来的内容。\n"
            f"5. 风格强度要求: {self._style_strength_instruction(strength)}\n"
            "6. title/body/tags/persona 都要写完整。"
        )

    def _normalize_style_strength(self, style_strength: str | None) -> str:
        value = str(style_strength or "").strip()
        if value in {"克制", "平衡", "强吸引"}:
            return value
        return "平衡"

    def _style_strength_instruction(self, strength: str) -> str:
        if strength == "克制":
            return "标题和开头要克制真实，少用夸张词，优先写细节、体验和判断，不要故意煽动情绪。"
        if strength == "强吸引":
            return "标题和开头要更抓人，允许更强的反差、冲突和情绪张力，但依然要像真人表达，不能标题党。"
        return "保持抓人但不过火，开头要有吸引力，正文要兼顾信息量、真实感和可读性。"

    def _generated_content_from_llm_payload(
        self,
        payload: dict[str, Any],
        topic: str,
        facts: dict[str, Any],
    ) -> GeneratedContent:
        title = clean_text(str(payload.get("title") or ""))[:20]
        body = clean_text(str(payload.get("body") or ""))
        tags_raw = payload.get("tags")
        tags = [str(item).strip() for item in tags_raw if str(item).strip()] if isinstance(tags_raw, list) else []
        persona = payload.get("persona") if isinstance(payload.get("persona"), dict) else {}

        if not title or len(title) < 6:
            raise ValueError("Writer title is empty or too short.")
        if not body or len(body) < 80:
            raise ValueError("Writer body is empty or too short.")
        forbidden_markers = (
            "我会把它写成",
            "如果我要写",
            "适合写成",
            "我会建议重点写",
            "这张图更适合写成",
        )
        if any(marker in body for marker in forbidden_markers):
            raise ValueError("Writer body still contains meta writing logic.")

        if not tags:
            tags = [topic, "真实体验", "经验分享"]

        return GeneratedContent(
            title=title,
            body=body[: int(self.settings.get("content", "max_body_length", 1000))],
            tags=tags[:5],
            referenced_sources=[str(item) for item in facts.get("images", []) if str(item).strip()],
            persona={
                "identity": str(persona.get("identity") or ""),
                "scene": str(persona.get("scene") or ""),
                "emotion": str(persona.get("emotion") or ""),
            },
        )
