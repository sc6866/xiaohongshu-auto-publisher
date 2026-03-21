const state = {
  generatedContents: [],
  generatedDetails: {},
  loadingDetails: new Set(),
  publishRecords: [],
  knowledgeSources: [],
};

const SUPPORTED_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif"];
const SUPPORTED_IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"];

async function requestJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    throw new Error(`请求发送失败：${error?.message || String(error)}`);
  }

  const rawText = await response.text();
  let payload = {};
  if (rawText.trim()) {
    try {
      payload = JSON.parse(rawText);
    } catch (error) {
      throw new Error(rawText.slice(0, 200) || `服务器返回了无法解析的数据 (${response.status})`);
    }
  }
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function setResult(payload) {
  document.getElementById("resultBox").textContent = JSON.stringify(payload, null, 2);
}

function showLoading(title, text) {
  document.getElementById("loadingTitle").textContent = title || "正在处理中";
  document.getElementById("loadingText").textContent = text || "请稍等，这一步通常需要几秒到几十秒。";
  document.getElementById("loadingMask").classList.remove("hidden");
}

function hideLoading() {
  document.getElementById("loadingMask").classList.add("hidden");
}

async function withLoading(title, text, task) {
  showLoading(title, text);
  try {
    return await task();
  } finally {
    hideLoading();
  }
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function parseTagsInput(raw) {
  return String(raw || "")
    .split(/[\n,，#\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item, index, arr) => arr.indexOf(item) === index)
    .slice(0, 5);
}

function selectorEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function isSupportedImageFile(file) {
  const name = String(file?.name || "").toLowerCase();
  const type = String(file?.type || "").toLowerCase();
  return (
    SUPPORTED_IMAGE_EXTENSIONS.some((suffix) => name.endsWith(suffix)) ||
    SUPPORTED_IMAGE_MIME_TYPES.includes(type)
  );
}

function collectUnsupportedImageNames(files) {
  return Array.from(files || [])
    .filter((file) => !isSupportedImageFile(file))
    .map((file) => String(file?.name || "unknown"));
}

function describeFiles(files) {
  return Array.from(files || []).map((file) => ({
    name: String(file?.name || ""),
    type: String(file?.type || ""),
    size: Number(file?.size || 0),
  }));
}

function modeLabel(mode) {
  const mapping = {
    product_review: "真人测评",
    travel_guide: "旅行攻略",
    lifestyle_note: "生活方式",
  };
  return mapping[mode] || mode || "未标注";
}

function setMcpStatus(mcp) {
  const badge = document.getElementById("mcpBadge");
  if (mcp && mcp.ready && mcp.login_ok) {
    badge.textContent = "发布链路已就绪";
    badge.className = "status-pill is-ok";
    return;
  }
  if (mcp && mcp.configured) {
    badge.textContent = mcp.error || mcp.message || "已配置但未就绪";
    badge.className = "status-pill is-warn";
    return;
  }
  badge.textContent = "尚未配置 MCP";
  badge.className = "status-pill is-error";
}

function renderKnowledgeSources(items) {
  state.knowledgeSources = items || [];
  const container = document.getElementById("sourceList");
  if (!items || items.length === 0) {
    container.className = "source-list empty";
    container.innerHTML = "暂无热点素材";
    return;
  }
  container.className = "source-list";
  container.innerHTML = items
    .map((item) => {
      const tags = (item.tags || []).map((tag) => `<span class="chip chip-soft">${escapeHtml(tag)}</span>`).join("");
      const link = item.source_url
        ? `<a class="external-link" href="${item.source_url}" target="_blank" rel="noreferrer">打开原文</a>`
        : "";
      return `
        <article class="source-card">
          <div class="meta-top">
            <strong>${escapeHtml(item.title)}</strong>
            <span class="mini-status">热度 ${Math.round(Number(item.heat_score || 0))}</span>
          </div>
          <p class="source-topic">${escapeHtml(item.topic)}</p>
          <div class="chip-row">${tags}</div>
          <div class="meta-grid">
            <span>质量 ${Math.round(Number(item.quality_score || 0))}</span>
            <span>${escapeHtml(item.updated_at)}</span>
          </div>
          <div class="action-row">
            <button class="btn btn-small btn-primary" data-source-topic="${escapeHtml(item.topic || item.title)}">用这个选题生成</button>
            ${link}
          </div>
        </article>
      `;
    })
    .join("");

  container.querySelectorAll("[data-source-topic]").forEach((button) => {
    button.addEventListener("click", async () => {
      const topic = button.dataset.sourceTopic || "";
      await produceFromTopic(topic);
    });
  });
}

function renderDetailSkeleton() {
  return `
    <div class="detail-shell detail-shell-loading">
      <div class="skeleton skeleton-line skeleton-line-wide"></div>
      <div class="content-detail-grid">
        <section class="editor-panel">
          <div class="section-head">
            <div class="skeleton skeleton-pill"></div>
            <div class="skeleton skeleton-pill skeleton-pill-short"></div>
          </div>
          <div class="skeleton skeleton-input"></div>
          <div class="skeleton skeleton-textarea"></div>
          <div class="skeleton skeleton-input"></div>
          <div class="thumb-row">
            <div class="skeleton skeleton-thumb"></div>
            <div class="skeleton skeleton-thumb"></div>
            <div class="skeleton skeleton-thumb"></div>
          </div>
        </section>
        <section class="analysis-panel">
          <div class="section-head">
            <div class="skeleton skeleton-pill"></div>
            <div class="skeleton skeleton-pill skeleton-pill-short"></div>
          </div>
          <div class="skeleton skeleton-line skeleton-line-wide"></div>
          <div class="skeleton skeleton-line"></div>
          <div class="skeleton skeleton-line skeleton-line-mid"></div>
          <div class="skeleton skeleton-fact"></div>
          <div class="skeleton skeleton-fact"></div>
        </section>
      </div>
    </div>
  `;
}

function renderAnalysis(detail) {
  const analysis = detail.image_analysis || {};
  const meta = detail.generation_meta || {};
  const summary = String(analysis.summary || "").trim();
  const keywords = Array.isArray(analysis.keywords) ? analysis.keywords : [];
  const visibleText = Array.isArray(analysis.visible_text) ? analysis.visible_text : [];
  const facts = analysis.facts && typeof analysis.facts === "object" ? analysis.facts : {};
  const factEntries = Object.entries(facts).filter(([, value]) => {
    if (Array.isArray(value)) {
      return value.length > 0;
    }
    return value !== null && value !== undefined && String(value).trim() !== "";
  });

  if (!summary && !keywords.length && !visibleText.length && !factEntries.length && !Object.keys(meta).length) {
    return `<div class="analysis-empty">这条内容目前没有图片分析结果。</div>`;
  }

  const metaChips = [
    meta.source ? `<span class="chip chip-meta">来源 ${escapeHtml(meta.source)}</span>` : "",
    meta.mode ? `<span class="chip chip-meta">模式 ${escapeHtml(modeLabel(meta.mode))}</span>` : "",
    meta.style_strength ? `<span class="chip chip-meta">强度 ${escapeHtml(meta.style_strength)}</span>` : "",
    meta.angle ? `<span class="chip chip-meta">角度 ${escapeHtml(meta.angle)}</span>` : "",
  ]
    .filter(Boolean)
    .join("");
  const keywordsHtml = keywords
    .slice(0, 8)
    .map((item) => `<span class="chip chip-soft">${escapeHtml(item)}</span>`)
    .join("");
  const visibleTextHtml = visibleText
    .slice(0, 8)
    .map((item) => `<span class="chip chip-soft">${escapeHtml(item)}</span>`)
    .join("");
  const factsHtml = factEntries
    .slice(0, 8)
    .map(([key, value]) => {
      const display = Array.isArray(value) ? value.join(" / ") : String(value);
      return `
        <div class="analysis-fact">
          <span class="fact-key">${escapeHtml(key)}</span>
          <span class="fact-value">${escapeHtml(display)}</span>
        </div>
      `;
    })
    .join("");

  return `
    <div class="analysis-stack">
      ${metaChips ? `<div class="chip-row">${metaChips}</div>` : ""}
      ${summary ? `<div class="analysis-block"><span class="analysis-label">分析摘要</span><p>${escapeHtml(summary)}</p></div>` : ""}
      ${keywordsHtml ? `<div class="analysis-block"><span class="analysis-label">关键词</span><div class="chip-row">${keywordsHtml}</div></div>` : ""}
      ${visibleTextHtml ? `<div class="analysis-block"><span class="analysis-label">图中文字</span><div class="chip-row">${visibleTextHtml}</div></div>` : ""}
      ${factsHtml ? `<div class="analysis-block"><span class="analysis-label">结构化信息</span><div class="analysis-facts">${factsHtml}</div></div>` : ""}
    </div>
  `;
}

function renderGenerated(items) {
  state.generatedContents = items || [];
  const container = document.getElementById("generatedList");
  if (!items || items.length === 0) {
    container.className = "card-list empty";
    container.innerHTML = "暂无内容";
    return;
  }

  container.className = "card-list";
  container.innerHTML = items
    .map((item) => {
      const tags = (item.tags || []).map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join("");
      const cover = item.display_cover_url
        ? `<img class="cover-thumb" src="${item.display_cover_url}" alt="${escapeHtml(item.title)}" />`
        : `<div class="cover-thumb cover-placeholder">暂无封面</div>`;
      const publishPreview = (item.publish_image_urls || [])
        .map((url) => `<img class="mini-thumb" src="${url}" alt="publish image" />`)
        .join("");
      const meta = item.generation_meta || {};
      const metaChips = [
        meta.mode ? `<span class="chip chip-meta">${escapeHtml(modeLabel(meta.mode))}</span>` : "",
        meta.style_strength ? `<span class="chip chip-meta">${escapeHtml(meta.style_strength)}</span>` : "",
        item.has_analysis ? `<span class="chip chip-meta">有图片分析</span>` : "",
      ]
        .filter(Boolean)
        .join("");
      const detail = state.generatedDetails[item.id];
      const isLoading = state.loadingDetails.has(item.id);

      return `
        <article class="content-card content-card-summary">
          <div class="cover-panel">
            ${cover}
            <div class="cover-meta">
              <span class="mini-status">${escapeHtml(item.status)}</span>
              <span class="meta-inline">评分 ${Number(item.review_score || 0)}</span>
            </div>
          </div>
          <div class="content-meta">
            <div class="meta-top">
              <strong>${escapeHtml(item.title)}</strong>
              <span class="meta-inline id-text">${escapeHtml(item.id)}</span>
            </div>
            <p class="body-preview">${escapeHtml(item.body_preview || "")}${(item.body_preview || "").length >= 120 ? "…" : ""}</p>
            <div class="chip-row">${tags}</div>
            ${metaChips ? `<div class="chip-row">${metaChips}</div>` : ""}
            <div class="publish-assets">
              <span class="meta-inline">待发布图片 ${Number(item.publish_image_count || 0)} 张</span>
              <div class="thumb-row">${publishPreview || '<span class="meta-inline">目前仅封面，尚未补图</span>'}</div>
            </div>
            <div class="action-row">
              <button class="btn btn-small btn-primary" data-toggle-id="${escapeHtml(item.id)}">${detail ? "收起编辑" : "展开编辑"}</button>
              <button class="btn btn-small btn-secondary" data-content-id="${escapeHtml(item.id)}">带入发布</button>
              <button class="btn btn-small btn-secondary" data-delete-id="${escapeHtml(item.id)}">删除</button>
            </div>
            ${isLoading ? renderDetailSkeleton() : ""}
            ${detail ? renderGeneratedDetail(detail) : ""}
          </div>
        </article>
      `;
    })
    .join("");

  bindGeneratedActions(container);
}

function renderGeneratedDetail(detail) {
  const tagsValue = (detail.tags || []).join(", ");
  const publishPreview = (detail.publish_image_urls || [])
    .map((url) => `<img class="mini-thumb" src="${url}" alt="publish image" />`)
    .join("");
  const persona = detail.persona || {};
  const personaLine = [persona.identity, persona.scene, persona.emotion].filter(Boolean).join(" / ");
  return `
    <div class="detail-shell">
      ${personaLine ? `<div class="persona-line">${escapeHtml(personaLine)}</div>` : ""}
      <div class="content-detail-grid">
        <section class="editor-panel">
          <div class="section-head">
            <h3>成文结果</h3>
            <span class="meta-inline">可直接修改后保存</span>
          </div>
          <label class="field">
            <span>标题</span>
            <input
              type="text"
              class="editor-input"
              data-edit-title="${escapeHtml(detail.id)}"
              value="${escapeHtml(detail.title)}"
              maxlength="20"
            />
          </label>
          <label class="field">
            <span>正文</span>
            <textarea class="editor-textarea" data-edit-body="${escapeHtml(detail.id)}">${escapeHtml(detail.body)}</textarea>
          </label>
          <label class="field">
            <span>标签</span>
            <input
              type="text"
              class="editor-input"
              data-edit-tags="${escapeHtml(detail.id)}"
              value="${escapeHtml(tagsValue)}"
              placeholder="用逗号、空格或 # 分隔"
            />
          </label>
          <div class="publish-assets">
            <span class="meta-inline">待发布图片 ${(detail.publish_images || []).length} 张</span>
            <div class="thumb-row">${publishPreview || '<span class="meta-inline">目前仅封面，尚未补图</span>'}</div>
          </div>
          <div class="inline-upload">
            <input class="inline-file" type="file" data-upload-for="${escapeHtml(detail.id)}" accept="image/png,image/jpeg,image/webp,image/heic,image/heif,.png,.jpg,.jpeg,.webp,.heic,.heif" multiple />
            <button class="btn btn-small btn-secondary" data-attach-id="${escapeHtml(detail.id)}">补充发布图片</button>
          </div>
          <div class="action-row">
            <button class="btn btn-small btn-primary" data-save-id="${escapeHtml(detail.id)}">保存修改</button>
          </div>
        </section>
        <section class="analysis-panel">
          <div class="section-head">
            <h3>图片分析结果</h3>
            <span class="meta-inline">用于判断写作方向</span>
          </div>
          ${renderAnalysis(detail)}
        </section>
      </div>
    </div>
  `;
}

async function produceFromTopic(topic) {
  const cleaned = String(topic || "").trim();
  if (!cleaned) {
    return;
  }
  document.getElementById("topic").value = cleaned;
  const payload = await withLoading("正在生成主题文章", `正在根据“${cleaned}”检索素材、生成文案并产出封面。`, () =>
    requestJson("/api/produce", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic: cleaned }),
    }),
  );
  setResult(payload);
  if (payload.content_id) {
    document.getElementById("contentId").value = payload.content_id;
  }
  await refreshDashboard({ silent: true });
  if (payload.content_id) {
    await ensureGeneratedDetail(payload.content_id);
    window.scrollTo({ top: document.getElementById("generatedList").offsetTop - 24, behavior: "smooth" });
  }
}

function bindGeneratedActions(container) {
  container.querySelectorAll("[data-content-id]").forEach((button) => {
    button.addEventListener("click", () => {
      document.getElementById("contentId").value = button.dataset.contentId || "";
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });

  container.querySelectorAll("[data-toggle-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const contentId = button.dataset.toggleId || "";
      if (state.generatedDetails[contentId]) {
        delete state.generatedDetails[contentId];
        renderGenerated(state.generatedContents);
        return;
      }
      await ensureGeneratedDetail(contentId);
    });
  });

  container.querySelectorAll("[data-attach-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const contentId = button.dataset.attachId || "";
      const input = container.querySelector(`[data-upload-for="${selectorEscape(contentId)}"]`);
      const files = input?.files ? Array.from(input.files) : [];
      if (!files.length) {
        setResult({ error: "请先为这条内容选择要补充的图片。" });
        return;
      }
      const unsupported = collectUnsupportedImageNames(files);
      if (unsupported.length) {
        setResult({
          error: "当前支持 JPG / PNG / WEBP / HEIC / HEIF。若仍上传失败，请确认图片没有损坏后重试。",
          unsupported_files: unsupported,
        });
        return;
      }
      const formData = new FormData();
      formData.append("content_id", contentId);
      files.forEach((file) => formData.append("images", file));
      try {
      const payload = await withLoading("正在补充发布图片", "正在把你追加的图片保存到这条内容里。", () =>
        requestJson("/api/attach-publish-images", {
          method: "POST",
          body: formData,
        }),
      );
      setResult(payload);
      await ensureGeneratedDetail(contentId, true);
      await refreshDashboard({ silent: true });
      } catch (error) {
        setResult({
          error: error?.message || String(error),
          stage: "attach_publish_images",
          content_id: contentId,
          files: describeFiles(files),
        });
      }
    });
  });

  container.querySelectorAll("[data-save-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const contentId = button.dataset.saveId || "";
      const titleInput = container.querySelector(`[data-edit-title="${selectorEscape(contentId)}"]`);
      const bodyInput = container.querySelector(`[data-edit-body="${selectorEscape(contentId)}"]`);
      const tagsInput = container.querySelector(`[data-edit-tags="${selectorEscape(contentId)}"]`);
      const payload = await withLoading("正在保存修改", "正在把你改过的标题、正文和标签写回数据库。", () =>
        requestJson("/api/generated-update", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            content_id: contentId,
            title: titleInput?.value || "",
            body: bodyInput?.value || "",
            tags: parseTagsInput(tagsInput?.value || ""),
          }),
        }),
      );
      setResult(payload);
      await ensureGeneratedDetail(contentId, true);
      await refreshDashboard({ silent: true });
    });
  });

  container.querySelectorAll("[data-delete-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const contentId = button.dataset.deleteId || "";
      if (!window.confirm(`确认删除这条生成内容？\n${contentId}`)) {
        return;
      }
      const payload = await withLoading("正在删除内容", "会删除数据库记录，并尽量清理这条内容独占的封面和上传素材。", () =>
        requestJson("/api/generated-delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content_id: contentId }),
        }),
      );
      delete state.generatedDetails[contentId];
      setResult(payload);
      await refreshDashboard({ silent: true });
    });
  });
}

async function ensureGeneratedDetail(contentId, forceReload = false) {
  if (!contentId) {
    return;
  }
  if (!forceReload && state.generatedDetails[contentId]) {
    renderGenerated(state.generatedContents);
    return;
  }
  state.loadingDetails.add(contentId);
  renderGenerated(state.generatedContents);
  try {
    const payload = await requestJson(`/api/generated-detail?content_id=${encodeURIComponent(contentId)}`);
    state.generatedDetails[contentId] = payload.detail || null;
  } catch (error) {
    setResult({ error: error.message, content_id: contentId });
  } finally {
    state.loadingDetails.delete(contentId);
    renderGenerated(state.generatedContents);
  }
}

function renderPublish(items) {
  state.publishRecords = items || [];
  const container = document.getElementById("publishList");
  if (!items || items.length === 0) {
    container.className = "card-list empty";
    container.innerHTML = "暂无发布记录";
    return;
  }

  container.className = "card-list";
  container.innerHTML = items
    .map((item) => {
      const engagement = item.engagement_24h || {};
      const link = item.note_url
        ? `<a class="external-link" href="${item.note_url}" target="_blank" rel="noreferrer">打开作品</a>`
        : `<span class="meta-inline">暂未解析作品链接</span>`;
      return `
        <article class="publish-card">
          <div class="meta-top">
            <strong class="id-text">${escapeHtml(item.content_id)}</strong>
            <span class="mini-status">${escapeHtml(item.status)}</span>
          </div>
          <div class="meta-grid">
            <span>占位 note_id：${escapeHtml(item.note_id)}</span>
            <span>真实 note_id：${escapeHtml(item.real_note_id || "未同步")}</span>
            <span>匹配方式：${escapeHtml(item.matched_via || "未记录")}</span>
            <span>发布时间：${escapeHtml(item.publish_time)}</span>
          </div>
          <div class="meta-grid">
            <span>点赞：${Number(engagement.likes || 0)}</span>
            <span>收藏：${Number(engagement.collects || 0)}</span>
            <span>评论：${Number(engagement.comments || 0)}</span>
          </div>
          ${link}
        </article>
      `;
    })
    .join("");
}

async function refreshDashboard(options = {}) {
  const { silent = false } = options;
  const payload = await requestJson("/api/dashboard");
  setMcpStatus(payload.mcp);
  renderKnowledgeSources(payload.knowledge_sources || []);
  renderGenerated(payload.generated_contents || []);
  renderPublish(payload.publish_records || []);
  if (!silent) {
    setResult(payload);
  }
}

async function init() {
  document.getElementById("refreshDashboard").addEventListener("click", async () => {
    await withLoading("正在刷新看板", "正在同步最新的热点素材、生成内容和发布记录。", () => refreshDashboard());
  });

  document.getElementById("checkMcp").addEventListener("click", async () => {
    const payload = await withLoading("正在检查登录状态", "正在检查小红书 MCP 服务和登录状态。", () =>
      requestJson("/api/mcp-check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }),
    );
    setMcpStatus(payload);
    setResult(payload);
  });

  document.getElementById("scanNow").addEventListener("click", async () => {
    const payload = await withLoading("正在扫描热点", "正在扫描热词、抓取公开内容并更新素材池。", () =>
      requestJson("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }),
    );
    setResult(payload);
    await refreshDashboard({ silent: true });
  });

  document.getElementById("syncLatest").addEventListener("click", async () => {
    const limit = Number(document.getElementById("syncLimit").value || 5);
    const payload = await withLoading("正在同步最新作品", "正在从账号主页回流最近作品。", () =>
      requestJson("/api/sync-latest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit }),
      }),
    );
    setResult(payload);
    await refreshDashboard({ silent: true });
  });

  document.getElementById("runFeedback").addEventListener("click", async () => {
    const payload = await withLoading("正在回流 24h 数据", "正在刷新真实 note_id 和互动指标。", () =>
      requestJson("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }),
    );
    setResult(payload);
    await refreshDashboard({ silent: true });
  });

  document.getElementById("clearGenerated").addEventListener("click", async () => {
    if (!window.confirm("确认清空未发布内容吗？已发布内容会保留。")) {
      return;
    }
    const payload = await withLoading("正在清理未发布内容", "正在删除数据库记录并清理可安全删除的素材文件。", () =>
      requestJson("/api/generated-clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preserve_published: true }),
      }),
    );
    state.generatedDetails = {};
    setResult(payload);
    await refreshDashboard({ silent: true });
  });

  document.getElementById("topicForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await produceFromTopic(document.getElementById("topic").value.trim());
  });

  document.getElementById("imageForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const imageInput = document.getElementById("images");
    const files = Array.from(imageInput.files || []);
    if (!files.length) {
      setResult({ error: "请先选择至少一张图片。" });
      return;
    }
    const unsupported = collectUnsupportedImageNames(files);
    if (unsupported.length) {
      setResult({
        error: "当前支持 JPG / PNG / WEBP / HEIC / HEIF。若仍上传失败，请确认图片没有损坏后重试。",
        unsupported_files: unsupported,
      });
      return;
    }
    const formData = new FormData();
    files.forEach((file) => formData.append("images", file));
    formData.append("mode", document.getElementById("mode").value);
    formData.append("angle", document.getElementById("angle").value.trim());
    formData.append("style_strength", document.getElementById("styleStrength").value);
    try {
    const payload = await withLoading("正在分析图片并生成文章", "正在调用图片理解、OCR、文案生成和封面链路，请耐心等待。", () =>
      requestJson("/api/produce-images", {
        method: "POST",
        body: formData,
      }),
    );
    setResult(payload);
    if (payload.content_id) {
      document.getElementById("contentId").value = payload.content_id;
    }
    await refreshDashboard({ silent: true });
    if (payload.content_id) {
      await ensureGeneratedDetail(payload.content_id);
    }
    } catch (error) {
      setResult({
        error: error?.message || String(error),
        stage: "produce_images",
        endpoint: "/api/produce-images",
        files: describeFiles(files),
        userAgent: navigator.userAgent,
      });
    }
  });

  document.getElementById("publishForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await withLoading("正在发布内容", "正在把正文和图片推送到小红书，请不要关闭页面。", () =>
      requestJson("/api/publish-live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content_id: document.getElementById("contentId").value.trim(),
          visibility: document.getElementById("visibility").value,
        }),
      }),
    );
    setResult(payload);
    await refreshDashboard({ silent: true });
  });

  await refreshDashboard();
}

init().catch((error) => {
  setResult({ error: error.message });
});
