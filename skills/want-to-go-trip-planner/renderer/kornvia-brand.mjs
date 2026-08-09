import fs from "node:fs";

export const PRODUCT_CONFIG = JSON.parse(
  fs.readFileSync(new URL("../config/product.json", import.meta.url), "utf8"),
);
export const REPORT_TEMPLATE_VERSION = PRODUCT_CONFIG.passportTemplateMarker;

const lim = fs.readFileSync(new URL("./assets/lim-persona.png", import.meta.url));
export const LIM_PERSONA_DATA_URL =
  `data:image/png;base64,${lim.toString("base64")}`;

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
:root{--yellow:${PRODUCT_CONFIG.brand.sunnyYellow};--cream:${PRODUCT_CONFIG.brand.sunnyYellow};--paper:#FFFDF8;--ink:#141414;
--blue:#62B2DF;--red:#E43D24;--brown:#955025;--muted:#6C665D}
*{box-sizing:border-box}
html{background:var(--cream)}
body{margin:0;color:var(--ink);background:var(--cream);
font-family:ui-rounded,"SF Pro Rounded","PingFang SC","Microsoft YaHei",sans-serif}
.shell{width:min(1180px,calc(100% - 28px));margin:auto;padding:24px 0 70px}
.hero,.card,.retained,.upgrade{border:5px solid var(--ink);border-radius:40px;background:var(--paper)}
.hero{position:relative;overflow:hidden;background:#F2B51D;padding:clamp(28px,6vw,66px);
padding-right:clamp(100px,20vw,220px);box-shadow:12px 12px 0 var(--ink)}
.kicker{font-size:13px;font-weight:950;letter-spacing:.14em;margin:0 0 16px}
h1{font-size:clamp(44px,8vw,92px);line-height:.92;margin:0;letter-spacing:-.055em}
.hero p{font-size:clamp(16px,2.3vw,24px);font-weight:760;line-height:1.55;max-width:700px}
.lim{position:absolute;right:20px;bottom:-10px;width:clamp(82px,16vw,178px);height:auto}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;margin-top:38px}
.card{overflow:hidden;display:flex;flex-direction:column}
.photo{aspect-ratio:4/3;flex:0 0 auto;background:#202126;overflow:hidden;border-bottom:5px solid var(--ink)}
.photo img{width:100%;height:100%;display:block;object-fit:cover}
.body{padding:clamp(22px,4vw,38px);display:flex;flex:1;flex-direction:column;gap:18px}
.eyebrow{font-size:13px;font-weight:950;letter-spacing:.1em;color:var(--brown);margin:0}
h2{font-size:clamp(34px,5vw,58px);line-height:1.02;letter-spacing:-.04em;margin:0}
.facts{display:grid;gap:13px;margin:0}.facts div{display:grid;grid-template-columns:92px 1fr;gap:16px}
.facts dt{font-weight:900;color:var(--brown)}.facts dd{margin:0;line-height:1.55;font-weight:620}
.route{background:#E7F5FD;border-left:8px solid var(--blue);padding:14px 16px;border-radius:0 18px 18px 0}
.sources{display:flex;flex-wrap:wrap;gap:10px;margin-top:auto}
.button{display:inline-flex;align-items:center;justify-content:center;border:3px solid var(--ink);
border-radius:999px;padding:10px 15px;color:var(--ink);background:#fff;text-decoration:none;font-weight:850}
.retained{padding:22px 28px;margin-top:28px;background:#FFF6DC;line-height:1.6;font-weight:750}
.upgrade{display:grid;grid-template-columns:minmax(0,.9fr) minmax(320px,1.1fr);gap:clamp(24px,5vw,64px);
align-items:stretch;margin-top:28px;padding:clamp(26px,5vw,54px);background:#F2B51D;color:var(--ink);
box-shadow:10px 10px 0 var(--ink)}
.upgrade-copy{display:grid;align-content:start;gap:18px;background:var(--paper);color:var(--ink);
border:5px solid var(--ink);border-radius:28px;padding:clamp(20px,4vw,32px);box-shadow:10px 12px 0 var(--ink)}
.upgrade .kicker{color:var(--yellow);margin:0}
.upgrade h2{font-size:clamp(36px,5vw,62px);max-width:760px}.upgrade-copy>p{font-size:18px;line-height:1.65;margin:0}
.upgrade-price{display:flex;align-items:baseline;gap:10px}.upgrade-price strong{color:var(--yellow);
font-size:clamp(54px,8vw,86px);line-height:.9;letter-spacing:-.06em}.upgrade-price span{font-weight:850}
.upgrade-benefits{display:grid;gap:10px;margin:0;padding:0;list-style:none}
.upgrade-benefits li{border:2px solid var(--ink);border-radius:16px;background:#fff;color:var(--ink);
padding:12px 14px;font-weight:750}
.manual-offer{border-top:3px solid var(--ink);padding-top:18px;display:grid;gap:10px}
.manual-offer p{margin:0;line-height:1.55}.manual-price{font-size:22px;font-weight:950}
.request-form{display:grid;gap:16px;background:var(--paper);color:var(--ink);border:5px solid var(--ink);
border-radius:28px;padding:clamp(20px,4vw,32px);box-shadow:10px 12px 0 var(--ink)}
.request-form h3{font-size:26px;line-height:1.2;margin:0}.request-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.request-form label{display:grid;gap:7px;font-size:14px;font-weight:900}.request-form .full{grid-column:1/-1}
.request-form input,.request-form select,.request-form textarea{width:100%;border:2px solid var(--ink);border-radius:13px;
background:#fff;color:var(--ink);font:inherit;padding:12px 13px}.request-form textarea{min-height:108px;resize:vertical}
.request-form .submit{border:3px solid var(--ink);border-radius:999px;background:#fff;color:#000;min-height:48px;
font:inherit;font-size:17px;font-weight:950;padding:13px 18px;cursor:pointer;box-shadow:4px 4px 0 var(--ink)}
.request-form .submit:active{transform:translate(2px,2px);box-shadow:2px 2px 0 var(--ink)}
.fine-print,.fallback{font-size:13px;line-height:1.6;margin:0;color:#4f493f}.fallback a{color:var(--ink);font-weight:900}
.honeypot{position:absolute!important;left:-10000px!important;width:1px!important;height:1px!important;overflow:hidden!important}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{
outline:4px solid var(--blue);outline-offset:3px}
.body,.request-form,.upgrade-copy{overflow-wrap:anywhere}
@media(max-width:860px){.upgrade{grid-template-columns:1fr}}
@media(max-width:760px){.shell{width:min(100% - 18px,1180px)}.grid{grid-template-columns:1fr}
.hero{padding-right:28px;padding-bottom:130px}.upgrade{padding:22px}.hero,.card,.retained,.upgrade{border-radius:28px}
.lim{width:105px}.facts div{grid-template-columns:76px 1fr}.request-grid{grid-template-columns:1fr}
.request-form .full{grid-column:auto}}
@media print{html,body{background:#fff}.shell{width:100%;padding:0}.hero,.card,.retained,.upgrade{
box-shadow:none;break-inside:avoid}.button{display:none}.grid{gap:14px}}
`;
