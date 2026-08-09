#!/usr/bin/env node
import fs from "node:fs";
import net from "node:net";
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
const FALLBACK_URL = PRODUCT_CONFIG.form.fallbackUrl;
const FORM_COPY = PRODUCT_CONFIG.form.copy;
const FREE_OFFER = PRODUCT_CONFIG.offers.free;
const MANUAL_OFFER = PRODUCT_CONFIG.offers.manualItineraryBeta;
const INTERNAL_PATTERN =
  /(?:\.workbuddy|\.claude|\/Users\/|\/mnt\/|localhost|127\.0\.0\.1|sourceRefs?|sourceIds?|mediaIds?|confidenceScore|detailLookupAudit|nameSource|hostChecks|platformAccess|platformItemId|platformEngagement|failureCode|paidPlanningReady|宿主诊断|schemaVersion|paymentCode|operationId|tombstones?)/i;
const CONTENT_LEAK_PATTERN = /(?:\.workbuddy|\.claude|\/Users\/|\/mnt\/|localhost|127\.0\.0\.1|宿主诊断)/i;
const SECRET_PATTERN = /(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk|rk|pk)_[A-Za-z0-9]{20,}\b|\bBearer\s+[A-Za-z0-9._~+/=-]{16,})/i;

function contentForSecretScan(html) {
  return html
    .replace(/data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+/g, "[embedded-image]")
    .replace(/<!--.*?-->/gs, "");
}

function customerTextForScan(html) {
  return contentForSecretScan(html)
    .replace(/\b(?:href|src|action)="[^"]*"/gi, (attribute) => `${attribute.split("=")[0]}="[customer-url]"`);
}

function clean(value, max = 260, field = "content") {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (CONTENT_LEAK_PATTERN.test(text)) {
    process.stderr.write(`suppressed unsafe ${field}\n`);
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
  assertSafeUrlAttributes(html);
  if (INTERNAL_PATTERN.test(customerTextForScan(html))) throw new Error("customer output contains internal text");
  if (SECRET_PATTERN.test(contentForSecretScan(html))) throw new Error("customer output contains secret-like text");
  fs.writeFileSync(outputPath, html);
  const en = report.locale === "en-US";
  process.stdout.write(`${en ? PRODUCT_CONFIG.copy.passportReadyEn : PRODUCT_CONFIG.copy.passportReadyZh}\n`);
}

main();
