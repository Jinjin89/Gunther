import { useCallback, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Artifact } from '@gunther/contracts';
import { SlideFace } from './src/outputs/SlideFace';
import { SlidesView } from './src/outputs/SlidesView';
import { PrintSlides } from './src/outputs/PrintSlides';
import { parseSlides } from './src/outputs/sections';
import cases from './.graphics-composer-qa-fixtures.json';
import './src/styles.css';
import './src/design/tokens.css';
import './src/design/primitives.css';
import './src/design/prose.css';
import './src/outputs/outputs.css';
import './src/outputs/slides/slides.css';

function Case({sample,index,theme,accent,onCite}:{sample:typeof cases[number];index:number;theme:string;accent:string;onCite:(n:number)=>void}){
 const slide=useMemo(()=>parseSlides(sample.content)[0]!,[sample.content]);
 const [metric,setMetric]=useState({fit:1,overflow:false}),[issue,setIssue]=useState<string|null>(null);
 const measure=useCallback((fit:number,overflow:boolean)=>setMetric(old=>old.fit===fit&&old.overflow===overflow?old:{fit,overflow}),[]);
 const receiveIssue=useCallback((s:string|null)=>setIssue(s),[]);
 return <article data-case={index}><div className="qa-label"><span>{sample.label}</span><output>{Math.round(metric.fit*100)}%{metric.overflow?' · overflow':''}</output></div><div className="qa-stage"><SlideFace {...slide} title={false} theme={theme} accent={accent} numbers={[1,2,3,4,5,6,7,8]} sources={Array.from({length:8},(_,i)=>`合成来源 ${i+1}`)} onCite={onCite} onFitChange={measure} onSceneIssue={receiveIssue}/></div>{issue&&<p className="qa-issue" role="status">{issue}</p>}</article>;
}
function App(){
 const [theme,setTheme]=useState('briefing'),[accent,setAccent]=useState('teal'),[view,setView]=useState('model'),[citation,setCitation]=useState<number|null>(null);
 const selected=view==='overview'?cases.slice(0,2):view==='model'?cases.filter(s=>s.label.startsWith('真实模型')):cases;
 const modeled=cases.filter(s=>s.label.startsWith('真实模型'));
 const deckCases=modeled.length===2?[...modeled,cases[2]!]:cases.slice(0,3);
 const content=`<!-- layout: title; theme: ${theme}; accent: ${accent} -->\n# 图形可以与说明共同组织在一页\n\n合成材料用于图形和构图验证。\n\n---\n\n${deckCases.map(s=>s.content).join('\n\n---\n\n')}`;
 const artifact={id:'qa_graphics_composer',kind:'slides',origin:'build',title:'图形组合',versionNumber:1,createdAt:'2026-10-17',content,outline:[{heading:'图形可以与说明共同组织在一页',goal:'',layout:'title'},...deckCases.map(s=>({heading:parseSlides(s.content)[0]!.heading,goal:'',layout:'composed'}))],citations:Array.from({length:8},(_,i)=>({id:`c${i+1}`,ref:i+1,sourceTitle:`合成训练来源 ${i+1}`,locator:'QA',quote:'用于验证的合成材料。'})),sections:[0,1,2,3].map(index=>({index,heading:'QA',checked:true,issues:[]}))} as unknown as Artifact;
 return <><style>{`body{margin:0;background:#e9eae7}main{padding:24px;max-width:1280px;margin:auto}.qa-heading{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:20px}.qa-heading h1{font:600 21px var(--gx-font-sans);margin:0 0 6px}.qa-heading p{font:13px var(--gx-font-sans);color:#555;margin:0}.qa-controls{display:flex;gap:8px}.qa-controls select{font:13px var(--gx-font-sans);padding:7px}.qa-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px 20px}.qa-label{font:12px var(--gx-font-sans);color:#4a4e49;margin-bottom:7px;display:flex;justify-content:space-between;gap:10px}.qa-stage{position:relative;aspect-ratio:16/9;container-type:inline-size;background:white}.qa-stage>.outputs-slide-face{position:absolute;transform-origin:0 0;scale:calc(100cqw / 1280px)}.qa-issue{font:12px var(--gx-font-sans);color:#725b24;margin:8px 0 0}.qa-product{min-height:760px;background:#f7f6f1;padding:20px}.qa-print .outputs-print-slide{position:relative;width:100%;height:auto;aspect-ratio:16/9;container-type:inline-size;overflow:visible;margin-bottom:20px}.qa-print .outputs-slide-face{position:absolute;transform-origin:0 0;scale:calc(100cqw / 1280px)}.qa-citation{position:fixed;bottom:20px;right:20px;padding:16px;background:#202820;color:white;z-index:900}.qa-citation button{margin-left:12px}@media(max-width:900px){.qa-grid{grid-template-columns:1fr}.qa-heading{align-items:flex-start;flex-direction:column}}`}</style>
 <main><header className="qa-heading"><div><h1>把图形、数字和说明放在同一页</h1><p>实际组件渲染 · 合成材料 · 流程、关系与数据均保留来源</p></div><div className="qa-controls"><select aria-label="Theme" value={theme} onChange={e=>setTheme(e.target.value)}>{['classic','briefing','editorial','keynote'].map(x=><option key={x}>{x}</option>)}</select><select aria-label="Accent" value={accent} onChange={e=>setAccent(e.target.value)}>{['ink','blue','teal','red','amber','violet'].map(x=><option key={x}>{x}</option>)}</select><select aria-label="View" value={view} onChange={e=>setView(e.target.value)}><option value="overview">混排概览</option><option value="all">全部样例</option><option value="model">真实模型</option><option value="product">产品预览</option><option value="print">打印路径</option></select></div></header>
 {view==='product'?<section className="qa-product"><SlidesView artifact={artifact} onCite={(_c,i)=>setCitation(i)} onTheme={setTheme} onAccent={setAccent}/></section>:view==='print'?<div className="qa-print"><PrintSlides artifact={artifact}/></div>:<div className="qa-grid">{selected.map((sample,i)=><Case key={`${i}-${theme}`} sample={sample} index={i} theme={theme} accent={accent} onCite={setCitation}/>)}</div>}
 {citation!==null&&<aside className="qa-citation" role="status">已打开合成来源 {citation+1}<button onClick={()=>setCitation(null)}>关闭</button></aside>}</main></>;
}
createRoot(document.getElementById('root')!).render(<App/>);
