import fs from "node:fs";

export const PRODUCT_CONFIG = JSON.parse(
  fs.readFileSync(new URL("../config/product.json", import.meta.url), "utf8"),
);
export const REPORT_TEMPLATE_VERSION = PRODUCT_CONFIG.passportTemplateMarker;

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function passportTitle(destination, locale = "zh-CN") {
  const name = String(destination || "").trim();
  return locale === "en-US"
    ? `GO PASSPORT · ${(name || "MY TRIP").toLocaleUpperCase("en-US")}`
    : `Go passport · ${name || "我的旅程"}`;
}

export const KORNVIA_REPORT_CSS = `
:root{--yellow:${PRODUCT_CONFIG.brand.sunnyYellow};--canvas:#F6E8C8;--paper:#FBFAF7;--ink:#171717;
--blue:#62B2DF;--brown:#955025;--muted:#5B564D;--line:rgba(23,23,23,.18);--shadow:8px 8px 0 var(--ink)}
*{box-sizing:border-box}
html{background:var(--canvas)}
body{margin:0;color:var(--ink);background:var(--canvas);
font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif}
.shell{width:min(1180px,calc(100% - 28px));margin:auto;padding:24px 0 70px}
.ticket,.card,.retained,.journey-plan{border:5px solid var(--ink);border-radius:26px;background:var(--paper)}
.hero{position:relative;display:grid;grid-template-columns:minmax(0,1fr) 250px;min-height:380px;overflow:hidden;box-shadow:var(--shadow)}
.hero-main{position:relative;display:grid;align-content:space-between;min-width:0;padding:clamp(28px,4vw,48px);border-right:3px dashed var(--ink)}
.hero-main:after{content:"";position:absolute;right:0;bottom:0;left:0;height:5px;background:var(--yellow)}
.brand-row{display:flex;align-items:center;justify-content:space-between;gap:20px;font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;
font-size:12px;font-weight:850;letter-spacing:.09em}.wordmark{font-family:inherit;font-size:clamp(24px,3vw,38px);font-weight:950;letter-spacing:-.05em}
.hero-title{align-self:center;margin:25px 0}.hero-title h1{max-width:11ch}.passport-label{display:inline-block;margin:24px 0 0;padding-bottom:7px;
border-bottom:6px solid var(--yellow);font-size:clamp(25px,3.5vw,46px);font-weight:950;line-height:1.1}
.hero-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;width:min(72%,520px);padding-top:18px;border-top:2px solid var(--ink)}
.hero-meta span,.stub-label{display:block;margin-bottom:5px;color:var(--muted);font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;
font-size:10px;font-weight:850;letter-spacing:.09em}.hero-meta strong{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:clamp(17px,2.2vw,25px)}
.hero-stub{display:grid;align-content:space-between;padding:34px 26px 26px;border-top:10px solid var(--yellow);background:var(--paper)}
.stub-destination{margin:12px 0 30px;font-size:clamp(34px,4.6vw,58px);font-weight:950;letter-spacing:-.06em;line-height:1}
.stub-dates strong{font-size:18px;font-weight:850}.hero-summary{display:none}.stub-note{margin:26px 0 0;padding-top:18px;
border-top:1px solid var(--line);color:var(--muted);font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:10px;font-weight:750;letter-spacing:.06em;line-height:1.7}
.kicker{font-size:13px;font-weight:950;letter-spacing:.14em;margin:0 0 16px}
h1{font-size:clamp(52px,8vw,104px);line-height:.88;margin:0;letter-spacing:-.065em}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;margin-top:38px}
.card{overflow:hidden;display:flex;flex-direction:column;box-shadow:0 16px 40px rgba(23,23,23,.08)}
.photo{aspect-ratio:4/3;flex:0 0 auto;background:#202126;overflow:hidden;border-bottom:3px solid var(--ink)}
.photo img{width:100%;height:100%;display:block;object-fit:cover}
.body{padding:clamp(22px,4vw,38px);display:flex;flex:1;flex-direction:column;gap:18px}
.eyebrow{font-size:13px;font-weight:950;letter-spacing:.1em;color:var(--brown);margin:0}
h2{font-size:clamp(34px,5vw,58px);line-height:1.02;letter-spacing:-.04em;margin:0}
.facts{display:grid;gap:13px;margin:0}.facts div{display:grid;grid-template-columns:92px 1fr;gap:16px}
.facts dt{font-weight:900;color:var(--brown)}.facts dd{margin:0;line-height:1.55;font-weight:620}
.route{background:#fff;border-top:1px solid var(--line);border-bottom:1px solid var(--line);border-left:7px solid var(--yellow);padding:14px 16px}
.risk-list,.trip-risks{border:2px solid var(--ink);border-radius:18px;padding:16px;background:#FFF8E5;display:grid;gap:10px}
.verification{border:1px solid var(--line);border-radius:13px;background:#FFF8E5;font-size:13px}
.verification summary{display:flex;align-items:baseline;gap:9px;padding:10px 12px;list-style:none;cursor:pointer;font-weight:750;line-height:1.45}
.verification summary::-webkit-details-marker{display:none}
.verification summary:hover span:last-child{text-decoration:underline;text-underline-offset:3px}
.verification summary:focus-visible{outline:3px solid var(--blue);outline-offset:2px;border-radius:13px}
.status-dot{width:8px;height:8px;border-radius:50%;flex:0 0 8px;align-self:center;background:#AC7700}
.tone-positive .status-dot{background:#187044}.tone-urgent .status-dot{background:#B52836}
.verification-body{display:grid;gap:8px;border-top:1px solid var(--line);padding:10px 12px 12px}
.verification p,.verification ul,.verification div,.risk-list p,.trip-risks p{margin:0;line-height:1.5}.verification b{display:inline-block;margin-right:6px}
.verification ul{padding-left:20px}.audit-sources{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.audit-sources a,.risk-sources a{color:var(--ink);font-weight:850}
.verification-fallback{margin:0;color:var(--muted);font-size:12px;line-height:1.45}
.risk-list>div,.trip-risks>div{display:grid;gap:10px}.risk-item{border-top:1px solid rgba(20,20,20,.2);padding-top:10px;display:grid;gap:6px}.risk-item:first-child{border-top:0;padding-top:0}
.risk-item>div{display:flex;align-items:center;gap:8px}.risk-item span{font-size:11px;font-weight:900;color:var(--brown)}.risk-item p strong{margin-right:8px}
.trip-risks{margin-top:28px;background:#E7F5FD;border-width:5px;padding:22px 28px}.trip-risks>strong{font-size:22px}
.sources{display:flex;flex-wrap:wrap;gap:10px;margin-top:auto}
.button{display:inline-flex;align-items:center;justify-content:center;border:3px solid var(--ink);
border-radius:16px;padding:10px 15px;color:var(--ink);background:var(--paper);text-decoration:none;font-weight:850;box-shadow:4px 4px 0 var(--ink)}
.retained{padding:22px 28px;margin-top:28px;background:var(--paper);line-height:1.6;font-weight:750}
.manifest{display:grid;grid-template-columns:minmax(0,1.08fr) minmax(280px,.92fr);gap:clamp(28px,5vw,72px);margin:70px 0;
padding:26px 0 34px;border-top:3px solid var(--ink);border-bottom:1px solid var(--ink)}
.manifest h2{max-width:12ch;margin:0;font-size:clamp(38px,5.4vw,64px);line-height:1.08;letter-spacing:.015em;text-wrap:balance}
.manifest-copy>p:last-child{max-width:36rem;margin:18px 0 0;color:var(--muted);font-size:17px;line-height:1.7}
.area-route{display:grid;align-content:center;gap:16px;font-family:ui-monospace,"SFMono-Regular",Consolas,monospace}
.route-stop{display:grid;grid-template-columns:48px minmax(0,1fr) auto;gap:14px;align-items:center;padding-bottom:14px;border-bottom:1px solid var(--line)}
.stop-number{display:grid;width:44px;height:44px;place-items:center;border:2px solid var(--ink);background:var(--yellow);font-weight:950}
.route-name{font-weight:900}.route-count{color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.06em}
.journey-plan{margin:28px 0 42px;padding:clamp(24px,5vw,52px);box-shadow:var(--shadow);display:grid;gap:28px}
.journey-heading{padding-bottom:18px;border-bottom:2px solid var(--ink)}
.journey-heading h2{font-size:clamp(31px,4vw,52px);line-height:1.08;letter-spacing:.01em}
.day-nav{display:flex;gap:8px;flex-wrap:wrap}
.day-nav a{display:inline-flex;align-items:center;gap:5px;padding:7px 11px;border:1px solid var(--ink);border-radius:999px;background:#FFF7E2;color:var(--ink);text-decoration:none;font-size:13px;font-weight:700}
.day-nav a:hover{background:var(--yellow)}.day-nav a span{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-weight:900;white-space:nowrap}
.day-card header span{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:11px;font-weight:900;letter-spacing:.1em}
.day-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}.day-card{scroll-margin-top:16px;border:2px solid var(--ink);border-radius:20px;padding:22px;display:grid;gap:16px}
.day-card header{display:grid;gap:6px}.day-card h3{margin:0;font-size:25px;line-height:1.2}.day-summary{margin:0;color:var(--muted);line-height:1.6}
.day-card ol{display:grid;gap:14px;margin:0;padding-left:24px}.day-card li{padding-left:5px}.day-card li>div{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:baseline}
.day-card li span{color:var(--brown);font-size:12px;font-weight:900}.day-card li p{margin:5px 0 0;color:var(--muted);font-size:13px;line-height:1.5}
.connections{display:grid;gap:10px;padding-top:14px;border-top:1px solid var(--line)}.connections p{display:grid;gap:3px;margin:0;font-size:12px;line-height:1.5}
.connections span,.connections small{color:var(--muted)}.unassigned{padding:18px 20px;border:2px dashed var(--ink);border-radius:16px}.unassigned ul{margin:8px 0 0;padding-left:20px}
.route-boundary{margin:0;padding-top:18px;border-top:2px solid var(--ink);color:var(--muted);font-size:13px;line-height:1.6}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{
outline:4px solid var(--blue);outline-offset:3px}
.body{overflow-wrap:anywhere}
@media(max-width:860px){.hero{grid-template-columns:1fr}.hero-main{min-height:500px;border-right:0;border-bottom:3px dashed var(--ink)}
.hero-stub{grid-template-columns:1fr 1fr;gap:22px}.stub-note{grid-column:1/-1}.manifest{grid-template-columns:1fr}}
@media(max-width:760px){.shell{width:min(100% - 18px,1180px)}.grid{grid-template-columns:1fr}
.hero,.card,.retained{border-radius:20px}.hero-main{min-height:470px;padding:28px 22px}.hero-stub{grid-template-columns:1fr;padding:28px 22px}
.stub-note{grid-column:auto}.hero-meta{width:100%;gap:10px}.manifest{margin:54px 0;padding-bottom:26px}.manifest h2{font-size:40px;letter-spacing:.02em}
.route-stop{grid-template-columns:44px 1fr}.route-count{grid-column:2}.journey-plan{padding:22px}.facts div{grid-template-columns:76px 1fr}
}
@media(max-width:639px){
  .shell{padding:14px 0 34px}
  .hero{min-height:0;border-width:3px;box-shadow:5px 5px 0 var(--ink)}
  .hero-main{min-height:0;padding:21px 17px 23px;border:0;display:block}
  .hero-stub,.hero-main .brand-row,.hero-title .kicker,.hero-title .passport-label,.hero-main .hero-meta{display:none}
  .hero-title{margin:0}.hero-title h1{font-size:clamp(32px,11vw,48px);line-height:1.02}
  .hero-summary{display:block;margin:9px 0 0;color:var(--muted);font-size:clamp(11px,3.15vw,14px);line-height:1.4;font-weight:750}
  .journey-plan{margin:20px 0 24px;padding:15px;gap:13px;border-width:3px;border-radius:18px;box-shadow:5px 5px 0 var(--ink)}
  .journey-heading{padding-bottom:12px}.journey-heading .kicker{margin-bottom:6px;font-size:11px}.journey-heading h2{font-size:27px}
  .day-nav{gap:6px}.day-nav a{font-size:12px;padding:6px 8px}
  .day-grid{grid-template-columns:minmax(0,1fr);gap:10px}.day-card{padding:13px;gap:9px;border-radius:13px}
  .day-card h3{font-size:20px}.day-card ol{gap:9px}.day-card li p{margin-top:3px}
  .route-boundary{padding-top:10px;font-size:12px}
  .manifest{margin:24px 0 16px;gap:12px;padding:14px 0}
  .manifest .kicker{margin-bottom:7px;font-size:10px}.manifest h2{max-width:none;font-size:25px}
  .manifest-copy>p:last-child{margin-top:7px;font-size:13px;line-height:1.45}
  .area-route{gap:7px}.route-stop{padding-bottom:6px;grid-template-columns:26px minmax(0,1fr) auto;gap:8px}
  .stop-number{width:26px;height:26px;font-size:12px}.route-count{grid-column:auto}
  .grid{margin-top:14px;gap:13px}
  .card .body{padding:17px 16px;gap:10px}
  .card h2{font-size:27px;line-height:1.1}
  .card .eyebrow{font-size:11px}
  .card .facts{gap:6px;font-size:14px}
  .card .facts div{grid-template-columns:68px minmax(0,1fr);gap:9px}
  .card .facts dd{line-height:1.4}
  .card .body>p{margin:0}
  .card .button{padding:7px 11px;border-width:2px;border-radius:11px;box-shadow:3px 3px 0 var(--ink);font-size:13px}
}
@media print{.verification::details-content{content-visibility:visible;display:block}
html,body{background:#fff}.shell{width:100%;padding:0}.hero,.card,.retained{box-shadow:none;break-inside:avoid}.button{display:none}.grid{gap:14px}}
`;
