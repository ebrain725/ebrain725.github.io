"use strict";
const $ = (id) => document.getElementById(id); let parsedPrices = []; let briefingItems = [];
const fmt = new Intl.NumberFormat("ko-KR");

document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-tab]").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === button.dataset.tab));
}));

function csvLine(line, delimiter) { const out=[]; let cell="", quoted=false; for(let i=0;i<line.length;i+=1){const c=line[i]; if(c==='"'&&quoted&&line[i+1]==='"'){cell+='"';i+=1}else if(c==='"')quoted=!quoted;else if(c===delimiter&&!quoted){out.push(cell.trim());cell=""}else cell+=c} out.push(cell.trim());return out; }
function normalized(value){return String(value||"").toLowerCase().replace(/[\s_()（）%·/.-]/g,"")}
function toDate(value){const text=String(value||"").trim().replace(/[./]/g,"-"); if(/^\d{8}$/.test(text))return `${text.slice(0,4)}-${text.slice(4,6)}-${text.slice(6,8)}`; const match=text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/); return match?`${match[1]}-${match[2].padStart(2,"0")}-${match[3].padStart(2,"0")}`:""}
function toNum(value){const text=String(value||"").replace(/,/g,"").trim(); const negative=/하락|감소|▼/.test(text); const result=Number(text.replace(/[^0-9.+-]/g,""))||0; return negative?-Math.abs(result):result}
function detectRows(text){const lines=text.replace(/^\uFEFF/,"").split(/\r?\n/).filter((line)=>line.trim()); if(!lines.length)return[]; if(lines.some((line)=>line.includes("\t")))return lines.map((line)=>line.split("\t").map((v)=>v.trim())); if(lines.some((line)=>/\s{2,}/.test(line)))return lines.map((line)=>line.trim().split(/\s{2,}/)); return lines.map((line)=>csvLine(line,","));}

function parsePricePaste(){
  const rows=detectRows($("pricePaste").value); if(!rows.length)throw new Error("붙여넣은 내용이 없습니다.");
  const aliases={date:["date","tradedate","거래일","기준일","기준일자","일자"],symbol:["symbol","종목","종목명"],close:["close","현재가","종가"],change:["change","대비"],changeRate:["changerate","등락률","등락률%"],open:["open","시가"],high:["high","고가"],low:["low","저가"],volume:["volume","거래량","거래량톤"],tradeValue:["tradevalue","거래대금","거래대금원"]};
  const first=rows[0].map(normalized); const hasHeader=Object.values(aliases).flat().some((name)=>first.includes(normalized(name)));
  const headers=hasHeader?first:["date","symbol","close","change","changerate","open","high","low","volume","tradevalue"];
  const index=(field)=>headers.findIndex((header)=>aliases[field].map(normalized).includes(header)); const data=hasHeader?rows.slice(1):rows;
  const result=data.map((row,lineIndex)=>{const get=(field)=>index(field)>=0?row[index(field)]:""; const date=toDate(get("date")); if(!date)throw new Error(`${lineIndex+(hasHeader?2:1)}행의 거래일을 확인하세요.`); const close=toNum(get("close")); if(!close)throw new Error(`${lineIndex+(hasHeader?2:1)}행의 현재가/종가를 확인하세요.`); return{date,symbol:String(get("symbol")||"KAU25").trim(),close,change:toNum(get("change")),change_rate:toNum(get("changeRate")),open:toNum(get("open")),high:toNum(get("high")),low:toNum(get("low")),volume:toNum(get("volume")),trade_value:toNum(get("tradeValue"))};});
  const unique=new Map(result.map((row)=>[`${row.date}|${row.symbol}`,row])); return [...unique.values()].sort((a,b)=>a.date.localeCompare(b.date));
}

function setResult(id,text,type=""){const node=$(id);node.textContent=text;node.className=`result ${type}`.trim()}
function download(name,content,type){const blob=new Blob([content],{type});const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),500)}
function previewPrices(rows){const body=document.querySelector("#pricePreview tbody");body.replaceChildren();rows.slice(0,10).forEach((row)=>{const tr=document.createElement("tr");[row.date,row.symbol,fmt.format(row.close),`${row.change_rate.toFixed(2)}%`,fmt.format(row.volume),fmt.format(row.trade_value)].forEach((value)=>{const td=document.createElement("td");td.textContent=value;tr.append(td)});body.append(tr)});$("pricePreview").hidden=false}

$("parsePrices").addEventListener("click",()=>{try{parsedPrices=parsePricePaste();previewPrices(parsedPrices);$("downloadPrices").disabled=false;setResult("priceResult",`${parsedPrices.length.toLocaleString("ko-KR")}개 거래일을 확인했습니다. 중복 날짜·종목은 마지막 값으로 정리했습니다.`,"success")}catch(error){parsedPrices=[];$("downloadPrices").disabled=true;$("pricePreview").hidden=true;setResult("priceResult",error.message,"error")}});
$("downloadPrices").addEventListener("click",()=>{const header="date,symbol,close,change,change_rate,open,high,low,volume,trade_value";const lines=parsedPrices.map((row)=>[row.date,row.symbol,row.close,row.change,row.change_rate,row.open,row.high,row.low,row.volume,row.trade_value].join(","));download("prices.csv",`\uFEFF${[header,...lines].join("\n")}\n`,"text/csv;charset=utf-8")});

function keywords(){return [...new Set($("keywordText").value.split(/[,\n]/).map((value)=>value.trim()).filter(Boolean))]}
$("keywordText").addEventListener("input",()=>setResult("keywordResult",`현재 ${keywords().length}개 키워드가 입력되어 있습니다.`));
$("downloadSettings").addEventListener("click",()=>{const words=keywords();if(!words.length){setResult("keywordResult","키워드를 한 개 이상 입력하세요.","error");return}const sources=[];if($("sourcePress").checked)sources.push({name:"기후부 보도자료",url:"https://www.mcee.go.kr/home/web/board/rss.do?menuId=286&boardMasterId=1"});if($("sourceNotice").checked)sources.push({name:"기후부 공지·공고",url:"https://www.mcee.go.kr/home/web/board/rss.do?menuId=290&boardMasterId=39"});const news=$("sourceNews").checked?[{name:"배출권시장 뉴스",query:words.map((word)=>`"${word.replaceAll('"',"")}"`).join(" OR ")}]:[];if(!sources.length&&!news.length){setResult("keywordResult","수집할 자료를 한 개 이상 선택하세요.","error");return}download("settings.json",`${JSON.stringify({policyKeywords:words,policySources:sources,policyNewsSearches:news,policyLookbackDays:30,maxNewsItems:24,maxPolicyItems:60},null,2)}\n`,"application/json");setResult("keywordResult",`${words.length}개 키워드가 제목·본문 검색에 적용된 settings.json을 만들었습니다.`,"success")});

$("briefingDateInput").value=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
$("existingBriefing").addEventListener("change",async(event)=>{const file=event.target.files[0];if(!file)return;try{const data=JSON.parse(await file.text());briefingItems=Array.isArray(data)?data:(data.items||[]);setResult("briefingResult",`기존 브리핑 ${briefingItems.length}건을 불러왔습니다.`,"success")}catch{briefingItems=[];setResult("briefingResult","JSON 파일 형식을 확인하세요.","error")}});
$("downloadBriefing").addEventListener("click",()=>{const date=$("briefingDateInput").value,title=$("briefingTitle").value.trim(),content=$("briefingContent").value.trim();if(!date||!title||!content){setResult("briefingResult","날짜, 제목, 상세 브리핑은 필수입니다.","error");return}const item={date,title,summary:$("briefingSummary").value.trim(),content,marketTone:$("marketTone").value,outlook:$("briefingOutlook").value.trim(),source:"Telegram"};const updated=[item,...briefingItems.filter((old)=>String(old.date||old.briefingDate)!==date)].sort((a,b)=>String(b.date||b.briefingDate).localeCompare(String(a.date||a.briefingDate))).slice(0,60);download("briefing.json",`${JSON.stringify({updatedAt:new Date().toISOString(),items:updated},null,2)}\n`,"application/json");setResult("briefingResult",`${date} 브리핑을 포함한 briefing.json을 만들었습니다.`,"success")});
