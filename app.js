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
          <div class="${chgClass(row.changePct)}">${pct(row.changePct)}</div>
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
      return `<li>${link}<div class="hint">${item.claim || ""} ${item.publishedAt || ""}</div></li>`;
    })
    .join("")}</ul>`;
}

function renderReport(report) {
  $("status").hidden = false;
  $("status").innerHTML = `
    <div>侧重点：<strong>${report.focus || "泛市场"}</strong></div>
    <div>生成时间：${report.generatedAt || "—"}</div>
    <div>覆盖：新闻 ${report.dataCoverage?.news ? "是" : "否"} · 行情 ${report.dataCoverage?.quotes ? "是" : "否"} · 微博/X ${report.dataCoverage?.weibo || report.dataCoverage?.x ? "是" : "否"}</div>
    <div>模型：${report.stats?.model || "—"}</div>
  `;

  const snap = report.marketSnapshot || {};
  $("snapshot").hidden = false;
  $("snapshot").innerHTML =
    renderQuotes("基准", snap.benchmarks) +
    renderQuotes("板块 / ETF", snap.sectors) +
    renderQuotes("相关标的", snap.tickers);

  $("outlook").hidden = false;
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

  $("notes").hidden = false;
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
      $("hint").textContent = "正在显示 latest.json。若刚在 Actions 跑完，请稍候刷新。";
      return;
    }
  } catch (error) {
    console.warn(error);
  }
  const sample = await fetch(`${sampleUrl}?t=${Date.now()}`);
  renderReport(await sample.json());
  $("hint").textContent = "尚未生成真实报告，先展示示例。在 GitHub Actions 运行分析后刷新。";
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
