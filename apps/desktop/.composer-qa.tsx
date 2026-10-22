import { useCallback, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Artifact } from '@gunther/contracts';
import { SlideFace } from './src/outputs/SlideFace';
import { SlidesView } from './src/outputs/SlidesView';
import { EditPane } from './src/outputs/EditPane';
import { PrintSlides } from './src/outputs/PrintSlides';
import { parseSlides, withTheme, withAccent } from './src/outputs/sections';
import cases from './.composer-qa-fixtures.json';
import './src/styles.css';
import './src/design/tokens.css';
import './src/design/primitives.css';
import './src/design/prose.css';
import './src/outputs/outputs.css';
import './src/outputs/slides/slides.css';

function Case({sample,index,theme,accent,onCite}:{sample:typeof cases[number];index:number;theme:string;accent:string;onCite:(n:number)=>void}) {
 const slide=useMemo(()=>parseSlides(sample.content)[0]!,[sample.content]);
 const [measurement,setMeasurement]=useState({fit:1,overflow:false});
 const [issue,setIssue]=useState<string|null>(null);
 const measure=useCallback((fit:number,overflow:boolean)=>setMeasurement(old=>old.fit===fit&&old.overflow===overflow?old:{fit,overflow}),[]);
 const issueChanged=useCallback((value:string|null)=>setIssue(value),[]);
 return <article data-case={index}><div className="qa-label"><span>{sample.label}</span><output>{Math.round(measurement.fit*100)}%{measurement.overflow?' · overflow':''}</output></div><div className="qa-stage"><SlideFace {...slide} title={false} theme={theme} accent={accent} numbers={[1,2,3,4,5,6,7]} onCite={onCite} onFitChange={measure} onSceneIssue={issueChanged}/></div>{issue&&<p className="qa-issue" role="status">{issue}</p>}</article>;
}
function App(){
 const [theme,setTheme]=useState('briefing'),[accent,setAccent]=useState('teal'),[view,setView]=useState('overview');
 const [citation,setCitation]=useState<number|null>(null),[saved,setSaved]=useState('');
 const content=`<!-- layout: title; theme: ${theme}; accent: ${accent} -->\n# 同一内容可以采用不同构图\n\n合成材料，仅作设计能力演示。\n\n---\n\n${[...cases.slice(0,3),...cases.slice(-2)].map(s=>s.content).join('\n\n---\n\n')}`;
 const artifact={id:'qa_composer',kind:'slides',origin:'build',title:'组合式页面',versionNumber:1,createdAt:'2026-10-17',content,outline:[{heading:'同一内容可以采用不同构图',goal:'',layout:'title'},...[...cases.slice(0,3),...cases.slice(-2)].map(s=>({heading:parseSlides(s.content)[0]!.heading,goal:'',layout:'composed'}))],citations:[{id:'c1',ref:1,sourceTitle:'合成训练材料',locator:'QA',quote:'用于测试的合成材料。'}],sections:[0,1,2,3,4,5].map(index=>({index,heading:'QA',checked:true,issues:[]}))} as unknown as Artifact;
 return <><style>{`body{margin:0;background:#e9eae7}main{padding:24px;max-width:1280px;margin:auto}.qa-heading{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:20px}.qa-heading h1{font:600 21px var(--gx-font-sans);margin:0 0 6px}.qa-heading p{font:13px var(--gx-font-sans);color:#555;margin:0}.qa-controls{display:flex;gap:8px}.qa-controls select{font:13px var(--gx-font-sans);padding:7px}.qa-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px 20px}.qa-label{font:12px var(--gx-font-sans);color:#4a4e49;margin-bottom:7px;display:flex;justify-content:space-between;gap:10px}.qa-stage{position:relative;aspect-ratio:16/9;container-type:inline-size;background:white}.qa-stage>.outputs-slide-face{position:absolute;transform-origin:0 0;scale:calc(100cqw / 1280px)}.qa-issue{font:12px var(--gx-font-sans);color:#725b24;margin:8px 0 0}.qa-product{min-height:760px;background:#f7f6f1;padding:20px}.qa-product .outputs-edit{height:820px}.qa-print .outputs-print-slide{position:relative;width:100%;height:auto;aspect-ratio:16/9;container-type:inline-size;overflow:visible;margin-bottom:20px}.qa-print .outputs-slide-face{position:absolute;transform-origin:0 0;scale:calc(100cqw / 1280px)}.qa-citation{position:fixed;bottom:20px;right:20px;padding:16px;background:#202820;color:white;z-index:900}.qa-citation button{margin-left:12px}@media(max-width:900px){.qa-grid{grid-template-columns:1fr}.qa-heading{align-items:flex-start;flex-direction:column}}`}</style>
 <main><header className="qa-heading"><div><h1>同一份内容，由元素组合成不同页面</h1><p>实际组件渲染 · 合成材料 · 所有内容与引用均保留</p></div><div className="qa-controls"><select aria-label="Theme" value={theme} onChange={e=>setTheme(e.target.value)}>{['classic','briefing','editorial','keynote'].map(x=><option key={x}>{x}</option>)}</select><select aria-label="Accent" value={accent} onChange={e=>setAccent(e.target.value)}>{['ink','blue','teal','red','amber','violet'].map(x=><option key={x}>{x}</option>)}</select><select aria-label="View" value={view} onChange={e=>setView(e.target.value)}><option value="overview">构图对照</option><option value="model">模型构图对照</option><option value="all">全部样例</option><option value="product">产品预览</option><option value="edit">内容编辑</option><option value="print">打印路径</option></select></div></header>
 {view==='overview'||view==='all'||view==='model'?<div className="qa-grid">{(view==='overview'?cases.slice(0,2):view==='model'?cases.slice(-2):cases).map((sample,i)=><Case key={`${i}-${theme}`} sample={sample} index={i} theme={theme} accent={accent} onCite={setCitation}/>)}</div>:view==='product'?<section className="qa-product"><SlidesView artifact={artifact} onCite={(_citation,index)=>setCitation(index)} onTheme={setTheme} onAccent={setAccent}/></section>:view==='edit'?<section className="qa-product"><EditPane key={`${theme}-${accent}`} artifact={artifact} saving={false} onSave={setSaved} onCancel={()=>setView('overview')} onCite={(_citation,index)=>setCitation(index)}/>{saved&&<output aria-label="Saved composition count">{parseSlides(saved).filter(s=>s.layout==='composed').length} composed pages saved; {saved.includes('<!-- scene:')?'design metadata retained':'no design metadata'}</output>}</section>:<div className="qa-print"><PrintSlides artifact={artifact}/></div>}
 {citation!==null&&<aside className="qa-citation" role="status">已打开合成来源 {citation+1}<button onClick={()=>setCitation(null)}>关闭</button></aside>}</main></>;
}
createRoot(document.getElementById('root')!).render(<App/>);
