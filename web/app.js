const CALIBRATION = {
  confirming: "价讯共振",
  pricedIn: "或已计价",
  divergence: "价讯背离",
  insufficientData: "行情不足",
};

const DIRECTION = {
  up: "偏多",
  down: "偏空",
  mixed: "混杂",
  unclear: "不明",
};

const TREND = {
  expanding: "放量",
  contracting: "缩量",
  more_active: "更活跃",
  less_active: "回落",
  warming: "升温",
  cooling: "降温",
  stable: "平稳",
  unknown: "不明",
};

function $(id) {
  return document.getElementById(id);
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const num = Number(value);
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(2)}%`;
}

function chgClass(value) {
  if (value === null || value === undefined) return "chg";
  return Number(value) >= 0 ? "chg up" : "chg down";
}

function formatBeijing(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const fmt = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = Object.fromEntries(fmt.formatToParts(date).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} 北京时间`;
}

function formatPrice(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const num = Number(value);
  const digits = Math.abs(num) >= 100 ? 2 : Math.abs(num) >= 1 ? 2 : 3;
  return num.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function formatMove(price, changePct) {
  const pctText = pct(changePct);
  if (price === null || price === undefined || changePct === null || changePct === undefined) {
    return pctText;
  }
  const prev = Number(price) / (1 + Number(changePct) / 100);
  if (!Number.isFinite(prev) || prev === 0) return pctText;
  const delta = Number(price) - prev;
  const sign = delta > 0 ? "+" : "";
  const absDelta = Math.abs(delta);
  const digits = absDelta >= 1 ? 2 : 3;
  const deltaText = `${sign}${delta.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  return `${deltaText}（${pctText}）`;
}

function renderQuotes(title, rows) {
  if (!rows || !rows.length) return "";
  return `
    <h3>${title}</h3>
    <div class="quotes">
      ${rows
        .map(
          (row) => `
        <div class="quote">
          <div class="name">${row.name || row.symbol}</div>
          <div class="price">${formatPrice(row.price)}</div>
          <div class="${chgClass(row.changePct)}">${formatMove(row.price, row.changePct)}</div>
        </div>`
        )
        .join("")}
    </div>`;
}

function sparkline(values, color) {
  const nums = (values || []).map(Number).filter((n) => Number.isFinite(n));
  if (nums.length < 2) return "";
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const width = 220;
  const height = 44;
  const pad = 3;
  const points = nums
    .map((value, index) => {
      const x = pad + (index / (nums.length - 1)) * (width - pad * 2);
      const y = height - pad - ((value - min) / span) * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true"><polyline fill="none" stroke="${color}" stroke-width="2" points="${points}"/></svg>`;
}

function formatYi(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(1)} 亿`;
}

function formatHeat(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
  const num = Number(value);
  if (num >= 10000) return `${(num / 10000).toFixed(1)} 万`;
  return String(Math.round(num));
}

function matchLabel(value) {
  if (value === "finance") return "财经";
  if (value === "market") return "市场词";
  if (value === "focus") return "侧重点";
  return value || "";
}

function renderHotSearch(report) {
  const section = $("hotsearch");
  if (!section) return;
  const items = report.hotSearch || [];
  const weibo = Boolean(report.dataCoverage?.weibo);
  if (!weibo && !items.length) {
    section.hidden = true;
    section.innerHTML = "";
    return;
  }
  const fetched = items[0]?.fetchedAt || report.generatedAt;
  const rows = items.length
    ? `<ol class="hot-list">${items
        .map((item) => {
          const word = item.word || "";
          const link = item.url
            ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">${word}</a>`
            : word;
          const meta = [
            item.rank ? `#${item.rank}` : "",
            item.category || "",
            matchLabel(item.match),
            item.label || "",
            formatHeat(item.heat),
            item.onboardAt ? `上榜 ${formatBeijing(item.onboardAt)}` : "上榜时间未知",
          ]
            .filter(Boolean)
            .join(" · ");
          return `<li><div class="hot-word">${link}</div><div class="hint">${meta}</div></li>`;
        })
        .join("")}</ol>`
    : `<p class="hint">当前热搜无财经/市场相关条目。总榜已拉取，但不把娱乐话题灌进分析。</p>`;
  section.hidden = false;
  section.innerHTML = `
    <p class="section-kicker">微博财经热搜</p>
    <p class="hint">公开榜单快照，不是博主时间线。拉取时间 ${formatBeijing(fetched)}。只保留财经分类、市场关键词或与侧重点重合的条目；过旧上榜时间会被丢掉。</p>
    ${rows}
  `;
}

function pulseHasData(pulse) {
  if (!pulse) return false;
  return Boolean(
    pulse.volume?.series?.length || pulse.northbound?.series?.length || pulse.sentiment?.series?.length
  );
}

function renderPulseLane(title, trend, note, values, color, extra) {
  return `
    <article class="pulse-lane">
      <div class="pulse-lane-head">
        <h3>${title}</h3>
        <span class="tag">${TREND[trend] || trend || "—"}</span>
      </div>
      ${sparkline(values, color)}
      <p class="hint">${note || ""}</p>
      ${extra || ""}
    </article>`;
}

function renderPulse(pulse) {
  if (!pulseHasData(pulse)) {
    $("pulse").hidden = true;
    $("pulse").innerHTML = "";
    return;
  }
  const volume = pulse.volume || {};
  const north = pulse.northbound || {};
  const sentiment = pulse.sentiment || {};
  const volValues = (volume.series || []).map((row) => row.volume).filter((value) => value);
  const northValues = (north.series || []).map((row) => row.dealAmtYi).filter((value) => value || value === 0);
  const sentValues = (sentiment.series || []).map((row) => row.score);
  const mix = sentiment.sourceMix || {};
  const mixText = ["official", "major_media", "google_news", "blog", "other"]
    .filter((key) => mix[key])
    .map((key) => `${{ official: "官方", major_media: "主流媒体", google_news: "新闻聚合", blog: "博客/专栏", other: "其他" }[key]} ${mix[key]}`)
    .join(" · ");
  const lastNorth = (north.series || [])[(north.series || []).length - 1] || {};
  $("pulse").hidden = false;
  $("pulse").innerHTML = `
    <p class="section-kicker">情绪与资金时间线</p>
    <p class="hint">把成交量、北向成交额、新闻热度串成近两周的线，而不是只看最新一个点。北向净买入已不再实时披露时，这里用成交额衡量外资活跃度，不是净流入。</p>
    <div class="pulse-grid">
      ${renderPulseLane(
        volume.name ? `${volume.name} 成交量` : "成交量",
        volume.trend,
        volume.note,
        volValues,
        "#e2b657"
      )}
      ${renderPulseLane(
        "北向成交额",
        north.trend,
        north.note,
        northValues,
        "#9ec5ff",
        lastNorth.date
          ? `<p class="hint">最近：${lastNorth.date} ${formatYi(lastNorth.dealAmtYi)}${lastNorth.leadStock ? ` · 活跃股 ${lastNorth.leadStock}` : ""}</p>`
          : ""
      )}
      ${renderPulseLane("新闻情绪（加权）", sentiment.trend, sentiment.note, sentValues, "#3dd68c", mixText ? `<p class="hint">信源构成：${mixText}</p>` : "")}
    </div>`;
}

function renderEvidence(items) {
  if (!items || !items.length) return "<p class='hint'>无</p>";
  return `<ul class="evidence">${items
    .map((item) => {
      const label = item.sourceTitle || item.claim || "来源";
      const link = item.url
        ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">${label}</a>`
        : label;
      return `<li>${link}<div class="hint">${item.claim || ""} ${formatBeijing(item.publishedAt)}</div></li>`;
    })
    .join("")}</ul>`;
}

function aiLabel(report) {
  const synth = String(report.stats?.model || "heuristic");
  if (synth === "sample") return { text: "示例文案", heuristic: true };
  if (!synth || synth === "heuristic") return { text: "规则草稿", heuristic: true };
  return { text: `模型综合 · ${synth}`, heuristic: false };
}

function renderReport(report) {
  const windowFrom = formatBeijing(report.timeWindow?.from);
  const windowTo = formatBeijing(report.timeWindow?.to);
  $("status").hidden = false;
  $("status").innerHTML = `
    <div>侧重点：<strong>${report.focus || "泛市场"}</strong></div>
    <div>生成时间：${formatBeijing(report.generatedAt)}</div>
    <div>回看窗口：${windowFrom} — ${windowTo}</div>
    <div>覆盖：新闻 ${report.dataCoverage?.news ? "是" : "否"} · 行情 ${report.dataCoverage?.quotes ? "是" : "否"} · 北向时间线 ${report.dataCoverage?.northbound ? "是" : "否"} · 微博热搜 ${report.dataCoverage?.weibo ? "是" : "否"} · X ${report.dataCoverage?.x ? "是" : "否"}</div>
    <div>模型：${report.stats?.plannerModel || "—"} → ${report.stats?.model || "—"}</div>
  `;

  const snap = report.marketSnapshot || {};
  $("snapshot").hidden = false;
  $("snapshot").innerHTML =
    `<p class="section-kicker">行情快照</p>` +
    `<p class="hint">公开行情，不是 AI 生成。大盘基准固定；板块 ETF 与个股随本次侧重点变化。</p>` +
    `<p class="hint">行情时间：${formatBeijing(snap.asOf)}（可能延迟）</p>` +
    renderQuotes("基准（大盘）", snap.benchmarks) +
    renderQuotes("板块 / ETF（随侧重点）", snap.sectors) +
    renderQuotes("相关标的（随侧重点）", snap.tickers);

  renderPulse(report.marketPulse);
  renderHotSearch(report);

  const label = aiLabel(report);
  $("ai-summary").hidden = false;
  $("ai-badge").textContent = label.text;
  $("ai-badge").className = "ai-badge" + (label.heuristic ? " heuristic" : "");

  $("outlook").innerHTML = (report.sectorOutlook || [])
    .map(
      (sector) => `
      <article class="card">
        <h2>${sector.heat}. ${sector.sector}</h2>
        <div class="meta">
          <span class="tag ${sector.calibration}">${CALIBRATION[sector.calibration] || sector.calibration}</span>
          <span class="tag">${DIRECTION[sector.direction] || sector.direction}</span>
          <span class="tag">热度 ${sector.heatScore ?? "—"}</span>
          <span class="tag">置信 ${sector.confidence ?? "—"}</span>
        </div>
        <p>${sector.narrative || ""}</p>
        <h3>依据</h3>
        ${renderEvidence(sector.evidence)}
        <h3>反向证据</h3>
        ${renderEvidence(sector.counterEvidence)}
        <p class="hint">失效条件：${sector.invalidatedIf || "—"}</p>
      </article>`
    )
    .join("");

  $("notes").innerHTML = `
    ${report.trendNotes ? `<div class="trend-notes"><p class="section-kicker">时间线笔记</p><p>${report.trendNotes}</p></div>` : ""}
    <p>${report.crossSectorNotes || ""}</p>
    <p class="hint">${(report.limitations || []).join(" · ")}</p>
  `;

  if (report.errors && report.errors.length) {
    $("errors").hidden = false;
    $("errors").textContent = report.errors.join("\n");
  } else {
    $("errors").hidden = true;
  }
}

async function loadReport() {
  const { reportsUrl, sampleUrl } = window.APP_CONFIG;
  $("hint").textContent = "正在读取报告…";
  try {
    const response = await fetch(`${reportsUrl}?t=${Date.now()}`);
    if (response.ok) {
      renderReport(await response.json());
      $("hint").textContent = "正在显示 latest.json。若刚在 Actions 跑完，请稍候刷新。页面时间均为北京时间。";
      return;
    }
  } catch (error) {
    console.warn(error);
  }
  const sample = await fetch(`${sampleUrl}?t=${Date.now()}`);
  renderReport(await sample.json());
  $("hint").textContent = "尚未生成真实报告，先展示示例。在 GitHub Actions 运行分析后刷新。页面时间均为北京时间。";
}

$("start").addEventListener("click", () => {
  const repo = window.APP_CONFIG.repo;
  const workflow = window.APP_CONFIG.workflow;
  const focus = $("focus").value.trim();
  $("hint").textContent = focus
    ? `请在 Actions 页面点击 Run workflow，侧重点填：${focus}`
    : "请在 Actions 页面点击 Run workflow。可不填侧重点，将做泛市场扫描。";
  window.open(`https://github.com/${repo}/actions/workflows/${workflow}`, "_blank", "noopener");
});

$("reload").addEventListener("click", loadReport);
loadReport();
