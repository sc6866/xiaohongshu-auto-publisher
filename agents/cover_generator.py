from __future__ import annotations

import io
import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

from agents.base import BaseAgent
from common.models import CoverAsset, GeneratedContent
from common.qwen_image_client import QwenImageClient
from common.qwen_text_client import QwenTextClient
from common.utils import ensure_parent, slugify


@dataclass(slots=True)
class CoverStrategy:
    name: str
    badge: str
    subtitle: str
    reason: str
    bg_top: str
    bg_bottom: str
    ink: str
    card: str
    accent: str
    accent_soft: str
    stickers: list[str]


class CoverGenerator(BaseAgent):
    def __init__(self, settings, db, vector_store):
        super().__init__(settings, db, vector_store)
        self.qwen_text_client = QwenTextClient(settings)
        self.qwen_image_client = QwenImageClient(settings)

    def generate(self, content: GeneratedContent) -> CoverAsset:
        cover_id = slugify(content.title)[:48] or "cover"
        html_path = self.settings.generated_cover_dir / f"{cover_id}.html"
        png_path = self.settings.generated_cover_dir / f"{cover_id}.png"
        ensure_parent(html_path)

        asset = self._generate_ai_cover(content=content, png_path=png_path, html_path=html_path)
        if asset is not None:
            self.logger.info("Generated AI cover asset %s", png_path)
            return asset

        strategy = self._resolve_strategy(content)
        headline = self._extract_headline(content)
        stickers = self._build_stickers(content, strategy)
        html_path.write_text(self._build_html(headline, strategy, stickers), encoding="utf-8")
        self._build_template_png(headline, strategy, stickers, png_path)

        asset = CoverAsset(
            image_path=str(png_path),
            html_path=str(html_path),
            template_name=strategy.name,
            palette=strategy.name,
        )
        self.logger.info("Generated template cover asset %s", png_path)
        return asset

    def _generate_ai_cover(
        self,
        *,
        content: GeneratedContent,
        png_path: Path,
        html_path: Path,
    ) -> CoverAsset | None:
        if not self.qwen_text_client.is_configured() or not self.qwen_image_client.is_configured():
            return None

        try:
            prompt_payload = self.qwen_text_client.generate_json(
                prompt=self._cover_prompt_user_prompt(content),
                system_prompt=self._cover_prompt_system_prompt(),
            )
            prompt = str(prompt_payload.get("prompt") or "").strip()
            negative_prompt = str(prompt_payload.get("negative_prompt") or "").strip()
            style_name = str(prompt_payload.get("style_name") or "ai_cover").strip() or "ai_cover"
            if not prompt:
                return None

            image_bytes = self.qwen_image_client.generate_image(prompt, negative_prompt=negative_prompt)
            base = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            composed = self._compose_ai_cover(base=base, title=content.title, tags=content.tags)
            png_path.parent.mkdir(parents=True, exist_ok=True)
            composed.save(png_path, format="PNG")
            html_path.write_text(
                self._build_ai_preview_html(
                    title=content.title,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    image_name=png_path.name,
                ),
                encoding="utf-8",
            )
            return CoverAsset(
                image_path=str(png_path),
                html_path=str(html_path),
                template_name=f"ai:{style_name}",
                palette="ai_generated",
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("AI cover generation failed, fallback to template: %s", exc)
            return None

    def _cover_prompt_system_prompt(self) -> str:
        return (
            "你是小红书封面视觉导演。"
            "你的任务是根据文章内容，生成一个适合竖版 3:4 小红书封面的中文文生图提示词。"
            "只返回 JSON，不要解释。"
            'JSON 结构必须是 {"style_name":"","prompt":"","negative_prompt":""}。'
            "提示词要强调真实感、生活感、可商用审美，不要出现低质海报感。"
            "画面里不要生成大段可读文字，因为标题会在后期叠加。"
        )

    def _cover_prompt_user_prompt(self, content: GeneratedContent) -> str:
        body_preview = content.body[:260]
        return (
            f"标题：{content.title}\n"
            f"标签：{json.dumps(content.tags[:5], ensure_ascii=False)}\n"
            f"正文摘要：{body_preview}\n"
            "要求：\n"
            "1. 画面要适合小红书爆款封面，主体明确，构图简洁，留出左上和下半部文字区。\n"
            "2. 风格优先真实生活摄影感，其次再强调氛围和质感。\n"
            "3. 商品测评优先近景实拍质感；旅行攻略优先真实场景感；经验分享优先人物或生活场景。\n"
            "4. 不要生成复杂排版文字，不要水印，不要 logo，不要二维码。\n"
            "5. 给一个负面提示词，避免廉价感、畸形手、低清晰度、过度饱和。"
        )

    def _compose_ai_cover(self, base: Image.Image, title: str, tags: list[str]) -> Image.Image:
        canvas = self._fit_cover_base(base)
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        width, height = canvas.size
        draw.rounded_rectangle(
            (32, 38, width - 32, height - 38),
            radius=36,
            outline=(255, 255, 255, 120),
            width=2,
        )
        draw.rounded_rectangle(
            (36, height - 318, width - 36, height - 36),
            radius=34,
            fill=(255, 249, 241, 220),
        )
        draw.rounded_rectangle((42, 42, 180, 98), radius=24, fill=(217, 79, 4, 225))

        badge_font = self._load_font(28, bold=True)
        title_font = self._load_font(60, bold=True)
        tag_font = self._load_font(24, bold=True)
        sub_font = self._load_font(20)

        draw.text((66, 56), "真人实测", font=badge_font, fill="#FFF9F1")

        title_lines = self._wrap_title(title, max_chars=9, max_lines=2)
        title_y = height - 286
        for line in title_lines:
            draw.text((66, title_y), line, font=title_font, fill="#1F1A16")
            title_y += 74

        draw.text((68, height - 112), "自动生成封面", font=sub_font, fill="#6B5B4D")

        tag_x = 500
        tag_y = 108
        for tag in tags[:3]:
            text = tag.strip().lstrip("#")
            if not text:
                continue
            text = text[:8]
            box_width = max(102, len(text) * 26 + 30)
            draw.rounded_rectangle(
                (tag_x, tag_y, min(width - 38, tag_x + box_width), tag_y + 52),
                radius=18,
                fill=(255, 255, 255, 215),
            )
            draw.text((tag_x + 16, tag_y + 12), text, font=tag_font, fill="#1F1A16")
            tag_y += 68

        result = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        return result

    def _fit_cover_base(self, image: Image.Image) -> Image.Image:
        target_size = (768, 1024)
        width, height = image.size
        target_w, target_h = target_size
        scale = max(target_w / max(width, 1), target_h / max(height, 1))
        resized = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
        left = max((resized.width - target_w) // 2, 0)
        top = max((resized.height - target_h) // 2, 0)
        return resized.crop((left, top, left + target_w, top + target_h))

    def _build_ai_preview_html(self, *, title: str, prompt: str, negative_prompt: str, image_name: str) -> str:
        return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)}</title>
    <style>
      body {{
        margin: 0;
        font-family: "Microsoft YaHei", sans-serif;
        background: #f6f1e8;
        color: #1f1a16;
        padding: 24px;
      }}
      .shell {{
        max-width: 920px;
        margin: 0 auto;
        display: grid;
        gap: 18px;
      }}
      img {{
        width: min(360px, 100%);
        border-radius: 24px;
        box-shadow: 0 20px 40px rgba(0,0,0,0.12);
      }}
      pre {{
        white-space: pre-wrap;
        background: #fff8ef;
        padding: 16px;
        border-radius: 18px;
        line-height: 1.6;
      }}
    </style>
  </head>
  <body>
    <div class="shell">
      <img src="{escape(image_name)}" alt="{escape(title)}" />
      <pre>prompt:
{escape(prompt)}

negative_prompt:
{escape(negative_prompt)}</pre>
    </div>
  </body>
</html>
"""

    def _resolve_strategy(self, content: GeneratedContent) -> CoverStrategy:
        joined = " ".join([content.title, content.body, *content.tags]).lower()
        if any(token in joined for token in ("旅行", "攻略", "开封", "景点", "夜市", "出行")):
            return CoverStrategy(
                name="travel_hot",
                badge="路线实测",
                subtitle="省腿省时 少踩坑",
                reason="实拍感场景 + 高对比暖色",
                bg_top="#FFD867",
                bg_bottom="#FFF5D8",
                ink="#402312",
                card="#FFF9F1",
                accent="#FF5A2A",
                accent_soft="#FFE0D6",
                stickers=["本地实测", "避坑", "值不值"],
            )
        if any(token in joined for token in ("测评", "值不值", "开箱", "好不好用", "产品")):
            return CoverStrategy(
                name="review_clean",
                badge="真人测评",
                subtitle="先说结论 再讲细节",
                reason="近景主体 + 清晰文字区",
                bg_top="#FFE5CF",
                bg_bottom="#FFF8F2",
                ink="#332217",
                card="#FFFCF8",
                accent="#E77734",
                accent_soft="#FCE5D8",
                stickers=["优缺点", "省钱", "实话"],
            )
        return CoverStrategy(
            name="default",
            badge="经验分享",
            subtitle="真实体感 更像真人发帖",
            reason="生活场景 + 柔和留白",
            bg_top="#E6EED9",
            bg_bottom="#FFFDF7",
            ink="#253126",
            card="#FFFEFB",
            accent="#4E8A5B",
            accent_soft="#E3F0E5",
            stickers=["干货", "建议", "收藏"],
        )

    def _extract_headline(self, content: GeneratedContent) -> str:
        cleaned = content.title.replace("：", " ").replace(":", " ").strip()
        if len(cleaned) <= 10:
            return cleaned
        midpoint = min(max(len(cleaned) // 2, 4), 9)
        return f"{cleaned[:midpoint]}\n{cleaned[midpoint:midpoint + 10]}"

    def _build_stickers(self, content: GeneratedContent, strategy: CoverStrategy) -> list[str]:
        stickers = list(strategy.stickers)
        for tag in content.tags:
            normalized = tag.strip("# ").strip()
            if 1 < len(normalized) <= 8 and normalized not in stickers:
                stickers.append(normalized)
            if len(stickers) >= 3:
                break
        return stickers[:3]

    def _build_html(self, headline: str, strategy: CoverStrategy, stickers: list[str]) -> str:
        headline_html = "<br>".join(escape(part) for part in headline.split("\n"))
        stickers_html = "".join(f"<span>{escape(sticker)}</span>" for sticker in stickers)
        return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(headline.replace(chr(10), " "))}</title>
    <style>
      :root {{
        --bg-top: {strategy.bg_top};
        --bg-bottom: {strategy.bg_bottom};
        --card: {strategy.card};
        --ink: {strategy.ink};
        --accent: {strategy.accent};
        --accent-soft: {strategy.accent_soft};
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        width: 768px;
        height: 1024px;
        font-family: "Microsoft YaHei", sans-serif;
        background: linear-gradient(180deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
        color: var(--ink);
      }}
      .canvas {{
        position: relative;
        width: 100%;
        height: 100%;
        overflow: hidden;
      }}
      .blob {{
        position: absolute;
        border-radius: 999px;
        filter: blur(6px);
        opacity: 0.8;
      }}
      .blob.a {{
        width: 260px;
        height: 260px;
        right: -40px;
        top: -20px;
        background: rgba(255,255,255,0.45);
      }}
      .blob.b {{
        width: 220px;
        height: 220px;
        left: -30px;
        bottom: 110px;
        background: rgba(255,255,255,0.28);
      }}
      .badge {{
        position: absolute;
        left: 54px;
        top: 56px;
        padding: 14px 22px;
        border-radius: 999px;
        font-size: 28px;
        font-weight: 700;
        color: #fff;
        background: var(--accent);
      }}
      .card {{
        position: absolute;
        left: 54px;
        top: 150px;
        width: 520px;
        min-height: 620px;
        padding: 46px 40px 40px;
        border-radius: 42px;
        background: var(--card);
        box-shadow: 0 22px 54px rgba(0,0,0,0.10);
      }}
      h1 {{
        margin: 0;
        font-size: 78px;
        line-height: 1.02;
      }}
      .sub {{
        margin-top: 30px;
        font-size: 28px;
        font-weight: 700;
        color: var(--accent);
      }}
      .reason {{
        margin-top: 28px;
        display: inline-block;
        padding: 14px 20px;
        border-radius: 18px;
        font-size: 24px;
        background: var(--accent-soft);
      }}
      .stickers {{
        position: absolute;
        right: 46px;
        top: 236px;
        width: 164px;
        display: grid;
        gap: 18px;
      }}
      .stickers span {{
        display: block;
        padding: 14px 18px;
        border-radius: 20px;
        background: rgba(255,255,255,0.78);
        font-size: 24px;
        font-weight: 700;
        text-align: center;
      }}
    </style>
  </head>
  <body>
    <div class="canvas">
      <div class="blob a"></div>
      <div class="blob b"></div>
      <div class="badge">{escape(strategy.badge)}</div>
      <div class="card">
        <h1>{headline_html}</h1>
        <div class="sub">{escape(strategy.subtitle)}</div>
        <div class="reason">{escape(strategy.reason)}</div>
      </div>
      <div class="stickers">{stickers_html}</div>
    </div>
  </body>
</html>
"""

    def _build_template_png(self, headline: str, strategy: CoverStrategy, stickers: list[str], output_path: Path) -> None:
        width, height = 768, 1024
        image = Image.new("RGB", (width, height), "#FFFFFF")
        self._draw_gradient(image, strategy.bg_top, strategy.bg_bottom)
        draw = ImageDraw.Draw(image)

        draw.ellipse((520, -20, 860, 320), fill="#FFF4D8")
        draw.ellipse((-60, 760, 220, 1000), fill="#FFF8EA")
        draw.rounded_rectangle((54, 150, 574, 794), radius=42, fill=strategy.card)

        badge_font = self._load_font(28, bold=True)
        title_font = self._load_font(78, bold=True)
        sub_font = self._load_font(28, bold=True)
        reason_font = self._load_font(24)
        sticker_font = self._load_font(24, bold=True)

        draw.rounded_rectangle((54, 56, 240, 110), radius=28, fill=strategy.accent)
        draw.text((76, 69), strategy.badge, font=badge_font, fill="#FFFFFF")

        title_y = 214
        for line in headline.split("\n")[:2]:
            draw.text((92, title_y), line, font=title_font, fill=strategy.ink)
            title_y += 92

        draw.text((92, title_y + 18), strategy.subtitle, font=sub_font, fill=strategy.accent)

        reason_box_top = title_y + 76
        draw.rounded_rectangle((92, reason_box_top, 470, reason_box_top + 58), radius=18, fill=strategy.accent_soft)
        draw.text((112, reason_box_top + 14), strategy.reason, font=reason_font, fill=strategy.ink)

        sticker_y = 236
        for sticker in stickers:
            draw.rounded_rectangle((596, sticker_y, 726, sticker_y + 62), radius=20, fill="#FFFFFF")
            text_width = self._text_width(draw, sticker, sticker_font)
            draw.text((661 - text_width / 2, sticker_y + 16), sticker, font=sticker_font, fill=strategy.ink)
            sticker_y += 86

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="PNG")

    def _draw_gradient(self, image: Image.Image, start_hex: str, end_hex: str) -> None:
        start = ImageColor.getrgb(start_hex)
        end = ImageColor.getrgb(end_hex)
        width, height = image.size
        draw = ImageDraw.Draw(image)
        for y in range(height):
            ratio = y / max(height - 1, 1)
            color = tuple(int(start[index] + (end[index] - start[index]) * ratio) for index in range(3))
            draw.line((0, y, width, y), fill=color)

    def _wrap_title(self, title: str, *, max_chars: int, max_lines: int) -> list[str]:
        cleaned = title.replace("\n", " ").strip()
        if not cleaned:
            return ["封面标题"]
        lines = [cleaned[i : i + max_chars] for i in range(0, len(cleaned), max_chars)]
        return lines[:max_lines]

    def _load_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = [
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf" if bold else "C:/Windows/Fonts/simsun.ttc",
            "arial.ttf",
        ]
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _text_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
        bbox = draw.textbbox((0, 0), text, font=font)
        return float(bbox[2] - bbox[0])
