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
    <div>覆盖：新闻 ${report.dataCoverage?.news ? "是" : "否"} · 行情 ${report.dataCoverage?.quotes ? "是" : "否"} · 微博/X ${report.dataCoverage?.weibo || report.dataCoverage?.x ? "是" : "否"}</div>
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
