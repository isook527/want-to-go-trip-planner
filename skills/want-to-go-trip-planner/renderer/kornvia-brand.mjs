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
.ticket,.card,.retained,.upgrade{border:5px solid var(--ink);border-radius:26px;background:var(--paper)}
.hero{position:relative;display:grid;grid-template-columns:minmax(0,1fr) 250px;min-height:520px;overflow:hidden;box-shadow:var(--shadow)}
.hero-main{position:relative;display:grid;align-content:space-between;min-width:0;padding:clamp(30px,5vw,58px);border-right:3px dashed var(--ink)}
.hero-main:after{content:"";position:absolute;right:0;bottom:0;left:0;height:5px;background:var(--yellow)}
.brand-row{display:flex;align-items:center;justify-content:space-between;gap:20px;font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;
font-size:12px;font-weight:850;letter-spacing:.09em}.wordmark{font-family:inherit;font-size:clamp(24px,3vw,38px);font-weight:950;letter-spacing:-.05em}
.hero-title{align-self:center;margin:34px 0}.hero-title h1{max-width:11ch}.passport-label{display:inline-block;margin:24px 0 0;padding-bottom:7px;
border-bottom:6px solid var(--yellow);font-size:clamp(25px,3.5vw,46px);font-weight:950;line-height:1.1}
.hero-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;width:min(72%,520px);padding-top:18px;border-top:2px solid var(--ink)}
.hero-meta span,.stub-label{display:block;margin-bottom:5px;color:var(--muted);font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;
font-size:10px;font-weight:850;letter-spacing:.09em}.hero-meta strong{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:clamp(17px,2.2vw,25px)}
.hero-stub{display:grid;align-content:space-between;padding:34px 26px 26px;border-top:10px solid var(--yellow);background:var(--paper)}
.stub-destination{margin:12px 0 30px;font-size:clamp(34px,4.6vw,58px);font-weight:950;letter-spacing:-.06em;line-height:1}
.stub-facts{display:grid;gap:22px}.stub-facts div{padding-bottom:15px;border-bottom:1px solid var(--ink)}
.stub-facts strong{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:20px}.stub-note{margin:26px 0 0;padding-top:18px;
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
.verification,.risk-list,.trip-risks{border:2px solid var(--ink);border-radius:18px;padding:16px;background:#FFF8E5;display:grid;gap:10px}
.verification-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.verification-head span{border-radius:999px;background:var(--yellow);padding:6px 10px;font-size:12px;font-weight:900}
.verification p,.verification ul,.verification div,.risk-list p,.trip-risks p{margin:0;line-height:1.5}.verification b{display:inline-block;margin-right:8px}
.verification ul{padding-left:20px}.audit-sources{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.audit-sources a,.risk-sources a{color:var(--ink);font-weight:850}
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
.upgrade{display:grid;grid-template-columns:minmax(0,.9fr) minmax(320px,1.1fr);gap:clamp(24px,5vw,64px);
align-items:stretch;margin-top:28px;padding:clamp(26px,5vw,54px);background:var(--paper);color:var(--ink);box-shadow:var(--shadow)}
.upgrade-copy{display:grid;align-content:start;gap:18px;background:var(--paper);color:var(--ink);
border:0;border-radius:20px;padding:clamp(10px,2vw,20px)}
.upgrade .kicker{color:var(--muted);margin:0}
.upgrade h2{max-width:760px;font-size:clamp(32px,4vw,54px);line-height:1.08;letter-spacing:.01em}.upgrade-copy>p{font-size:18px;line-height:1.65;margin:0}
.tier-list{display:grid;gap:14px}.tier-card{border:2px solid var(--ink);border-radius:20px;padding:20px;display:grid;gap:10px}
.tier-card p{margin:0;line-height:1.55}.tier-name{font-size:20px;font-weight:950}.tier-price{font-size:clamp(38px,6vw,58px);
font-weight:950;line-height:1;letter-spacing:-.04em}.tier-card ul{display:grid;gap:7px;margin:0;padding-left:20px}
.recommended-tier{position:relative;background:var(--paper)}.recommended-tier:before{content:"";position:absolute;top:0;right:18px;left:18px;height:5px;background:var(--yellow)}
.recommended-label{font-size:12px;font-weight:950;letter-spacing:.12em}.recommended-tier .tier-price{width:max-content;border-bottom:7px solid var(--yellow)}
.availability{font-weight:900}.service-boundary{font-size:14px;color:#3f392f}
.request-form{display:grid;gap:16px;background:var(--paper);color:var(--ink);border:2px solid var(--ink);
border-radius:20px;padding:clamp(20px,4vw,32px);box-shadow:0 16px 40px rgba(23,23,23,.12)}
.request-form h3{font-size:26px;line-height:1.2;margin:0}.request-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.request-form label{display:grid;gap:7px;font-size:14px;font-weight:900}.request-form .full{grid-column:1/-1}
.request-form input,.request-form select,.request-form textarea{width:100%;border:2px solid var(--ink);border-radius:13px;
background:#fff;color:var(--ink);font:inherit;padding:12px 13px}.request-form textarea{min-height:108px;resize:vertical}
.request-form .submit{border:3px solid var(--ink);border-radius:16px;background:var(--paper);color:#000;min-height:48px;
font:inherit;font-size:17px;font-weight:950;padding:13px 18px;cursor:pointer;box-shadow:4px 4px 0 var(--ink)}
.request-form .submit:active{transform:translate(2px,2px);box-shadow:2px 2px 0 var(--ink)}
.request-notice{display:grid;gap:6px;border-top:2px solid var(--ink);padding-top:14px}.request-notice p,.payment-flow,.fallback{
font-size:13px;line-height:1.6;margin:0;color:#4f493f}.payment-flow{font-weight:850;color:var(--ink)}
.fallback a{color:var(--ink);font-weight:900}
.honeypot{position:absolute!important;left:-10000px!important;width:1px!important;height:1px!important;overflow:hidden!important}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{
outline:4px solid var(--blue);outline-offset:3px}
.body,.request-form,.upgrade-copy{overflow-wrap:anywhere}
@media(max-width:860px){.hero{grid-template-columns:1fr}.hero-main{min-height:500px;border-right:0;border-bottom:3px dashed var(--ink)}
.hero-stub{grid-template-columns:1fr 1fr;gap:22px}.stub-note{grid-column:1/-1}.manifest,.upgrade{grid-template-columns:1fr}}
@media(max-width:760px){.shell{width:min(100% - 18px,1180px)}.grid{grid-template-columns:1fr}
.hero,.card,.retained,.upgrade{border-radius:20px}.hero-main{min-height:470px;padding:28px 22px}.hero-stub{grid-template-columns:1fr;padding:28px 22px}
.stub-note{grid-column:auto}.hero-meta{width:100%;gap:10px}.manifest{margin:54px 0;padding-bottom:26px}.manifest h2{font-size:40px;letter-spacing:.02em}
.route-stop{grid-template-columns:44px 1fr}.route-count{grid-column:2}.upgrade{padding:22px}.facts div{grid-template-columns:76px 1fr}.request-grid{grid-template-columns:1fr}
.request-form .full{grid-column:auto}}
@media print{html,body{background:#fff}.shell{width:100%;padding:0}.hero,.card,.retained,.upgrade{
box-shadow:none;break-inside:avoid}.button{display:none}.grid{gap:14px}}
`;
