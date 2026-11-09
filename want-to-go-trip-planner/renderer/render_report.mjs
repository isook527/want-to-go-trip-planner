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
const SENSITIVE_QUERY_KEYS = new Set([
  "xsec_token", "xsec_source", "access_token", "refresh_token", "auth_token",
  "authorization", "api_key", "apikey", "signature", "sig", "igsh",
]);
const INTERNAL_PATTERN =
  /(?:\.workbuddy|\.claude|%USERPROFILE%|https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?|sourceRefs?|sourceIds?|mediaIds?|confidenceScore|detailLookupAudit|nameSource|hostChecks|platformAccess|platformItemId|platformEngagement|failureCode|paidPlanningReady|宿主诊断|schemaVersion|paymentCode|operationId|tombstones?)/i;
const CONTENT_LEAK_PATTERN = /(?:\.workbuddy|\.claude|%USERPROFILE%|https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?|宿主诊断)/i;
const LOCAL_PATH_PATTERN = /(?:(?:^|[^A-Za-z0-9:])\/(?:Applications|Library|System|Users|bin|dev|etc|home|lib64?|media|mnt|opt|private|proc|root|run|sbin|srv|tmp|usr|var|Volumes|workspace)\/|(?:^|[^A-Za-z0-9])[A-Za-z]:[\\/]|(?:^|[^\\/])\\\\[^\\/\s<>:"|?*]+[\\/][^\\/\s<>:"|?*]+)/im;
const SECRET_PATTERN = /(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk|rk|pk)_[A-Za-z0-9]{20,}\b|\bBearer\s+[A-Za-z0-9._~+/=-]{16,})/i;
const CUSTOMER_CREDENTIAL_PATTERN = /(?:\b(?:xsec[_-]?token|xsec[_-]?source|auth[_-]?token|authorization|api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|password|cookie)[ \t]*[:=][ \t]*[^\s,;<>"']{8,}|\b(?:signature|sig|igsh)[ \t]*[:=][ \t]*(?=[^\s,;<>"']{12,})[A-Za-z0-9._~+/=-]{12,})/i;
const BLOCKED_HOST_SUFFIXES = [".localhost", ".local", ".lan", ".internal", ".home", ".home.arpa"];

function decodeHtmlEntities(value) {
  const named = {amp: "&", quot: '"', apos: "'", lt: "<", gt: ">"};
  let decoded = String(value);
  for (let pass = 0; pass < 3; pass += 1) {
    const next = decoded.replace(/&(?:#(\d+)|#x([0-9a-f]+)|(amp|quot|apos|lt|gt));/gi, (_match, decimal, hex, name) => {
      if (decimal) return String.fromCodePoint(Number.parseInt(decimal, 10));
      if (hex) return String.fromCodePoint(Number.parseInt(hex, 16));
      return named[String(name).toLowerCase()] || _match;
    });
    if (next === decoded) break;
    decoded = next;
  }
  return decoded;
}

function normalizeCredentialWhitespace(value) {
  const keys = "xsec[_-]?token|xsec[_-]?source|auth[_-]?token|authorization|api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|password|cookie|signature|sig|igsh";
  const sameLine = String(value).replace(
    new RegExp(`\\b(${keys})[ \\t]{0,4}([:=])[ \\t]{0,4}`, "gi"),
    (_match, key, delimiter) => `${key}${delimiter}`,
  );
  return sameLine.replace(
    new RegExp(
      `\\b(${keys})([ \\t\\r\\n]{0,8})([:=])([ \\t\\r\\n]{0,8})(?=[A-Za-z0-9._~+/=-]{16,})`,
      "gi",
    ),
    (match, key, before, delimiter, after) => {
      const whitespace = `${before}${after}`;
      if (!/[\r\n]/.test(whitespace) || whitespace.length > 8) return match;
      return `${key}${delimiter}`;
    },
  );
}

function contentForSecretScan(html) {
  return normalizeCredentialWhitespace(decodeHtmlEntities(
    html.replace(/data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+/g, "[embedded-image]")
  ));
}

function customerTextForScan(html) {
  return contentForSecretScan(html)
    .replace(/\b(?:href|src|action)="[^"]*"/gi, (attribute) => `${attribute.split("=")[0]}="[customer-url]"`);
}

function clean(value, max = 260, field = "content") {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (CONTENT_LEAK_PATTERN.test(text) || LOCAL_PATH_PATTERN.test(text)) {
    return "";
  }
  return text.slice(0, max);
}

function assertSafeUrlAttributes(html) {
  const dayIds = new Set([...html.matchAll(/\bid="(day-\d{2,})"/g)].map((match) => match[1]));
  for (const match of html.matchAll(/\b(href|src|action)="([^"]*)"/gi)) {
    const [raw, attribute, value] = match;
    if (attribute.toLowerCase() === "href" && value.startsWith("#") && dayIds.has(value.slice(1))) continue;
    if (attribute.toLowerCase() === "src" && /^data:image\/(?:png|jpeg|webp);base64,/i.test(value)) continue;
    let parsed;
    try {
      parsed = new URL(value);
    } catch {
      throw new Error(`unsafe customer URL attribute: ${raw.slice(0, 80)}`);
    }
    if (parsed.protocol !== "https:") throw new Error("unsafe customer URL protocol");
    const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    if (parsed.username || parsed.password || (parsed.port && parsed.port !== "443")) {
      throw new Error("unsafe customer URL host");
    }
    if (host === "localhost" || (!host.includes(".") && !net.isIP(host)) || BLOCKED_HOST_SUFFIXES.some((suffix) => host.endsWith(suffix))) {
      throw new Error("unsafe customer URL host");
    }
    if (net.isIP(host)) {
      const privateIpv4 = /^(?:0\.|10\.|127\.|169\.254\.|192\.(?:0\.|168\.)|172\.(?:1[6-9]|2\d|3[01])\.|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|198\.(?:18|19)\.|(?:22[4-9]|23\d|24\d|25[0-5])\.)/.test(host);
      const privateIpv6 = /^(?:::|fc|fd|fe8|fe9|fea|feb|2001:db8|ff)/i.test(host);
      if (privateIpv4 || privateIpv6) throw new Error("unsafe customer URL host");
    }
  }
}

function dataUrlFromPath(file, baseDir) {
  if (!file || /^https?:\/\//i.test(file)) return "";
  const lexicalBase = path.resolve(baseDir);
  const resolved = path.resolve(lexicalBase, file);
  const relative = path.relative(lexicalBase, resolved);
  if (relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) {
    throw new Error("unsafe local image path outside passport directory");
  }
  if (!fs.existsSync(resolved)) return "";
  const realBase = fs.realpathSync(lexicalBase);
  const realResolved = fs.realpathSync(resolved);
  const realRelative = path.relative(realBase, realResolved);
  if (realRelative.startsWith(`..${path.sep}`) || realRelative === ".." || path.isAbsolute(realRelative)) {
    throw new Error("unsafe local image path outside passport directory");
  }
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
  if (!/^https:\/\//i.test(value || "")) return "";
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return "";
  }
  const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (
    parsed.protocol !== "https:"
    || parsed.username
    || parsed.password
    || (parsed.port && parsed.port !== "443")
    || host === "localhost"
    || (!host.includes(".") && !net.isIP(host))
    || BLOCKED_HOST_SUFFIXES.some((suffix) => host.endsWith(suffix))
  ) return "";
  if (net.isIP(host)) {
    const privateIpv4 = /^(?:0\.|10\.|127\.|169\.254\.|192\.(?:0\.|168\.)|172\.(?:1[6-9]|2\d|3[01])\.|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|198\.(?:18|19)\.|(?:22[4-9]|23\d|24\d|25[0-5])\.)/.test(host);
    const privateIpv6 = /^(?:::|fc|fd|fe8|fe9|fea|feb|2001:db8|ff)/i.test(host);
    if (privateIpv4 || privateIpv6) return "";
  }
  for (const key of [...parsed.searchParams.keys()]) {
    if (SENSITIVE_QUERY_KEYS.has(key.toLowerCase().replaceAll("-", "_"))) parsed.searchParams.delete(key);
  }
  return parsed.toString();
}

function atomicWriteFile(outputPath, content) {
  const directory = path.dirname(path.resolve(outputPath));
  const temporary = path.join(directory, `.${path.basename(outputPath)}.${process.pid}.${Date.now()}.tmp`);
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(descriptor, content, {encoding: "utf8"});
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = undefined;
    try { fs.chmodSync(temporary, 0o600); } catch {}
    fs.renameSync(temporary, outputPath);
    try {
      const directoryDescriptor = fs.openSync(directory, "r");
      try { fs.fsyncSync(directoryDescriptor); } finally { fs.closeSync(directoryDescriptor); }
    } catch {}
  } catch (error) {
    if (descriptor !== undefined) fs.closeSync(descriptor);
    try { fs.unlinkSync(temporary); } catch {}
    throw error;
  }
}

function verificationPanel(place, en) {
  const summary = place.verificationSummary || {};
  const status = summary.status || "unverified";
  const sources = publicAuditLinks(summary.sources);
  if (status === "unverified" && !sources.length) {
    return `<p class="verification-fallback">${en ? "Check opening hours and transport before you go." : "出发前确认营业与交通"}</p>`;
  }
  const tone = status === "verified" ? "positive" : ["conflict", "stale"].includes(status) ? "urgent" : "caution";
  const label = clean(summary.statusLabel, 80) || (en ? "Check before you go" : "出发前再确认");
  const primary = Array.isArray(summary.items) ? summary.items.find((item) => item.status === status) : null;
  const topic = clean(primary?.label, 80);
  const checkedAt = clean(summary.checkedAtLabel, 60);
  const validUntil = clean(summary.validUntilLabel, 60);
  const headline = en ? [topic, label, checkedAt].filter(Boolean).join(" · ")
    : [topic + label, checkedAt].filter(Boolean).join(" · ");
  const pending = Array.isArray(summary.pending)
    ? summary.pending.map((item) => clean(item, 180)).filter((item) => item && !/^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/i.test(item))
    : [];
  const actions = Array.isArray(summary.nextActions) ? summary.nextActions.map((item) => clean(item, 180)).filter(Boolean) : [];
  return `<details class="verification tone-${tone}">
    <summary><span class="status-dot" aria-hidden="true"></span><span>${escapeHtml(headline)}</span></summary>
    <div class="verification-body">
      ${validUntil ? `<p>${escapeHtml(validUntil)}</p>` : ""}
      ${sources.length ? `<div class="audit-sources"><b>${en ? "Sources" : "参考来源"}</b>${sources.map((item) => `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.label)}</a>`).join("")}</div>` : ""}
      ${pending.length ? `<div><b>${en ? "What to check" : "出发前留意"}</b><ul>${pending.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
      ${actions.length ? `<div><b>${en ? "Before you go" : "出发前准备"}</b><ul>${actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
    </div>
  </details>`;
}

function riskPanel(risks, en, className = "risk-list") {
  const safeRisks = Array.isArray(risks) ? risks : [];
  if (!safeRisks.length) return "";
  return `<section class="${className}" aria-label="${en ? "Before you go" : "行前提醒"}">
    <strong>${en ? "Before you go" : "行前提醒"}</strong>
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

function placeArea(place, destination) {
  for (const key of ["area", "areaGroup", "district"]) {
    const value = typeof place[key] === "string" ? clean(place[key], 80) : "";
    if (value) return value;
  }
  const parts = clean(place.address, 400).split(/[,，]/).map((part) => part.trim()).filter(Boolean);
  if (parts.length < 3) return "";
  const candidate = parts.at(-2).replace(/\s+\d{4,6}$/, "").trim();
  const cityNames = [place.city, place.destination, destination, parts.at(-1)].map(
    (name) => clean(name, 80).replace(/\s+\d{4,6}$/, "").toLowerCase(),
  );
  return cityNames.includes(candidate.toLowerCase()) ? "" : clean(candidate, 80);
}

function publicAreas(places, destination) {
  const groups = new Map();
  let unknown = 0;
  for (const place of places) {
    const label = placeArea(place, destination);
    if (!label) {
      unknown += 1;
      continue;
    }
    const key = label.toLowerCase();
    const group = groups.get(key) || [label, 0];
    group[1] += 1;
    groups.set(key, group);
  }
  return {groups: [...groups.values()], unknown};
}

function dayNavTitle(value, number) {
  const title = clean(value, 140);
  const english = /^day\s+0*(\d+)$/i.exec(title);
  const numericZh = /^第\s*0*(\d+)\s*天$/.exec(title);
  const digits = "零一二三四五六七八九";
  const units = number % 10;
  const tens = Math.floor(number / 10);
  const zhNumber = tens ? `${tens === 1 ? "" : digits[tens]}十${units ? digits[units] : ""}` : digits[units];
  return Number(english?.[1] || numericZh?.[1]) === number
    || title.replace(/\s+/g, "") === `第${zhNumber}天` ? "" : title;
}

function routePlanSection(plan, en) {
  if (!plan || !Array.isArray(plan.days) || !plan.days.length) return "";
  const days = plan.days;
  const dayId = (index) => `day-${String(index + 1).padStart(2, "0")}`;
  const dayNav = days.map((day, index) => {
    const number = Number(day.day) || index + 1;
    const title = dayNavTitle(day.title, number);
    return `<a href="#${dayId(index)}"><span>DAY ${String(number).padStart(2, "0")}</span>${title ? ` · ${escapeHtml(title)}` : ""}</a>`;
  }).join("");
  const noteLabels = en
    ? {lunch: "Lunch", rest: "Rest", backup: "Backup", dropIfDelayed: "Skip if delayed", tradeoff: "Trade-off"}
    : {lunch: "午餐", rest: "休息", backup: "备选", dropIfDelayed: "超时则跳过", tradeoff: "取舍"};
  const dayCards = days.map((day, index) => `<article class="day-card" id="${dayId(index)}">
    <header><span>DAY ${String(day.day || index + 1).padStart(2, "0")}</span><h3>${escapeHtml(clean(day.title, 140))}</h3></header>
    ${day.summary ? `<p class="day-summary">${escapeHtml(clean(day.summary, 420))}</p>` : ""}
    <ol>${(day.stops || []).map((stop) => `<li${stop.type === "activity" ? ' class="day-activity"' : ""}><div><span>${escapeHtml(clean(stop.timeLabel, 60))}</span><strong>${escapeHtml(clean(stop.name, 120))}</strong></div>
      ${stop.address ? `<p>${escapeHtml(clean(stop.address, 220))}</p>` : ""}
      ${stop.note ? `<p>${escapeHtml(clean(stop.note, 320))}</p>` : ""}</li>`).join("")}</ol>
    ${Object.entries(noteLabels).some(([key]) => day[key]) ? `<dl>${Object.entries(noteLabels).filter(([key]) => day[key]).map(([key, label]) => `<dt>${label}</dt><dd>${escapeHtml(clean(day[key], 300))}</dd>`).join("")}</dl>` : ""}
    ${(day.connections || []).length ? `<div class="connections">${day.connections.map((connection) => `<p><strong>${escapeHtml(clean(connection.from, 100))} → ${escapeHtml(clean(connection.to, 100))}</strong><span>${escapeHtml(clean(connection.modeLabel, 80))}${connection.durationText ? ` · ${escapeHtml(clean(connection.durationText, 120))}` : ""} · ${escapeHtml(clean(connection.statusLabel, 80))}</span>${connection.note ? `<small>${escapeHtml(clean(connection.note, 220))}</small>` : ""}</p>`).join("")}</div>` : ""}
  </article>`).join("");
  const unassigned = Array.isArray(plan.unassigned) ? plan.unassigned : [];
  return `<section class="journey-plan" aria-labelledby="route-plan-title">
    <div class="journey-heading"><p class="kicker">${en ? "DAY-BY-DAY PLAN" : "逐日行程"}</p><h2 id="route-plan-title">${escapeHtml(clean(plan.title, 180) || (en ? "Your day-by-day route" : "按天想去路线"))}</h2></div>
    <nav class="day-nav" aria-label="${en ? "Jump to day" : "跳到某天行程"}">${dayNav}</nav>
    <div class="day-grid">${dayCards}</div>
    ${unassigned.length ? `<aside class="unassigned"><strong>${en ? "Saved for another route" : "本轮未排入"}</strong><ul>${unassigned.map((item) => `<li>${escapeHtml(clean(item.name, 120))}${item.reason ? `：${escapeHtml(clean(item.reason, 220))}` : ""}</li>`).join("")}</ul></aside>` : ""}
    <p class="route-boundary">${escapeHtml(clean(plan.boundary, 420))}</p>
  </section>`;
}

function card(place, locale, baseDir, mapRegion, destination) {
  const en = locale === "en-US";
  const route = clean(
    place.routeText ||
    ([place.routeStart, ...(place.waypoints || []), place.routeEnd].filter(Boolean).join(" → ")),
  );
  const links = publicLinks(place);
  const query = [place.displayName || place.name, place.branch, place.address || place.positionText, place.city || destination].filter(Boolean).join(" ");
  mapRegion = ["mainland", "outside_mainland", "unknown"].includes(place.mapRegion) ? place.mapRegion : mapRegion;
  const mapSearch = mapRegion === "mainland"
    ? `https://uri.amap.com/search?${new URLSearchParams({keyword: query})}`
    : mapRegion === "outside_mainland"
      ? `https://www.google.com/maps/search/?${new URLSearchParams({api: "1", query})}`
      : "";
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
        ${fact(en ? "Area" : "片区", placeArea(place, destination))}
        ${fact(en ? "Accessibility" : "无障碍提示", place.accessibilityNote)}
        ${fact(en ? "Review status" : "复核状态", place.verificationStatus)}
        ${fact(en ? "Why go" : "想去理由", place.signature)}
        ${fact(en ? "Before you go" : "出发提醒", place.visitTip)}
      </dl>
      ${route ? `<div class="route"><strong>${en ? "Route" : "路线"}</strong><br>${escapeHtml(route)}</div>` : ""}
      ${mapSearch ? `<p><a class="button" href="${escapeHtml(mapSearch)}" target="_blank" rel="noopener noreferrer">${mapRegion === "mainland" ? (en ? "Search Amap" : "高德地图搜索") : (en ? "Search Google Maps" : "Google 地图搜索")}</a></p>` : ""}
      ${riskPanel(place.executionRisks, en)}
      ${links.length ? `<div class="sources">${links.map((url) =>
        `<a class="button" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${en ? "Open original saved link" : "打开原始收藏链接"}</a>`
      ).join("")}</div>` : ""}
      ${verificationPanel(place, en)}
    </div>
  </article>`;
}

function tripDateRange(plan, dayCount, locale) {
  const value = plan?.tripProfile?.startDate;
  if (!dayCount || !/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "";
  const start = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(start.getTime()) || start.toISOString().slice(0, 10) !== value) return "";
  const end = new Date(start.getTime() + (dayCount - 1) * 86_400_000);
  const format = new Intl.DateTimeFormat(locale, {
    month: "short", day: "numeric", timeZone: "UTC",
    ...(start.getUTCFullYear() === end.getUTCFullYear() ? {} : {year: "numeric"}),
  });
  return dayCount === 1 ? format.format(start) : format.formatRange(start, end);
}

function render(report, inputPath) {
  const locale = report.locale === "en-US" ? "en-US" : "zh-CN";
  const en = locale === "en-US";
  const title = passportTitle(report.destination, locale);
  const places = Array.isArray(report.places) ? report.places : [];
  const retained = Math.max(0, Number(report.retainedClueCount || 0));
  const destination = clean(report.destination, 80) || (en ? "MY TRIP" : "我的旅程");
  const {groups, unknown} = publicAreas(places, destination);
  const areaCount = groups.length;
  const areas = unknown > places.length / 2 ? []
    : [...groups, ...(unknown ? [[en ? "Area to confirm" : "待定片区", unknown]] : [])];
  const baseDir = path.dirname(inputPath);
  const dayCount = Array.isArray(report.routePlan?.days) ? report.routePlan.days.length : 0;
  const dateRange = tripDateRange(report.routePlan, dayCount, locale);
  const stubAreas = groups.slice(0, 3).map(([area]) => area).join(" · ");
  const brief = [
    dayCount ? (en ? `${dayCount} ${dayCount === 1 ? "day" : "days"}` : `${dayCount} 天`) : "",
    en ? `${places.length} ${places.length === 1 ? "place" : "places"}` : `${places.length} 个地点`,
    areaCount ? (en ? `${areaCount} ${areaCount === 1 ? "area" : "areas"}` : `${areaCount} 个片区`) : "",
    dateRange,
  ].filter(Boolean).join(" · ");
  const html = `<!doctype html><html lang="${en ? "en" : "zh-CN"}"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title>
  <style>${KORNVIA_REPORT_CSS}</style></head><body><main class="shell">
    <header class="hero ticket">
      <div class="hero-main">
        <div class="brand-row"><strong class="wordmark">Kornvia</strong><span>WANT TO GO PASS</span></div>
        <div class="hero-title"><p class="kicker">${en ? "TRAVEL DESTINATION" : "旅行目的地"}</p><h1>${escapeHtml(destination)}</h1>
          <p class="passport-label">${en ? "WANT TO GO PASSPORT" : "想去护照"}</p></div>
        <div class="hero-meta" aria-label="${en ? "Passport summary" : "护照摘要"}">
          <div><span>${en ? "DAYS" : "行程天数"}</span><strong>${dayCount || "—"}</strong></div>
          <div><span>${en ? "PLACES" : "地点"}</span><strong>${places.length}</strong></div>
          ${areaCount ? `<div><span>${en ? "AREAS" : "片区"}</span><strong>${areaCount}</strong></div>` : ""}
        </div>
        <p class="hero-summary">${escapeHtml(brief)}</p>
      </div>
      <aside class="hero-stub" aria-label="${en ? "Trip document stub" : "行程票存根"}">
        <div><span class="stub-label">${en ? "DESTINATION" : "目的地"}</span><p class="stub-destination">${escapeHtml(destination)}</p></div>
        <div class="stub-detail"><span class="stub-label">${dateRange ? (en ? "TRIP DATES" : "行程日期") : (en ? "TRIP DAYS" : "行程天数")}</span><strong>${dateRange ? escapeHtml(dateRange) : dayCount || "—"}</strong></div>
        ${stubAreas ? `<div class="stub-detail stub-areas"><span class="stub-label">${en ? "AREAS" : "片区"}</span><strong>${escapeHtml(stubAreas)}</strong></div>` : ""}
        <p class="stub-note">KORNVIA TRIP DOCUMENT</p>
      </aside>
    </header>
    ${routePlanSection(report.routePlan, en)}
    ${areaCount ? `<section class="manifest" aria-labelledby="manifest-title">
      <div class="manifest-copy"><p class="kicker">TRIP MANIFEST</p>
        <h2 id="manifest-title">${en ? "A want-to-go itinerary you can take with you" : "一张能带走的想去行程单"}</h2>
        <p>${en ? "Places stay grouped by area, with display images, addresses, reminders and only the source links you actually submitted." : "地点按片区整理，保留展示配图、地址和出发提醒；只有你实际提交过的原始链接才会显示。"}</p>
      </div>
      ${areas.length ? `<div class="area-route" aria-label="${en ? "Area summary" : "片区摘要"}">${areas.slice(0, 8).map(([area, count], index) => `
        <div class="route-stop"><span class="stop-number">${String(index + 1).padStart(2, "0")}</span><span class="route-name">${escapeHtml(area)}</span><span class="route-count">${count} ${en ? (count === 1 ? "PLACE" : "PLACES") : "个地点"}</span></div>`).join("")}
      </div>` : ""}
    </section>` : ""}
    <section class="grid">${places.map((place) => card(place, locale, baseDir, report.mapRegion, destination)).join("")}</section>
    ${riskPanel(report.tripRisks, en, "trip-risks")}
    ${retained ? `<aside class="retained">${en
      ? `${retained} more saved place clue${retained === 1 ? "" : "s"} remain in your library and can be added after verification.`
      : `另有 ${retained} 条地点线索已保留，核实后可补进下一版。`}</aside>` : ""}
  </main><!-- ${REPORT_TEMPLATE_VERSION} --></body></html>`;
  return html.replace(/[ \t]+$/gm, "");
}

function main() {
  const [inputPath, outputPath] = process.argv.slice(2);
  if (!inputPath || !outputPath) throw new Error("usage: render_report.mjs input.json output.html");
  const stat = fs.statSync(inputPath);
  if (!stat.isFile() || stat.size > MAX_INPUT_BYTES) throw new Error("invalid input");
  const report = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  if (!Array.isArray(report.places) || (!report.places.length && report.lightweight !== true)) {
    throw new Error("no deliverable places");
  }
  if (report.placesOnly !== true && (!Array.isArray(report.routePlan?.days) || report.routePlan.days.length === 0)) throw new Error("no route plan");
  const html = render(report, inputPath);
  assertSafeUrlAttributes(html);
  const customerScanContent = customerTextForScan(html);
  if (INTERNAL_PATTERN.test(customerScanContent) || LOCAL_PATH_PATTERN.test(customerScanContent)) {
    throw new Error("customer output contains internal text");
  }
  const secretScanContent = contentForSecretScan(html);
  if (SECRET_PATTERN.test(secretScanContent) || CUSTOMER_CREDENTIAL_PATTERN.test(secretScanContent)) {
    throw new Error("customer output contains secret-like text");
  }
  atomicWriteFile(outputPath, html);
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
  if (message.includes("no route plan")) return "NO_ROUTE_PLAN";
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
    NO_ROUTE_PLAN: "缺少按天路线；请先完成行程问诊并保存逐日路线。",
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
    NO_ROUTE_PLAN: "The day-by-day route is missing. Complete trip intake and save the day-by-day route first.",
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
