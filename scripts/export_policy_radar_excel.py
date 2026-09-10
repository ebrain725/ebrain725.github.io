#!/usr/bin/env python3
"""정책 레이더의 모든 탭을 하나의 XLSX 파일로 내보낸다(외부 패키지 없음)."""
from __future__ import annotations
import argparse, json, math, os, re, tempfile, urllib.parse, zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/'public/data/policies.json'; BILLS=ROOT/'public/data/bills.json'
SEMINARS=ROOT/'public/data/assembly_seminars.json'
OUTPUT=ROOT/'public/data/ets-policy-radar-events.xlsx'
MANIFEST=ROOT/'public/data/policy-radar-excel.json'
KST=timezone(timedelta(hours=9)); NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RNS='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PNS='http://schemas.openxmlformats.org/package/2006/relationships'
BAD=re.compile('[\x00-\x08\x0B\x0C\x0E-\x1F\uFFFE\uFFFF]'); DATE=re.compile(r'^(20\d{2})-(\d{2})-(\d{2})$')
TABS={'press':'기후부 보도자료','notice':'기후부 공지사항','motie_press':'산업부 보도자료','motie_notice':'산업부 공지사항','krx_notice':'한국거래소 공지사항','news':'뉴스'}
TAB_ORDER=list(TABS.values())
DATE_HEADERS={'게시일','기준일','시작일','종료일','행사 시작일','행사 종료일','자료 게시일','제안일','최근 처리일','게시·최초수집일'}

def text(v:Any,limit:int=32767)->str:
    if v is None:return ''
    if isinstance(v,list):v=', '.join(dict.fromkeys(text(x,500) for x in v if text(x,500)))
    elif isinstance(v,dict):v=json.dumps(v,ensure_ascii=False,separators=(', ',': '))
    return BAD.sub('',str(v)).replace('\r\n','\n').replace('\r','\n')[:limit]

def first(d:dict,*keys:str)->Any:
    for k in keys:
        v=d.get(k)
        if v not in (None,'',[],{}):return v
    return ''

def good_url(v:Any)->str:
    u=text(v,4000).strip()
    try:return u if re.match(r'^https?://',u,re.I) and urllib.parse.urlsplit(u).hostname else ''
    except ValueError:return ''

def load(path:Path)->dict:
    if not path.is_file():raise RuntimeError(f'필수 파일 없음: {path}')
    d=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(d,dict):raise RuntimeError(f'JSON 형식 오류: {path}')
    return d

def policy_tab(d:dict)->str:
    s=text(d.get('section')).lower()
    if s in TABS:return TABS[s]
    source=text(d.get('source')); u=text(d.get('url'))
    if text(d.get('sourceType')).lower()=='news':return '뉴스'
    if '한국거래소' in source or 'ets.krx.co.kr' in u:return '한국거래소 공지사항'
    if '산업부' in source or 'motir.go.kr' in u:return '산업부 보도자료' if '보도' in source else '산업부 공지사항'
    return '기후부 보도자료' if '보도자료' in source else '기후부 공지사항'

def policy_row(d:dict)->list[Any]:
    return [text(first(d,'publishedAt','date'))[:10],text(first(d,'category','primaryCategory')),text(first(d,'sourceBoardCategory','boardCategory','sourceBoard')),text(d.get('keywordScope')),text(d.get('title')),text(d.get('summary')),text(first(d,'matchedKeywords','keywords')),text(d.get('source')),int(d.get('duplicateCount') or 1),good_url(d.get('url')),text(first(d,'id','sourceId'))]

def schedule_row(d:dict)->list[Any]:
    return [text(first(d,'startDate','date'))[:10],text(first(d,'endDate','startDate','date'))[:10],text(first(d,'startTime','time')),text(d.get('status')),text(d.get('eventType')),text(d.get('title')),text(first(d,'organizer','host')),text(first(d,'location','venue')),text(first(d,'evidence','summary')),text(d.get('publishedAt'))[:10],text(d.get('source')),int(d.get('duplicateCount') or 1),good_url(d.get('url')),text(first(d,'id','sourceId'))]

def bill_row(d:dict)->list[Any]:
    result=' | '.join(x for x in [text(d.get('committeeResult')),text(d.get('lawResult')),text(d.get('plenaryResult'))] if x)
    return [text(d.get('billNo')),text(d.get('proposedDate'))[:10],text(d.get('lastActionDate'))[:10],text(d.get('status')),text(d.get('title')),text(d.get('proposer')),text(d.get('proposerKind')),text(d.get('committee')),text(d.get('primaryCategory')),text(d.get('relevanceLevel')),int(d.get('relevanceScore') or 0),text(d.get('relevanceReason')),text(d.get('summary')),result,good_url(d.get('url')),text(d.get('billId'))]

def seminar_row(d:dict)->list[Any]:
    return [text(d.get('startDate'))[:10],text(first(d,'endDate','startDate'))[:10],text(d.get('startTime')),text(d.get('status')),text(d.get('eventType')),text(d.get('title')),text(d.get('host')),text(d.get('venue')),text(d.get('publishedAt'))[:10],text(d.get('keywords')),text(d.get('relevance')),text(d.get('relevanceReason')),text(d.get('summary')),text(d.get('source')),good_url(d.get('url')),text(first(d,'id','sourceId'))]

def unified(tab:str,d:dict,kind:str)->list[Any]:
    if kind=='policy':return [tab,text(first(d,'publishedAt','date'))[:10],'','','','',text(first(d,'category','keywordScope')),text(d.get('title')),text(d.get('summary')),'',text(first(d,'sourceBoardCategory','boardCategory','sourceBoard')),text(first(d,'matchedKeywords','keywords')),text(d.get('source')),good_url(d.get('url')),text(first(d,'id','sourceId'))]
    if kind=='bill':return [tab,text(d.get('proposedDate'))[:10],text(d.get('proposedDate'))[:10],text(d.get('lastActionDate'))[:10],'',text(d.get('status')),text(d.get('primaryCategory')),text(d.get('title')),text(d.get('summary')),text(d.get('proposer')),text(d.get('committee')),text(d.get('topics')),'대한민국 국회',good_url(d.get('url')),text(d.get('billId'))]
    return [tab,text(d.get('publishedAt'))[:10],text(first(d,'startDate','date'))[:10],text(first(d,'endDate','startDate','date'))[:10],text(first(d,'startTime','time')),text(d.get('status')),text(d.get('eventType')),text(d.get('title')),text(first(d,'evidence','summary')),text(first(d,'organizer','host')),text(first(d,'location','venue')),text(first(d,'keywords','matchedKeywords')),text(d.get('source')),good_url(d.get('url')),text(first(d,'id','sourceId'))]

def col(n:int)->str:
    s='';n+=1
    while n:n,r=divmod(n-1,26);s=chr(65+r)+s
    return s

def serial(v:str)->int|None:
    m=DATE.match(v)
    if not m:return None
    try:return (date(*map(int,m.groups()))-date(1899,12,30)).days
    except ValueError:return None

def kind(header:str)->str:
    if 'URL' in header:return 'url'
    if header in DATE_HEADERS:return 'date'
    if '건수' in header or '점수' in header:return 'num'
    return 'text'

def style(k:str,alt:bool)->int:
    return {'text':3 if alt else 2,'date':5 if alt else 4,'num':7 if alt else 6,'url':9 if alt else 8}[k]

def cell(ref:str,v:Any,k:str,st:int)->str:
    if v in (None,''):return f'<c r="{ref}" s="{st}"/>'
    if k=='date':
        x=serial(text(v)[:10])
        if x is not None:return f'<c r="{ref}" s="{st}" t="n"><v>{x}</v></c>'
    if k=='num' and isinstance(v,(int,float)) and math.isfinite(float(v)):return f'<c r="{ref}" s="{st}" t="n"><v>{float(v):.15g}</v></c>'
    x=text(v);space=' xml:space="preserve"' if '\n' in x or x[:1].isspace() or x[-1:].isspace() else ''
    return f'<c r="{ref}" s="{st}" t="inlineStr"><is><t{space}>{escape(x)}</t></is></c>'

def sheet_xml(headers:list[str],rows:list[list[Any]],widths:list[float])->tuple[str,str|None]:
    types=[kind(h) for h in headers]; rels=[]; links=[]
    out=['<row r="1" ht="24" customHeight="1">'+''.join(cell(f'{col(i)}1',h,'text',1) for i,h in enumerate(headers))+'</row>']
    for r,values in enumerate(rows,2):
        alt=r%2==1; cells=[]
        for c,h in enumerate(headers):
            v=values[c] if c<len(values) else ''; k=types[c]; ref=f'{col(c)}{r}'
            cells.append(cell(ref,v,k,style(k,alt)))
            if k=='url' and good_url(v):
                rid=f'rId{len(rels)+1}';rels.append((rid,good_url(v)));links.append((ref,rid))
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    last=f'{col(len(headers)-1)}{max(1,len(rows)+1)}'
    cols=''.join(f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>' for i,w in enumerate(widths))
    hyper='<hyperlinks>'+''.join(f'<hyperlink ref="{ref}" r:id="{rid}"/>' for ref,rid in links)+'</hyperlinks>' if links else ''
    xml=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{NS}" xmlns:r="{RNS}"><dimension ref="A1:{last}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/><cols>{cols}</cols><sheetData>{''.join(out)}</sheetData><autoFilter ref="A1:{last}"/>{hyper}<pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>'''
    rel=None
    if rels:rel=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{PNS}">{''.join(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target={quoteattr(u)} TargetMode="External"/>' for rid,u in rels)}</Relationships>'''
    return xml,rel

STYLES=f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts><fonts count="3"><font><sz val="10"/><name val="맑은 고딕"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="맑은 고딕"/></font><font><color rgb="FF0563C1"/><u/><sz val="10"/><name val="맑은 고딕"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F5D50"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF3F7F5"/></patternFill></fill></fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFD9E2DE"/></left><right style="thin"><color rgb="FFD9E2DE"/></right><top style="thin"><color rgb="FFD9E2DE"/></top><bottom style="thin"><color rgb="FFD9E2DE"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="10"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf><xf numFmtId="164" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf><xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/><xf numFmtId="3" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles><dxfs count="0"/></styleSheet>'''

def write_book(policy:dict,bills:dict,seminars:dict,out:Path,manifest:Path)->dict:
    grouped={t:[] for t in TAB_ORDER}
    for d in policy.get('items',[]):
        if isinstance(d,dict):grouped[policy_tab(d)].append(d)
    for v in grouped.values():v.sort(key=lambda d:(text(first(d,'publishedAt','date')),text(d.get('title'))),reverse=True)
    schedules=[d for d in policy.get('institutionSchedules',[]) if isinstance(d,dict)]
    schedules.sort(key=lambda d:(text(first(d,'startDate','date')),text(d.get('startTime')),text(d.get('title'))),reverse=True)
    bill_items=[d for d in bills.get('items',[]) if isinstance(d,dict)];bill_items.sort(key=lambda d:(text(d.get('proposedDate')),text(d.get('title'))),reverse=True)
    seminar_items=[d for d in seminars.get('items',[]) if isinstance(d,dict)];seminar_items.sort(key=lambda d:(text(d.get('startDate')),text(d.get('startTime')),text(d.get('title'))),reverse=True)
    ph=['게시일','분류','세부 게시판','직접·연관','제목','요약','일치 키워드','출처','중복 건수','원문 URL','ID']
    sh=['행사 시작일','행사 종료일','시간','상태','행사 유형','제목','주최기관','장소','근거·요약','자료 게시일','출처','중복 건수','원문 URL','ID']
    bh=['의안번호','제안일','최근 처리일','현재 단계','제목','발의자','발의자 구분','소관위원회','주요 분류','관련도','관련도 점수','관련 사유','제안이유·주요내용','처리결과','원문 URL','의안 ID']
    mh=['행사 시작일','행사 종료일','시간','상태','행사 유형','제목','주최','장소','게시·최초수집일','검색 키워드','관련도','관련 사유','요약','출처','원문 URL','ID']
    uh=['탭','기준일','시작일','종료일','시간','상태','분류','제목','요약·근거','기관·주최·발의자','소관·장소','키워드','출처','원문 URL','ID']
    sheets=[]; counts={}; allrows=[]
    for tab in TAB_ORDER:
        rows=[policy_row(d) for d in grouped[tab]];counts[tab]=len(rows);sheets.append((tab,ph,rows,[12,16,18,12,42,85,34,24,12,52,24]));allrows += [unified(tab,d,'policy') for d in grouped[tab]]
    counts['기관일정']=len(schedules);sheets.append(('기관일정',sh,[schedule_row(d) for d in schedules],[12,12,10,12,14,42,28,28,85,12,24,12,52,24]));allrows += [unified('기관일정',d,'schedule') for d in schedules]
    counts['발의법률안']=len(bill_items);sheets.append(('발의법률안',bh,[bill_row(d) for d in bill_items],[14,12,12,12,48,26,14,24,18,12,12,32,90,28,52,28]));allrows += [unified('발의법률안',d,'bill') for d in bill_items]
    counts['국회의원 세미나 일정']=len(seminar_items);sheets.append(('국회 세미나',mh,[seminar_row(d) for d in seminar_items],[12,12,10,12,14,46,32,28,14,30,12,34,70,26,52,26]));allrows += [unified('국회의원 세미나 일정',d,'schedule') for d in seminar_items]
    allrows.sort(key=lambda r:(text(r[2] or r[1]),text(r[4]),text(r[7])),reverse=True);counts['전체 이벤트']=len(allrows)
    generated=datetime.now(KST).isoformat(timespec='seconds')
    info=[['파일명',out.name],['생성시각(KST)',generated],['설명','정책 레이더의 모든 탭을 시트별로 정리한 엑셀 파일'],['전체 이벤트 건수',len(allrows)]]+[[f'{k} 건수',v] for k,v in counts.items() if k!='전체 이벤트']
    specs=[('안내',['항목','내용'],info,[28,90]),('전체 이벤트',uh,allrows,[22,12,12,12,10,12,18,46,85,30,30,34,24,52,28])]+sheets
    names=[s[0][:31] for s in specs]
    wb='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>'%(NS,RNS,''.join(f'<sheet name={quoteattr(n)} sheetId="{i}" r:id="rId{i}"/>' for i,n in enumerate(names,1)))
    wbr='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="%s">%s<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'%(PNS,''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(specs)+1)),len(specs)+1)
    ct='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>%s</Types>'%''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(specs)+1))
    rr='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="%s"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'%PNS
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False,dir=out.parent,suffix='.xlsx') as f:tmp=Path(f.name)
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            z.writestr('[Content_Types].xml',ct);z.writestr('_rels/.rels',rr);z.writestr('xl/workbook.xml',wb);z.writestr('xl/_rels/workbook.xml.rels',wbr);z.writestr('xl/styles.xml',STYLES)
            for i,(_,h,r,w) in enumerate(specs,1):
                sx,sr=sheet_xml(h,r,w);z.writestr(f'xl/worksheets/sheet{i}.xml',sx)
                if sr:z.writestr(f'xl/worksheets/_rels/sheet{i}.xml.rels',sr)
        os.replace(tmp,out);out.chmod(0o644)
    finally:tmp.unlink(missing_ok=True)
    with zipfile.ZipFile(out) as z:
        if z.testzip():raise RuntimeError('XLSX ZIP 무결성 오류')
    result={'schemaVersion':'1.0','generatedAt':generated,'file':f'data/{out.name}','fileName':out.name,'fileBytes':out.stat().st_size,'sheetCount':len(specs),'totalEventCount':len(allrows),'counts':counts,'sourceLastSync':{'정책자료':text(policy.get('lastSync')),'발의법률안':text(bills.get('lastSync')),'국회 세미나':text(seminars.get('lastSync'))}}
    manifest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return result

def self_test():
    p={'items':[{'section':'press','publishedAt':'2026-09-09','title':'보도','source':'기후부','url':'https://example.com'}],'institutionSchedules':[{'startDate':'2026-09-10','title':'일정','url':'https://example.com'}]};b={'items':[{'billId':'b','billNo':'1','proposedDate':'2026-01-01','title':'법안','url':'https://example.com'}]};s={'items':[{'id':'s','startDate':'2026-09-11','title':'세미나','url':'https://example.com'}]}
    with tempfile.TemporaryDirectory() as d:
        x=Path(d)/'a.xlsx';m=Path(d)/'a.json';r=write_book(p,b,s,x,m);assert r['sheetCount']==11 and r['totalEventCount']==4 and x.stat().st_size>10000
    print('POLICY_RADAR_EXCEL_SELF_TEST=PASS')

def main()->int:
    a=argparse.ArgumentParser();a.add_argument('--policy-path',type=Path,default=POLICY);a.add_argument('--bill-path',type=Path,default=BILLS);a.add_argument('--seminar-path',type=Path,default=SEMINARS);a.add_argument('--output',type=Path,default=OUTPUT);a.add_argument('--manifest',type=Path,default=MANIFEST);a.add_argument('--self-test',action='store_true');x=a.parse_args()
    if x.self_test:self_test();return 0
    r=write_book(load(x.policy_path),load(x.bill_path),load(x.seminar_path),x.output,x.manifest);print('POLICY_RADAR_EXCEL_RESULT='+json.dumps(r,ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
