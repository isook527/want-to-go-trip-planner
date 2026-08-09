#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import {
  escapeHtml,
  KORNVIA_REPORT_CSS,
  LIM_PERSONA_DATA_URL,
  passportTitle,
  PRODUCT_CONFIG,
  REPORT_TEMPLATE_VERSION,
} from "./kornvia-brand.mjs";

const MAX_INPUT_BYTES = 12 * 1024 * 1024;
const MAX_PHOTO_BYTES = 16 * 1024 * 1024;
const CTA_URL = PRODUCT_CONFIG.form.requestUrl;
const REVIEW_OFFER = PRODUCT_CONFIG.offers.preTripReview;
const MANUAL_OFFER = PRODUCT_CONFIG.offers.manualItineraryBeta;
const INTERNAL_PATTERN =
  /(?:\.workbuddy|\.claude|\/Users\/|\/mnt\/|localhost|127\.0\.0\.1|sourceRefs?|sourceIds?|mediaIds?|confidenceScore|detailLookupAudit|nameSource|hostChecks|platformAccess|platformItemId|platformEngagement|failureCode|paidPlanningReady|OCR|模型|宿主诊断|schemaVersion|paymentCode|operationId|tombstones?)/i;
const SECRET_PATTERN = /(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk|rk|pk)_[A-Za-z0-9]{20,}\b|\bBearer\s+[A-Za-z0-9._~+/=-]{16,})/i;

function customerTextForScan(html) {
  return html
    .replace(/data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+/g, "[embedded-image]")
    .replace(/<!--.*?-->/gs, "");
}

function clean(value, max = 260) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text || INTERNAL_PATTERN.test(text)) return "";
  return text.slice(0, max);
}

function dataUrlFromPath(file, baseDir) {
  if (!file || /^https?:\/\//i.test(file)) return "";
  const resolved = path.resolve(baseDir, file);
  if (!fs.existsSync(resolved)) return "";
  const stat = fs.lstatSync(resolved);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_PHOTO_BYTES) return "";
  const mime = {".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".webp":"image/webp"}[
    path.extname(resolved).toLowerCase()
  ];
  return mime ? `data:${mime};base64,${fs.readFileSync(resolved).toString("base64")}` : "";
}

function photoFor(place, baseDir) {
  const raw = place.displayPhoto || place.photo || place.placePassport?.photo || {};
  const photo = typeof raw === "string"
    ? (/^data:image\//.test(raw) ? {dataUrl: raw} : {path: raw})
    : raw;
  const source =
    /^data:image\/(?:png|jpeg|webp);base64,/.test(photo.dataUrl || "")
      ? photo.dataUrl
      : dataUrlFromPath(photo.path || place.photoPath, baseDir);
  if (!source) return "";
  const x = Math.max(0, Math.min(1, Number(photo.focalPoint?.x ?? 0.5))) * 100;
  const y = Math.max(0, Math.min(1, Number(photo.focalPoint?.y ?? 0.5))) * 100;
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
    if (/^https?:\/\//i.test(url || "") && !links.includes(url)) links.push(url);
  }
  return links;
}

function requestOption(value, label) {
  return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
}

function requestCard(report, locale, placeCount) {
  const en = locale === "en-US";
  const destination = clean(report.destination, 80);
  const fallbackUrl = new URL(CTA_URL);
  fallbackUrl.searchParams.set("destination", destination);
  fallbackUrl.searchParams.set("locale", locale);
  fallbackUrl.searchParams.set("placeCount", String(placeCount));
  return `<section class="upgrade">
    <div class="upgrade-copy" aria-labelledby="paid-options-title">
      <p class="kicker">KORNVIA · ${en ? "OPTIONAL SERVICES" : "按需服务"}</p>
      <div class="upgrade-price"><strong>${escapeHtml(REVIEW_OFFER.price)}</strong><span>${en ? "/ pre-trip review" : "/ 出发前复核"}</span></div>
      <h2 id="paid-options-title">${en ? "Recheck saved places before departure" : "临近出发，再复核一次"}</h2>
      <p>${escapeHtml(en ? REVIEW_OFFER.descriptionEn : REVIEW_OFFER.descriptionZh)}</p>
      <ul class="upgrade-benefits">
        <li>${en ? "Shows a dated change diff against the last review" : "按复核时间交付变更 diff"}</li>
        <li>${en ? "Does not promise a full day-by-day itinerary" : "不承诺完整逐日行程"}</li>
      </ul>
      <div class="manual-offer">
        <p class="manual-price">${escapeHtml(MANUAL_OFFER.price)} · ${escapeHtml(en ? MANUAL_OFFER.nameEn : MANUAL_OFFER.nameZh)}</p>
        <p>${escapeHtml(en ? MANUAL_OFFER.descriptionEn : MANUAL_OFFER.descriptionZh)}</p>
        <p class="fine-print">${escapeHtml(en ? MANUAL_OFFER.exclusionsEn.join(" · ") : MANUAL_OFFER.exclusionsZh.join(" · "))}</p>
      </div>
    </div>
    <form class="request-form" action="${CTA_URL}" method="post" target="_blank" rel="noopener noreferrer" accept-charset="UTF-8" referrerpolicy="no-referrer">
      <input type="hidden" name="locale" value="${locale}">
      <input type="hidden" name="placeCount" value="${placeCount}">
      <div class="honeypot" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>
      <h3>${en ? "Send a service request" : "提交服务需求"}</h3>
      <div class="request-grid">
        <label class="full">${en ? "Service" : "服务类型"}<select name="offerId" required>
          ${requestOption(REVIEW_OFFER.id, `${REVIEW_OFFER.price} · ${en ? REVIEW_OFFER.nameEn : REVIEW_OFFER.nameZh}`)}
          ${requestOption(MANUAL_OFFER.id, `${MANUAL_OFFER.price} · ${en ? MANUAL_OFFER.nameEn : MANUAL_OFFER.nameZh}`)}
        </select></label>
        <label>${en ? "Destination" : "目的地"}<input name="destination" value="${escapeHtml(destination)}" required maxlength="80" autocomplete="address-level1"></label>
        <label>${en ? "Start date" : "出发日期"}<input name="startDate" type="date" required></label>
        <label>${en ? "Trip length" : "旅行天数"}<select name="days" required>
          ${requestOption("", en ? "Select" : "请选择")}
          ${requestOption("1", en ? "1 day" : "1 天")}
          ${requestOption("2", en ? "2 days" : "2 天")}
          ${requestOption("3", en ? "3 days" : "3 天")}
        </select></label>
        <label>${en ? "Contact" : "联系方式"}<input name="contact" required maxlength="120" placeholder="${en ? "WeChat / phone / email" : "微信号 / 手机号 / 邮箱"}"></label>
        <label class="full">${en ? "Travel party and preferences" : "同行与偏好"}<textarea name="notes" maxlength="1200" placeholder="${en ? "Party size, walking limit, must-go places, diet or hotel area." : "人数、步行上限、必去地点、饮食禁忌或住宿区域。"}"></textarea></label>
      </div>
      <button class="submit" type="submit">${en ? "Send request — no charge now" : "提交需求，不会立即扣款"}</button>
      <p class="fine-print">${en
        ? "A receipt with a request number opens after the service saves your request."
        : "服务保存成功后会打开带需求编号的回执页。"}</p>
      <p class="fallback">${en ? "If no receipt opens, " : "如果提交后没有看到需求编号，"}<a href="${escapeHtml(fallbackUrl.toString())}" target="_blank" rel="noopener noreferrer">${en ? "use the backup request page" : "打开备用需求页"}</a>${en ? "." : "。"}</p>
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
        ${fact(en ? "Why go" : "想去理由", place.signature)}
        ${fact(en ? "Before you go" : "出发提醒", place.visitTip)}
      </dl>
      ${route ? `<div class="route"><strong>${en ? "Route" : "路线"}</strong><br>${escapeHtml(route)}</div>` : ""}
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
  const subtitle = en
    ? "Saved places, addresses, opening notes and practical reminders—ready in one guide."
    : "收藏的地点、地址、营业信息和出发提醒，都整理在这里。";
  const baseDir = path.dirname(inputPath);
  const visitorMode = report.presentation?.visitorMode === true;
  return `<!doctype html><html lang="${en ? "en" : "zh-CN"}"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title>
  <style>${KORNVIA_REPORT_CSS}</style></head><body><main class="shell">
    <header class="hero"><p class="kicker">KORNVIA · ${en ? "WANT TO GO" : "想去就出发"}</p>
      <h1>${escapeHtml(title)}</h1><p>${escapeHtml(subtitle)}</p>
      <img class="lim" src="${LIM_PERSONA_DATA_URL}" alt="" aria-hidden="true"></header>
    ${visitorMode ? `<aside class="retained">${en ? "Visitor view: customer-safe fields only." : "访客查看版：仅展示客户可见字段。"}</aside>` : ""}
    <section class="grid">${places.map((place) => card(place, locale, baseDir)).join("")}</section>
    ${retained ? `<aside class="retained">${en
      ? `${retained} more saved place clue${retained === 1 ? "" : "s"} remain in your library and can be added after verification.`
      : `另有 ${retained} 条地点线索已保留，核实后可补进下一版。`}</aside>` : ""}
    ${requestCard(report, locale, places.length)}
  </main><!-- ${REPORT_TEMPLATE_VERSION} --></body></html>`;
}

function main() {
  const [inputPath, outputPath] = process.argv.slice(2);
  if (!inputPath || !outputPath) throw new Error("usage: render_report.mjs input.json output.html");
  const stat = fs.statSync(inputPath);
  if (!stat.isFile() || stat.size > MAX_INPUT_BYTES) throw new Error("invalid input");
  const report = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  if (!Array.isArray(report.places) || report.places.length === 0) throw new Error("no deliverable places");
  const html = render(report, inputPath);
  if (INTERNAL_PATTERN.test(customerTextForScan(html))) throw new Error("customer output contains internal text");
  if (SECRET_PATTERN.test(customerTextForScan(html))) throw new Error("customer output contains secret-like text");
  fs.writeFileSync(outputPath, html);
  const en = report.locale === "en-US";
  process.stdout.write(`${en ? PRODUCT_CONFIG.copy.passportReadyEn : PRODUCT_CONFIG.copy.passportReadyZh}\n`);
}

main();
