#!/usr/bin/env node
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import {
  escapeHtml,
  KORNVIA_REPORT_CSS,
  passportTitle,
  PRODUCT_CONFIG,
  REPORT_TEMPLATE_VERSION,
} from "./kornvia-brand.mjs";

const MAX_INPUT_BYTES = 12 * 1024 * 1024;
const MAX_PHOTO_BYTES = 16 * 1024 * 1024;
const CTA_URL = PRODUCT_CONFIG.form.requestUrl;
const FALLBACK_URL = PRODUCT_CONFIG.form.fallbackUrl;
const FORM_COPY = PRODUCT_CONFIG.form.copy;
const FREE_OFFER = PRODUCT_CONFIG.offers.free;
const MANUAL_OFFER = PRODUCT_CONFIG.offers.manualItineraryBeta;
const SENSITIVE_QUERY_KEYS = new Set([
  "xsec_token", "xsec_source", "access_token", "refresh_token", "auth_token",
  "authorization", "api_key", "apikey", "signature", "sig",
]);
const INTERNAL_PATTERN =
  /(?:\.workbuddy|\.claude|\/Users\/|\/mnt\/|[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]|%USERPROFILE%|https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?|sourceRefs?|sourceIds?|mediaIds?|confidenceScore|detailLookupAudit|nameSource|hostChecks|platformAccess|platformItemId|platformEngagement|failureCode|paidPlanningReady|宿主诊断|schemaVersion|paymentCode|operationId|tombstones?)/i;
const CONTENT_LEAK_PATTERN = /(?:\.workbuddy|\.claude|\/Users\/|\/mnt\/|[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]|%USERPROFILE%|https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?|宿主诊断)/i;
const SECRET_PATTERN = /(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk|rk|pk)_[A-Za-z0-9]{20,}\b|\bBearer\s+[A-Za-z0-9._~+/=-]{16,})/i;

function contentForSecretScan(html) {
  return html
    .replace(/data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+/g, "[embedded-image]");
}

function customerTextForScan(html) {
  return contentForSecretScan(html)
    .replace(/\b(?:href|src|action)="[^"]*"/gi, (attribute) => `${attribute.split("=")[0]}="[customer-url]"`);
}

function clean(value, max = 260, field = "content") {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (CONTENT_LEAK_PATTERN.test(text)) {
    return "";
  }
  return text.slice(0, max);
}

function assertSafeUrlAttributes(html) {
  for (const match of html.matchAll(/\b(href|src|action)="([^"]*)"/gi)) {
    const [raw, attribute, value] = match;
    if (attribute.toLowerCase() === "src" && /^data:image\/(?:png|jpeg|webp);base64,/i.test(value)) continue;
    let parsed;
    try {
      parsed = new URL(value);
    } catch {
      throw new Error(`unsafe customer URL attribute: ${raw.slice(0, 80)}`);
    }
    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("unsafe customer URL protocol");
    const host = parsed.hostname.toLowerCase();
    if (host === "localhost" || host === "127.0.0.1" || host === "::1") {
      throw new Error("unsafe customer URL host");
    }
    if (net.isIP(host)) {
      const privateIpv4 = /^(?:0\.|10\.|127\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(host);
      const privateIpv6 = /^(?:::|fc|fd|fe8|fe9|fea|feb)/i.test(host);
      if (privateIpv4 || privateIpv6) throw new Error("unsafe customer URL host");
    }
  }
}

function dataUrlFromPath(file, baseDir) {
  if (!file || /^https?:\/\//i.test(file)) return "";
  const resolved = path.resolve(baseDir, file);
  const relative = path.relative(path.resolve(baseDir), resolved);
  if (relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) {
    throw new Error("unsafe local image path outside passport directory");
  }
  if (!fs.existsSync(resolved)) return "";
  const stat = fs.lstatSync(resolved);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_PHOTO_BYTES) return "";
  const mime = {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp"}[
    path.extname(resolved).toLowerCase()
  ];
  if (!mime) return "";
  const data = fs.readFileSync(resolved);
  if (!imageMagicMatches(data, mime)) throw new Error("unsafe local image content");
  return `data:${mime};base64,${data.toString("base64")}`;
}

function imageMagicMatches(data, mime) {
  if (!Buffer.isBuffer(data) || data.length < 12) return false;
  if (mime === "image/png") return data.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]));
  if (mime === "image/jpeg") return data[0] === 0xff && data[1] === 0xd8 && data[data.length - 2] === 0xff && data[data.length - 1] === 0xd9;
  if (mime === "image/webp") return data.subarray(0, 4).toString("ascii") === "RIFF" && data.subarray(8, 12).toString("ascii") === "WEBP";
  return false;
}

function validatedDataUrl(value) {
  const match = String(value || "").match(/^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$/i);
  if (!match) return "";
  const data = Buffer.from(match[2], "base64");
  if (data.length > MAX_PHOTO_BYTES || !imageMagicMatches(data, match[1].toLowerCase())) {
    throw new Error("unsafe embedded image content");
  }
  return value;
}

function photoFor(place, baseDir) {
  const raw = place.displayPhoto || place.photo || place.placePassport?.photo || {};
  const photo = typeof raw === "string"
    ? (/^data:image\//.test(raw) ? {dataUrl: raw} : {path: raw})
    : raw;
  const source =
    /^data:image\/(?:png|jpeg|webp);base64,/.test(photo.dataUrl || "")
      ? validatedDataUrl(photo.dataUrl)
      : dataUrlFromPath(photo.path || place.photoPath, baseDir);
  if (!source) return "";
  const rawX = Number(photo.focalPoint?.x ?? 0.5);
  const rawY = Number(photo.focalPoint?.y ?? 0.5);
  const x = Math.max(0, Math.min(1, Number.isFinite(rawX) ? rawX : 0.5)) * 100;
  const y = Math.max(0, Math.min(1, Number.isFinite(rawY) ? rawY : 0.5)) * 100;
  return `<div class="photo"><img src="${escapeHtml(source)}" alt="${escapeHtml(clean(place.name, 100))}" style="object-position:${x}% ${y}%"></div>`;
}

function fact(label, value) {
  const safe = clean(value);
  return safe ? `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(safe)}</dd></div>` : "";
}

function publicLinks(place) {
  const links = [];
  for (const item of place.originalSourceLinks || []) {
    const url = typeof item === "string" ? item : item?.url || item?.href || item?.publicRef;
    const safeUrl = customerSafeUrl(url);
    if (safeUrl && !links.includes(safeUrl)) links.push(safeUrl);
  }
  return links;
}

function publicAuditLinks(items) {
  const result = [];
  const seen = new Set();
  for (const item of Array.isArray(items) ? items : []) {
    const url = customerSafeUrl(item?.url);
    if (!/^https?:\/\//i.test(url || "") || seen.has(url)) continue;
    seen.add(url);
    result.push({url, label: clean(item?.label, 100) || url});
  }
  return result;
}

function customerSafeUrl(value) {
  if (!/^https?:\/\//i.test(value || "")) return "";
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return "";
  }
  for (const key of [...parsed.searchParams.keys()]) {
    if (SENSITIVE_QUERY_KEYS.has(key.toLowerCase())) parsed.searchParams.delete(key);
  }
  return parsed.toString();
}

function verificationPanel(place, en) {
  const summary = place.verificationSummary || {};
  const label = clean(summary.statusLabel, 80) || (en ? "Not manually reviewed" : "尚未人工复核");
  const checkedAt = clean(summary.checkedAt, 40);
  const validUntil = clean(summary.validUntil, 40);
  const sources = publicAuditLinks(summary.sources);
  const pending = Array.isArray(summary.pending) ? summary.pending.map((item) => clean(item, 180)).filter(Boolean) : [];
  const actions = Array.isArray(summary.nextActions) ? summary.nextActions.map((item) => clean(item, 180)).filter(Boolean) : [];
  return `<section class="verification" aria-label="${en ? "Verification status" : "核验状态"}">
    <div class="verification-head"><strong>${en ? "Verification status" : "核验状态"}</strong><span>${escapeHtml(label)}</span></div>
    ${checkedAt ? `<p><b>${en ? "Last checked" : "最近检查"}</b>${escapeHtml(checkedAt)}</p>` : ""}
    ${validUntil ? `<p><b>${en ? "Valid until" : "适用期限"}</b>${escapeHtml(validUntil)}</p>` : ""}
    ${sources.length ? `<div class="audit-sources"><b>${en ? "Public sources" : "公开来源"}</b>${sources.map((item) => `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.label)}</a>`).join("")}</div>` : ""}
    ${pending.length ? `<div><b>${en ? "Still unproven" : "尚待确认"}</b><ul>${pending.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
    ${actions.length ? `<div><b>${en ? "Before departure" : "出发前动作"}</b><ul>${actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
  </section>`;
}

function riskPanel(risks, en, className = "risk-list") {
  const safeRisks = Array.isArray(risks) ? risks : [];
  if (!safeRisks.length) return "";
  return `<section class="${className}" aria-label="${en ? "Execution risks" : "执行风险"}">
    <strong>${en ? "Execution risks" : "执行风险"}</strong>
    <div>${safeRisks.map((risk) => {
      const sources = publicAuditLinks(risk.sources);
      return `<article class="risk-item"><div><span>${escapeHtml(clean(risk.scopeLabel, 40))}</span><b>${escapeHtml(clean(risk.label, 80))}</b></div>
        <p>${escapeHtml(clean(risk.summary, 220) || clean(risk.statusLabel, 80))}</p>
        ${risk.nextAction ? `<p><strong>${en ? "Action" : "行动"}</strong>${escapeHtml(clean(risk.nextAction, 180))}</p>` : ""}
        ${sources.length ? `<p class="risk-sources">${sources.map((item) => `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.label)}</a>`).join("")}</p>` : ""}
      </article>`;
    }).join("")}</div>
  </section>`;
}

function publicAreas(places, en) {
  const counts = new Map();
  for (const place of places) {
    const label = clean(place.areaGroup || place.area || place.district || place.neighborhood || place.branch, 80)
      || (en ? "SAVED PLACES" : "未分片区");
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return [...counts.entries()].slice(0, 8);
}

function requestOption(value, label) {
  return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
}

function formCopy(en, key) {
  return FORM_COPY[`${key}${en ? "En" : "Zh"}`];
}

function requestCard(report, locale, placeCount) {
  const en = locale === "en-US";
  const destination = clean(report.destination, 80);
  const fallbackUrl = new URL(FALLBACK_URL);
  fallbackUrl.searchParams.set("destination", destination);
  fallbackUrl.searchParams.set("locale", locale);
  fallbackUrl.searchParams.set("placeCount", String(placeCount));
  return `<section class="upgrade">
    <div class="upgrade-copy" aria-labelledby="paid-options-title">
      <p class="kicker">KORNVIA · ${escapeHtml(formCopy(en, "sectionKicker"))}</p>
      <h2 id="paid-options-title">${escapeHtml(formCopy(en, "sectionTitle"))}</h2>
      <div class="tier-list">
        <article class="tier-card free-tier">
          <p class="tier-name">${escapeHtml(en ? FREE_OFFER.nameEn : FREE_OFFER.nameZh)}</p>
          <p class="tier-price">${escapeHtml(en ? FREE_OFFER.priceEn : FREE_OFFER.price)}</p>
          <ul>${(en ? FREE_OFFER.featuresEn : FREE_OFFER.featuresZh).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </article>
        <article class="tier-card recommended-tier">
          <p class="recommended-label">${escapeHtml(formCopy(en, "recommendedLabel"))}</p>
          <p class="tier-name">${escapeHtml(en ? MANUAL_OFFER.nameEn : MANUAL_OFFER.nameZh)}</p>
          <p class="tier-price">${escapeHtml(MANUAL_OFFER.price)}</p>
          <p>${escapeHtml(en ? MANUAL_OFFER.descriptionEn : MANUAL_OFFER.descriptionZh)}</p>
          <p class="availability">${escapeHtml(en ? MANUAL_OFFER.availabilityEn : MANUAL_OFFER.availabilityZh)}</p>
          <p class="service-boundary">${escapeHtml(en ? MANUAL_OFFER.exclusionsEn.join(" · ") : MANUAL_OFFER.exclusionsZh.join(" · "))}</p>
        </article>
      </div>
    </div>
    <form class="request-form" action="${CTA_URL}" method="post" target="_blank" rel="noopener noreferrer" accept-charset="UTF-8">
      <input type="hidden" name="locale" value="${locale}">
      <input type="hidden" name="placeCount" value="${placeCount}">
      <input type="hidden" name="offerId" value="${escapeHtml(PRODUCT_CONFIG.form.publicOfferId)}">
      <div class="honeypot" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>
      <h3>${escapeHtml(formCopy(en, "requestTitle"))}</h3>
      <div class="request-grid">
        <label>${escapeHtml(formCopy(en, "destinationLabel"))}<input name="destination" value="${escapeHtml(destination)}" required maxlength="80" autocomplete="address-level1"></label>
        <label>${escapeHtml(formCopy(en, "startDateLabel"))}<input name="startDate" type="date" required></label>
        <label>${escapeHtml(formCopy(en, "daysLabel"))}<select name="days" required>
          ${requestOption("", formCopy(en, "selectLabel"))}
          ${requestOption("3", en ? "3 days" : "3 天")}
          ${requestOption("4", en ? "4 days" : "4 天")}
          ${requestOption("5", en ? "5 days" : "5 天")}
          ${requestOption("6", en ? "6 days" : "6 天")}
          ${requestOption("7", en ? "7 days" : "7 天")}
        </select></label>
        <label>${escapeHtml(formCopy(en, "contactLabel"))}<input name="contact" required maxlength="120" placeholder="${escapeHtml(formCopy(en, "contactPlaceholder"))}"></label>
        <label class="full">${escapeHtml(formCopy(en, "notesLabel"))}<textarea name="notes" maxlength="1200" placeholder="${escapeHtml(formCopy(en, "notesPlaceholder"))}"></textarea></label>
      </div>
      <button class="submit" type="submit">${escapeHtml(formCopy(en, "submit"))}</button>
      <div class="request-notice">
        <p><strong>${escapeHtml(formCopy(en, "purposeLabel"))}</strong>${escapeHtml(en ? PRODUCT_CONFIG.form.privacy.purposeEn : PRODUCT_CONFIG.form.privacy.purposeZh)}</p>
        <p><strong>${escapeHtml(formCopy(en, "handlingLabel"))}</strong>${escapeHtml(en ? PRODUCT_CONFIG.form.privacy.handlingEn : PRODUCT_CONFIG.form.privacy.handlingZh)}</p>
        <p><strong>${escapeHtml(formCopy(en, "boundaryLabel"))}</strong>${escapeHtml(en ? PRODUCT_CONFIG.form.privacy.boundaryEn : PRODUCT_CONFIG.form.privacy.boundaryZh)}</p>
      </div>
      <p class="payment-flow">${escapeHtml(en ? PRODUCT_CONFIG.paymentWorkflow.copyEn : PRODUCT_CONFIG.paymentWorkflow.copyZh)}</p>
      <p class="fallback">${escapeHtml(formCopy(en, "fallbackPrefix"))}<a href="${escapeHtml(fallbackUrl.toString())}" target="_blank" rel="noopener noreferrer">${escapeHtml(formCopy(en, "fallbackLink"))}</a>${en ? "." : "。"}</p>
    </form>
  </section>`;
}

function card(place, locale, baseDir) {
  const en = locale === "en-US";
  const route = clean(
    place.routeText ||
    ([place.routeStart, ...(place.waypoints || []), place.routeEnd].filter(Boolean).join(" → ")),
  );
  const links = publicLinks(place);
  return `<article class="card">${photoFor(place, baseDir)}
    <div class="body">
      <p class="eyebrow">${escapeHtml(clean(place.category || (en ? "SAVED PLACE" : "想去地点"), 60))}</p>
      <h2>${escapeHtml(clean(place.displayName || place.name, 120))}</h2>
      <dl class="facts">
        ${fact(en ? "Local name" : "当地名称", place.localName)}
        ${fact(en ? "Branch" : "分店", place.branch)}
        ${fact(en ? "Address" : "地址", place.address || place.positionText)}
        ${fact(en ? "Hours" : "营业", place.openingHoursText)}
        ${fact(en ? "Duration" : "建议时长", place.suggestedDuration)}
        ${fact(en ? "Area" : "片区", place.areaGroup)}
        ${fact(en ? "Accessibility" : "无障碍提示", place.accessibilityNote)}
        ${fact(en ? "Review status" : "复核状态", place.verificationStatus)}
        ${fact(en ? "Why go" : "想去理由", place.signature)}
        ${fact(en ? "Before you go" : "出发提醒", place.visitTip)}
      </dl>
      ${route ? `<div class="route"><strong>${en ? "Route" : "路线"}</strong><br>${escapeHtml(route)}</div>` : ""}
      ${verificationPanel(place, en)}
      ${riskPanel(place.executionRisks, en)}
      ${links.length ? `<div class="sources">${links.map((url) =>
        `<a class="button" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${en ? "Open original saved link" : "打开原始收藏链接"}</a>`
      ).join("")}</div>` : ""}
    </div>
  </article>`;
}

function render(report, inputPath) {
  const locale = report.locale === "en-US" ? "en-US" : "zh-CN";
  const en = locale === "en-US";
  const title = passportTitle(report.destination, locale);
  const places = Array.isArray(report.places) ? report.places : [];
  const retained = Math.max(0, Number(report.retainedClueCount || 0));
  const destination = clean(report.destination, 80) || (en ? "MY TRIP" : "我的旅程");
  const requestedMode = report.presentation?.contentDepth || report.presentation?.contentMode;
  const contentMode = ["deep", "standard", "compact"].includes(requestedMode)
    ? requestedMode
    : "standard";
  const sourceLinkCount = places.reduce((sum, place) => sum + publicLinks(place).length, 0);
  const areas = publicAreas(places, en);
  const baseDir = path.dirname(inputPath);
  const visitorMode = report.presentation?.visitorMode === true;
  const html = `<!doctype html><html lang="${en ? "en" : "zh-CN"}"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title>
  <style>${KORNVIA_REPORT_CSS}</style></head><body><main class="shell">
    <header class="hero ticket">
      <div class="hero-main">
        <div class="brand-row"><strong class="wordmark">Kornvia</strong><span>WANT TO GO PASS</span></div>
        <div class="hero-title"><p class="kicker">${en ? "DESTINATION LIBRARY" : "目的地分库"}</p><h1>${escapeHtml(destination)}</h1>
          <p class="passport-label">${en ? "WANT TO GO PASSPORT" : "想去护照"}</p></div>
        <div class="hero-meta" aria-label="${en ? "Passport summary" : "护照摘要"}">
          <div><span>${en ? "PLACES" : "地点"}</span><strong>${places.length}</strong></div>
          <div><span>${en ? "SOURCES" : "原始链接"}</span><strong>${sourceLinkCount}</strong></div>
          <div><span>${en ? "STATUS" : "状态"}</span><strong>READY</strong></div>
        </div>
      </div>
      <aside class="hero-stub" aria-label="${en ? "Trip document stub" : "行程票存根"}">
        <div><span class="stub-label">DESTINATION</span><p class="stub-destination">${escapeHtml(destination)}</p></div>
        <div class="stub-facts">
          <div><span class="stub-label">VIEW</span><strong>${escapeHtml(contentMode.toUpperCase())}</strong></div>
          <div><span class="stub-label">PLACES</span><strong>${places.length}</strong></div>
          <div><span class="stub-label">SOURCE</span><strong>${sourceLinkCount ? "LINK" : "NONE"}</strong></div>
        </div>
        <p class="stub-note">KORNVIA TRIP DOCUMENT<br>${en ? "ORIGINAL IMAGES AND SOURCES RETAINED" : "原图保留，来源可追溯"}</p>
      </aside>
    </header>
    ${visitorMode ? `<aside class="retained">${en ? "Visitor view: customer-safe fields only." : "访客查看版：仅展示客户可见字段。"}</aside>` : ""}
    <section class="manifest" aria-labelledby="manifest-title">
      <div class="manifest-copy"><p class="kicker">TRIP MANIFEST</p>
        <h2 id="manifest-title">${en ? "A want-to-go itinerary you can take with you" : "一张能带走的想去行程单"}</h2>
        <p>${en ? "Places stay grouped by area, with display images, addresses, reminders and only the source links you actually submitted." : "地点按片区整理，保留展示配图、地址和出发提醒；只有你实际提交过的原始链接才会显示。"}</p>
      </div>
      <div class="area-route" aria-label="${en ? "Area summary" : "片区摘要"}">${areas.map(([area, count], index) => `
        <div class="route-stop"><span class="stop-number">${String(index + 1).padStart(2, "0")}</span><span class="route-name">${escapeHtml(area)}</span><span class="route-count">${count} ${en ? "PLACE" : "个地点"}</span></div>`).join("")}
      </div>
    </section>
    <section class="grid">${places.map((place) => card(place, locale, baseDir)).join("")}</section>
    ${riskPanel(report.tripRisks, en, "trip-risks")}
    ${retained ? `<aside class="retained">${en
      ? `${retained} more saved place clue${retained === 1 ? "" : "s"} remain in your library and can be added after verification.`
      : `另有 ${retained} 条地点线索已保留，核实后可补进下一版。`}</aside>` : ""}
    ${requestCard(report, locale, places.length)}
  </main><!-- ${REPORT_TEMPLATE_VERSION} --></body></html>`;
  return html.replace(/[ \t]+$/gm, "");
}

function main() {
  const [inputPath, outputPath] = process.argv.slice(2);
  if (!inputPath || !outputPath) throw new Error("usage: render_report.mjs input.json output.html");
  const stat = fs.statSync(inputPath);
  if (!stat.isFile() || stat.size > MAX_INPUT_BYTES) throw new Error("invalid input");
  const report = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  if (!Array.isArray(report.places) || report.places.length === 0) throw new Error("no deliverable places");
  const html = render(report, inputPath);
  assertSafeUrlAttributes(html);
  if (INTERNAL_PATTERN.test(customerTextForScan(html))) throw new Error("customer output contains internal text");
  if (SECRET_PATTERN.test(contentForSecretScan(html))) throw new Error("customer output contains secret-like text");
  fs.writeFileSync(outputPath, html);
  const en = report.locale === "en-US";
  process.stdout.write(`${en ? PRODUCT_CONFIG.copy.passportReadyEn : PRODUCT_CONFIG.copy.passportReadyZh}\n`);
}

function rendererErrorCode(error) {
  const message = String(error?.message || error || "");
  if (message.includes("local image")) return "UNSAFE_LOCAL_IMAGE";
  if (message.includes("embedded image")) return "UNSAFE_EMBEDDED_IMAGE";
  if (message.includes("customer URL")) return "UNSAFE_CUSTOMER_URL";
  if (message.includes("internal text")) return "CUSTOMER_INTERNAL_LEAK";
  if (message.includes("secret-like")) return "CUSTOMER_SECRET_LEAK";
  if (message.includes("no deliverable places")) return "NO_DELIVERABLE_PLACES";
  if (message.includes("invalid input") || message.includes("usage:")) return "INVALID_RENDER_INPUT";
  return "RENDER_FAILED";
}

function rendererLocale(inputPath) {
  try {
    const stat = fs.statSync(inputPath);
    if (!stat.isFile() || stat.size > MAX_INPUT_BYTES) return "zh-CN";
    return JSON.parse(fs.readFileSync(inputPath, "utf8")).locale === "en-US" ? "en-US" : "zh-CN";
  } catch {
    return "zh-CN";
  }
}

const RENDER_ERROR_MESSAGES = {
  "zh-CN": {
    UNSAFE_LOCAL_IMAGE: "本地图片不在授权的护照资产目录内，或文件内容与图片类型不一致。",
    UNSAFE_EMBEDDED_IMAGE: "内嵌图片内容无效或超过大小限制。",
    UNSAFE_CUSTOMER_URL: "客户页面包含不安全的链接或协议。",
    CUSTOMER_INTERNAL_LEAK: "客户页面包含内部字段或本地路径，已阻止生成。",
    CUSTOMER_SECRET_LEAK: "客户页面包含疑似密钥或凭证，已阻止生成。",
    NO_DELIVERABLE_PLACES: "没有可交付地点，请先补齐并确认地点信息。",
    INVALID_RENDER_INPUT: "护照输入文件缺失、过大或格式不正确。",
    RENDER_FAILED: "护照页面生成失败，请检查输入数据后重试。",
  },
  "en-US": {
    UNSAFE_LOCAL_IMAGE: "The local image is outside the authorized passport asset directory or its content does not match its image type.",
    UNSAFE_EMBEDDED_IMAGE: "The embedded image is invalid or exceeds the size limit.",
    UNSAFE_CUSTOMER_URL: "The customer page contains an unsafe URL or protocol.",
    CUSTOMER_INTERNAL_LEAK: "The customer page contains an internal field or local path, so rendering was blocked.",
    CUSTOMER_SECRET_LEAK: "The customer page contains a possible secret or credential, so rendering was blocked.",
    NO_DELIVERABLE_PLACES: "There are no deliverable places. Complete and confirm place details first.",
    INVALID_RENDER_INPUT: "The passport input file is missing, too large or invalid.",
    RENDER_FAILED: "The passport page could not be generated. Check the input data and retry.",
  },
};

try {
  main();
} catch (error) {
  const inputPath = process.argv.slice(2)[0] || "";
  const locale = rendererLocale(inputPath);
  const code = rendererErrorCode(error);
  process.stderr.write(`${JSON.stringify({code, message: RENDER_ERROR_MESSAGES[locale][code]})}\n`);
  process.exitCode = 1;
}
