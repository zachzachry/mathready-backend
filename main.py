import { useState, useEffect, useCallback } from "react";
import MathText from "./shared/MathText";
import { API } from "./shared/constants";

const STANDARDS = [
  "5.NR.1.1","5.NR.1.2","5.NR.2.1","5.NR.2.2",
  "5.NR.3.1","5.NR.3.2","5.NR.3.3","5.NR.3.4","5.NR.3.5","5.NR.3.6",
  "5.NR.4.1","5.NR.4.2","5.NR.4.3","5.NR.4.4","5.NR.5.1",
  "5.PAR.6.1","5.PAR.6.2",
  "5.MDR.7.1","5.MDR.7.2","5.MDR.7.3","5.MDR.7.4",
  "5.GSR.8.1","5.GSR.8.2","5.GSR.8.3","5.GSR.8.4",
];
const DOK_LABELS = { 1:"Recall", 2:"Skill/Concept", 3:"Strategic", 4:"Extended" };

const S = {
  inp:   { width:"100%", padding:"0.5rem 0.75rem", border:"1px solid #c8d3dd", borderRadius:"3px", fontSize:"0.85rem", background:"#fafbfc", boxSizing:"border-box" },
  lbl:   { display:"block", fontSize:"0.62rem", fontWeight:700, letterSpacing:"0.12em", color:"#555", marginBottom:"4px" },
  smBtn: { border:"1px solid #c8d3dd", borderRadius:"3px", padding:"4px 10px", cursor:"pointer", fontSize:"0.75rem", fontWeight:600, background:"#f0f4f8", color:"#333" },
  ta:    { width:"100%", padding:"0.5rem 0.75rem", border:"1px solid #c8d3dd", borderRadius:"3px", fontSize:"0.85rem", background:"#fafbfc", boxSizing:"border-box", resize:"vertical", minHeight:"80px", fontFamily:"sans-serif" },
  code:  { fontFamily:"monospace", fontSize:"1.1rem", letterSpacing:"0.18em", textTransform:"uppercase", fontWeight:700, color:"#003865" },
};

function genCode() {
  return Math.random().toString(36).substring(2,8).toUpperCase();
}

// ── Edit Question Modal ────────────────────────────────────
function EditModal({ question, onSave, onClose }) {
  const [q, setQ] = useState({ ...question });
  const [saving, setSaving] = useState(false);

  function updateChoice(i, val) {
    const choices = [...q.choices]; choices[i] = val;
    setQ(p => ({ ...p, choices }));
  }

  async function handleSave() {
    if (!q.question.trim() || q.choices.filter(c=>c.trim()).length < 4 || !q.correct.trim()) return;
    setSaving(true);
    try {
      await fetch(`${API}/questions`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(q) });
      onSave(q);
    } catch {}
    setSaving(false);
  }

  return (
    <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,.55)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:1000,padding:"1rem"}}>
      <div style={{background:"#fff",borderRadius:"6px",width:"100%",maxWidth:"600px",maxHeight:"90vh",overflow:"hidden",display:"flex",flexDirection:"column",boxShadow:"0 8px 32px rgba(0,0,0,.25)"}}>
        <div style={{background:"#003865",color:"#fff",padding:"0.9rem 1.25rem",display:"flex",justifyContent:"space-between",alignItems:"center",flexShrink:0}}>
          <div><div style={{fontSize:"0.6rem",opacity:.65,letterSpacing:"0.14em"}}>QUESTION BANK</div><div style={{fontSize:"1rem",fontWeight:700}}>Edit Question</div></div>
          <button onClick={onClose} style={{background:"rgba(255,255,255,.15)",border:"1px solid rgba(255,255,255,.3)",color:"#fff",borderRadius:"3px",padding:"5px 12px",cursor:"pointer",fontSize:"0.8rem"}}>✕ Cancel</button>
        </div>
        <div style={{overflowY:"auto",padding:"1.25rem",display:"flex",flexDirection:"column",gap:"0.85rem"}}>
          <div style={{display:"flex",gap:"0.75rem"}}>
            <div style={{flex:2}}><label style={S.lbl}>STANDARD</label>
              <select style={S.inp} value={q.standard} onChange={e=>setQ(p=>({...p,standard:e.target.value}))}>
                {STANDARDS.map(s=><option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div style={{flex:1}}><label style={S.lbl}>DOK</label>
              <select style={S.inp} value={q.dok||""} onChange={e=>setQ(p=>({...p,dok:Number(e.target.value)}))}>
                <option value="">—</option>
                {[1,2,3,4].map(d=><option key={d} value={d}>{d} — {DOK_LABELS[d]}</option>)}
              </select>
            </div>
          </div>
          <div><label style={S.lbl}>SKILL LABEL</label>
            <input style={S.inp} value={q.short} onChange={e=>setQ(p=>({...p,short:e.target.value}))} placeholder="e.g. Add Fractions"/>
          </div>
          <div><label style={S.lbl}>QUESTION TEXT (use $...$ for math)</label>
            <textarea style={S.ta} value={q.question} onChange={e=>setQ(p=>({...p,question:e.target.value}))} rows={3}/>
            {q.question&&<div style={{marginTop:"4px",padding:"0.5rem 0.75rem",background:"#f8fafc",border:"1px solid #dde3e9",borderRadius:"3px",fontSize:"0.85rem",fontFamily:"Georgia,serif"}}><MathText text={q.question}/></div>}
          </div>
          <div><label style={S.lbl}>ANSWER CHOICES — click letter to mark correct</label>
            <div style={{display:"flex",flexDirection:"column",gap:"0.4rem"}}>
              {["A","B","C","D"].map((letter,i)=>{
                const isCorrect=q.correct===q.choices[i];
                return (
                  <div key={i} style={{display:"flex",alignItems:"center",gap:"0.5rem"}}>
                    <div onClick={()=>setQ(p=>({...p,correct:p.choices[i]}))}
                      style={{width:"24px",height:"24px",borderRadius:"50%",background:isCorrect?"#1a6e2e":"#e8edf2",border:`2px solid ${isCorrect?"#1a6e2e":"#bcc8d4"}`,display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0,cursor:"pointer"}}>
                      <span style={{fontSize:"0.65rem",fontWeight:700,color:isCorrect?"#fff":"#667"}}>{letter}</span>
                    </div>
                    <input style={{...S.inp,flex:1,border:`1px solid ${isCorrect?"#1a6e2e":"#c8d3dd"}`,background:isCorrect?"#f0faf2":"#fafbfc"}}
                      value={q.choices[i]} onChange={e=>updateChoice(i,e.target.value)} placeholder={`Choice ${letter}`}/>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        <div style={{padding:"0.9rem 1.25rem",borderTop:"1px solid #dde3e9",display:"flex",gap:"0.65rem",justifyContent:"flex-end",flexShrink:0}}>
          <button onClick={onClose} style={{...S.smBtn,padding:"0.6rem 1.25rem"}}>Cancel</button>
          <button onClick={handleSave} disabled={saving} style={{background:"#003865",border:"none",borderRadius:"3px",padding:"0.6rem 1.5rem",fontSize:"0.85rem",fontWeight:700,color:"#fff",cursor:"pointer"}}>
            {saving?"Saving…":"💾 Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Save Test Modal ────────────────────────────────────────
function SaveTestModal({ count, currentTitle, savedTests = [], onSave, onClose }) {
  const [name,      setName]      = useState(currentTitle || "");
  const [code,      setCode]      = useState(genCode());
  const [saving,    setSaving]    = useState(false);
  const [codeErr,   setCodeErr]   = useState("");
  const [adaptive,  setAdaptive]  = useState(false);
  const [overwriteWarning, setOverwriteWarning] = useState(false);
  const [untimed,        setUntimed]        = useState(false);
  const [timeMins,       setTimeMins]       = useState(30);
  const [warnMins,       setWarnMins]       = useState(5);
  const [oneAttempt,     setOneAttempt]     = useState(false);
  const [classes,        setClasses]        = useState([]);
  const [assignedClassIds, setAssignedClassIds] = useState([]);

  useEffect(() => {
    fetch(`${API}/roster`).then(r=>r.json()).then(d=>setClasses(Array.isArray(d)?d:[])).catch(()=>{});
  }, []);

  function toggleClass(id) {
    setAssignedClassIds(prev => prev.includes(id) ? prev.filter(x=>x!==id) : [...prev, id]);
  }

  const duplicate = savedTests.find(t =>
    t.name?.trim().toLowerCase() === name.trim().toLowerCase()
  );

  function handleCodeChange(val) {
    const clean = val.toUpperCase().replace(/[^A-Z0-9]/g,"").slice(0,8);
    setCode(clean);
    setCodeErr(clean.length < 4 ? "Code must be at least 4 characters" : "");
  }

  async function handleSave() {
    if (!name.trim() || code.length < 4) return;
    if (duplicate && !overwriteWarning) { setOverwriteWarning(true); return; }
    setSaving(true);
    const timerCfg = {
      untimed,
      timeLimitSecs: untimed ? 0 : Math.max(1, timeMins) * 60,
      warnSecs:      untimed ? 0 : Math.max(1, warnMins) * 60,
      oneAttempt,
      classIds: assignedClassIds,
    };
    const err = await onSave(name.trim(), code, adaptive, timerCfg);
    if (err) { setCodeErr(err); setSaving(false); setOverwriteWarning(false); }
  }

  return (
    <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,.55)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:1000}}>
      <div style={{background:"#fff",borderRadius:"6px",width:"100%",maxWidth:"420px",overflow:"hidden",boxShadow:"0 8px 32px rgba(0,0,0,.25)"}}>
        <div style={{background:"#003865",color:"#fff",padding:"0.9rem 1.25rem"}}>
          <div style={{fontSize:"0.6rem",opacity:.65,letterSpacing:"0.14em"}}>TEST LIBRARY</div>
          <div style={{fontSize:"1rem",fontWeight:700}}>Save Test</div>
        </div>
        <div style={{padding:"1.25rem",display:"flex",flexDirection:"column",gap:"0.85rem"}}>
          <div><label style={S.lbl}>TEST NAME</label>
            <input style={S.inp} value={name} onChange={e=>setName(e.target.value)} placeholder="e.g. Chapter 3 Fractions Quiz" autoFocus/>
          </div>
          <div>
            <label style={S.lbl}>STUDENT CODE — students enter this to access the test</label>
            <div style={{display:"flex",gap:"0.5rem",alignItems:"center"}}>
              <input style={{...S.inp,...S.code,flex:1}} value={code} onChange={e=>handleCodeChange(e.target.value)} maxLength={8} placeholder="e.g. FRACTIONS"/>
              <button onClick={()=>setCode(genCode())} style={{...S.smBtn,flexShrink:0,padding:"0.5rem 0.75rem"}}>🔀 New</button>
            </div>
            {codeErr
              ? <div style={{fontSize:"0.7rem",color:"#8b1a1a",marginTop:"4px"}}>⚠ {codeErr}</div>
              : <div style={{fontSize:"0.7rem",color:"#888",marginTop:"4px"}}>4–8 characters, letters and numbers only</div>
            }
          </div>
          <div style={{background:"#f0f4f8",borderRadius:"3px",padding:"0.65rem 0.85rem",fontSize:"0.78rem",color:"#555"}}>
            Students log in and enter <strong style={S.code}>{code||"—"}</strong> to take this {count}-question test.
          </div>
          <div>
            <label style={{display:"flex",alignItems:"center",gap:"0.75rem",cursor:"pointer",padding:"0.65rem 0.85rem",background:adaptive?"#f0faf2":"#fafbfc",border:`1px solid ${adaptive?"#b3dfc0":"#dde3e9"}`,borderRadius:"3px"}}>
              <div onClick={()=>setAdaptive(a=>!a)}
                style={{width:"36px",height:"20px",borderRadius:"10px",background:adaptive?"#1a6e2e":"#c8d3dd",position:"relative",flexShrink:0,transition:"background .2s"}}>
                <div style={{position:"absolute",top:"2px",left:adaptive?"18px":"2px",width:"16px",height:"16px",borderRadius:"50%",background:"#fff",transition:"left .2s"}}/>
              </div>
              <div>
                <div style={{fontSize:"0.82rem",fontWeight:700,color:adaptive?"#1a6e2e":"#333"}}>Adaptive Mode {adaptive?"ON":"OFF"}</div>
                <div style={{fontSize:"0.7rem",color:"#888",marginTop:"1px"}}>Questions adjust to each student's weak areas during the test</div>
              </div>
            </label>
          </div>
        <div style={{borderTop:"1px solid #eef1f4",paddingTop:"0.85rem"}}>
          <label style={{display:"flex",alignItems:"center",gap:"0.75rem",cursor:"pointer",marginBottom:"0.75rem",
            padding:"0.65rem 0.85rem",background:untimed?"#fff3cd":"#fafbfc",
            border:`1px solid ${untimed?"#ffc107":"#dde3e9"}`,borderRadius:"3px"}}
            onClick={()=>setUntimed(u=>!u)}>
            <div style={{width:"36px",height:"20px",borderRadius:"10px",background:untimed?"#b8860b":"#c8d3dd",position:"relative",flexShrink:0,transition:"background .2s"}}>
              <div style={{position:"absolute",top:"2px",left:untimed?"18px":"2px",width:"16px",height:"16px",borderRadius:"50%",background:"#fff",transition:"left .2s"}}/>
            </div>
            <div>
              <div style={{fontSize:"0.82rem",fontWeight:700,color:untimed?"#7a4e00":"#333"}}>Untimed {untimed?"ON":"OFF"}</div>
              <div style={{fontSize:"0.7rem",color:"#888",marginTop:"1px"}}>No countdown — students work at their own pace</div>
            </div>
          </label>
          {!untimed && (
            <div style={{display:"flex",gap:"0.75rem"}}>
              <div style={{flex:1}}>
                <label style={{display:"block",fontSize:"0.62rem",fontWeight:700,letterSpacing:"0.1em",color:"#555",marginBottom:"4px"}}>TIME LIMIT (minutes)</label>
                <input type="number" min="1" max="180" value={timeMins}
                  onChange={e=>setTimeMins(Math.max(1,Math.min(180,Number(e.target.value))))}
                  style={{...S.inp,fontFamily:"monospace",fontWeight:700,fontSize:"1rem"}}/>
              </div>
              <div style={{flex:1}}>
                <label style={{display:"block",fontSize:"0.62rem",fontWeight:700,letterSpacing:"0.1em",color:"#555",marginBottom:"4px"}}>WARN AT (minutes left)</label>
                <input type="number" min="1" max={timeMins-1} value={warnMins}
                  onChange={e=>setWarnMins(Math.max(1,Math.min(timeMins-1,Number(e.target.value))))}
                  style={{...S.inp,fontFamily:"monospace",fontWeight:700,fontSize:"1rem"}}/>
              </div>
            </div>
          )}
        </div>
        {/* One Attempt toggle */}
        <div>
          <label style={{display:"flex",alignItems:"center",gap:"0.75rem",cursor:"pointer",padding:"0.65rem 0.85rem",
            background:oneAttempt?"#fdf2f2":"#fafbfc",
            border:`1px solid ${oneAttempt?"#f0b8b8":"#dde3e9"}`,borderRadius:"3px"}}
            onClick={()=>setOneAttempt(a=>!a)}>
            <div style={{width:"36px",height:"20px",borderRadius:"10px",background:oneAttempt?"#8b1a1a":"#c8d3dd",position:"relative",flexShrink:0,transition:"background .2s"}}>
              <div style={{position:"absolute",top:"2px",left:oneAttempt?"18px":"2px",width:"16px",height:"16px",borderRadius:"50%",background:"#fff",transition:"left .2s"}}/>
            </div>
            <div>
              <div style={{fontSize:"0.82rem",fontWeight:700,color:oneAttempt?"#8b1a1a":"#333"}}>
                One Attempt Only {oneAttempt?"ON":"OFF"}
              </div>
              <div style={{fontSize:"0.7rem",color:"#888",marginTop:"1px"}}>
                Students can only submit this test once. They cannot retake it.
              </div>
            </div>
          </label>
        </div>
        </div>
        {overwriteWarning && (
          <div style={{padding:"0.75rem 1.25rem",background:"#fff8e1",borderTop:"1px solid #ffd166"}}>
            <div style={{fontSize:"0.82rem",color:"#7a4e00",fontWeight:700,marginBottom:"4px"}}>
              ⚠ A test named "{name.trim()}" already exists.
            </div>
            <div style={{fontSize:"0.75rem",color:"#7a4e00"}}>
              This will save as a second copy with a different code. Click Save again to confirm.
            </div>
          </div>
        )}
        <div style={{display:"flex",gap:"0.65rem",padding:"0.9rem 1.25rem",borderTop:"1px solid #dde3e9"}}>
          <button onClick={()=>{ setOverwriteWarning(false); onClose(); }} style={{flex:1,background:"#f0f4f8",border:"1px solid #c8d3dd",borderRadius:"3px",padding:"0.65rem",fontSize:"0.85rem",cursor:"pointer",fontWeight:600,color:"#333"}}>Cancel</button>
          <button onClick={handleSave} disabled={saving||!name.trim()||code.length<4}
            style={{flex:1,background:(!name.trim()||code.length<4)?"#c8d3dd":overwriteWarning?"#b8860b":"#003865",border:"none",borderRadius:"3px",padding:"0.65rem",fontSize:"0.85rem",cursor:(!name.trim()||code.length<4)?"not-allowed":"pointer",color:"#fff",fontWeight:700}}>
            {saving?"Saving…":overwriteWarning?"Save Anyway →":"💾 Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main TestBuilder ───────────────────────────────────────
export default function TestBuilder() {
  const [bank, setBank]               = useState([]);
  const [selected, setSelected]       = useState([]);
  const [loading, setLoading]         = useState(true);

  const [testTitle, setTestTitle]     = useState("Grade 5 Math — Practice");
  const [editingQ, setEditingQ]       = useState(null);
  const [confirmDelete,     setConfirmDelete]     = useState(null);
  const [confirmDeleteTest, setConfirmDeleteTest] = useState(null);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [savedTests, setSavedTests]   = useState([]);
  const [allClasses,  setAllClasses]  = useState([]);
  const [assigningTest, setAssigningTest] = useState(null); // test id being assigned
  const [savedMsg, setSavedMsg]       = useState("");
  const [rightTab, setRightTab]       = useState("current");

  const [filterStd,  setFilterStd]  = useState("");
  const [filterDok,  setFilterDok]  = useState("");
  const [filterText, setFilterText] = useState("");
  const [autoCount,  setAutoCount]  = useState(10);

  // Fluency Drill state
  const [drillName,     setDrillName]     = useState("Fluency Drill");
  const [drillStds,     setDrillStds]     = useState([]);
  const [drillCount,    setDrillCount]    = useState(10);
  const [drillCode,     setDrillCode]     = useState(genCode());
  const [drillSaving,   setDrillSaving]   = useState(false);
  const [drillMsg,      setDrillMsg]      = useState("");
  const [drillCodeErr,  setDrillCodeErr]  = useState("");

  const loadBank = useCallback(async () => {
    try { const r=await fetch(`${API}/questions`); setBank(await r.json()); }
    catch { setBank([]); }
    setLoading(false);
  }, []);

  const loadActive = useCallback(async () => {
    try {
      const r=await fetch(`${API}/test/active`); const t=await r.json();
      setSelected((t.questions||[]).map(q=>q.id));
      setTestTitle(t.title||"Grade 5 Math — Practice");
    } catch {}
  }, []);

  const loadSavedTests = useCallback(async () => {
    try { const r=await fetch(`${API}/tests/saved`); setSavedTests(await r.json()); }
    catch { setSavedTests([]); }
  }, []);

  useEffect(()=>{
    loadBank(); loadActive(); loadSavedTests();
    fetch(`${API}/roster`).then(r=>r.json()).then(d=>setAllClasses(Array.isArray(d)?d:[])).catch(()=>{});
  },[loadBank,loadActive,loadSavedTests]);

  const filtered = bank.filter(q => {
    if (filterStd  && !q.standard?.startsWith(filterStd)) return false;
    if (filterDok  && q.dok !== Number(filterDok))         return false;
    if (filterText) {
      const t = filterText.toLowerCase();
      const matchesId = q.id?.toLowerCase().includes(t);
      const matchesQ  = q.question?.toLowerCase().includes(t);
      const matchesS  = q.short?.toLowerCase().includes(t);
      if (!matchesId && !matchesQ && !matchesS) return false;
    }
    return true;
  });

  const selectedQuestions = selected.map(id=>bank.find(q=>q.id===id)).filter(Boolean);
  const isSelected = id => selected.includes(id);

  function toggleSelect(q) { setSelected(s=>s.includes(q.id)?s.filter(x=>x!==q.id):[...s,q.id]); }
  function moveUp(i)       { setSelected(s=>{const a=[...s];[a[i-1],a[i]]=[a[i],a[i-1]];return a;}); }
  function moveDown(i)     { setSelected(s=>{const a=[...s];[a[i],a[i+1]]=[a[i+1],a[i]];return a;}); }
  function removeFromTest(id) { setSelected(s=>s.filter(x=>x!==id)); }

  function autoFill() {
    const needed=autoCount-selected.length; if(needed<=0)return;
    const candidates=filtered.filter(q=>!selected.includes(q.id));
    const shuffled=[...candidates].sort(()=>Math.random()-.5);
    setSelected(s=>[...s,...shuffled.slice(0,needed).map(q=>q.id)]);
  }

  async function deleteQuestion(id) {
    try { await fetch(`${API}/questions/${id}`,{method:"DELETE"}); setBank(b=>b.filter(q=>q.id!==id)); setSelected(s=>s.filter(x=>x!==id)); }
    catch {}
    setConfirmDelete(null);
  }

  function handleSaveEdit(updated) { setBank(b=>b.map(q=>q.id===updated.id?updated:q)); setEditingQ(null); }

  async function assignClasses(testId, classIds) {
    const t = savedTests.find(x => x.id === testId);
    if (!t) return;
    try {
      await fetch(`${API}/tests/saved/${testId}`, {
        method:"PUT", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({...t, classIds})
      });
      await loadSavedTests();
    } catch {}
    setAssigningTest(null);
  }

  async function saveTest(name, code, adaptive=false, timerCfg={}) {
    try {
      const r = await fetch(`${API}/tests/saved`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,code,title:testTitle,questions:selectedQuestions,adaptive,...timerCfg})});
      const data = await r.json();
      if (r.status===400) return data.detail || "Code already in use";
      await loadSavedTests();
      setSavedMsg(`Saved! Code: ${data.code}`);
      setTimeout(()=>setSavedMsg(""),5000);
      setShowSaveModal(false);
    } catch { return "Save failed"; }
  }

  async function saveDrill() {
    if (!drillName.trim() || drillStds.length === 0 || drillCode.length < 4) return;
    setDrillSaving(true); setDrillCodeErr("");
    try {
      const r = await fetch(`${API}/tests/saved`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          name: drillName.trim(),
          code: drillCode,
          title: drillName.trim(),
          questions: [],
          type: "drill",
          drillStandards: drillStds,
          drillCount,
        }),
      });
      const data = await r.json();
      if (r.status === 400) { setDrillCodeErr(data.detail || "Code already in use"); setDrillSaving(false); return; }
      await loadSavedTests();
      setDrillMsg(`Saved! Code: ${data.code}`);
      setDrillCode(genCode());
      setTimeout(() => setDrillMsg(""), 5000);
    } catch { setDrillCodeErr("Save failed"); }
    setDrillSaving(false);
  }

  async function loadSavedTest(id) {
    try {
      const r=await fetch(`${API}/tests/saved/${id}`); const t=await r.json();
      setSelected((t.questions||[]).map(q=>q.id));
      setTestTitle(t.title||t.name||"");
      setRightTab("current");
    } catch {}
  }

  async function deleteSavedTest(id) {
    try { await fetch(`${API}/tests/saved/${id}`,{method:"DELETE"}); setSavedTests(s=>s.filter(t=>t.id!==id)); }
    catch {}
  }

  if (loading) return <div style={{padding:"3rem",textAlign:"center",color:"#aaa"}}>Loading question bank…</div>;

  return (
    <div style={{display:"flex",height:"100%",fontFamily:"sans-serif",background:"#e8edf2",overflow:"hidden"}}>

      {/* ── Left: Bank browser ── */}
      <div style={{width:"55%",display:"flex",flexDirection:"column",borderRight:"2px solid #c8d3dd",overflow:"hidden"}}>
        <div style={{background:"#fff",borderBottom:"1px solid #c8d3dd",padding:"0.75rem 1rem",display:"flex",flexDirection:"column",gap:"0.5rem",flexShrink:0}}>
          <div style={{display:"flex",gap:"0.5rem",alignItems:"center"}}>
            <div style={{fontSize:"0.65rem",fontWeight:700,letterSpacing:"0.12em",color:"#003865"}}>QUESTION BANK</div>
            <span style={{fontSize:"0.7rem",color:"#aaa"}}>{bank.length} questions · {filtered.length} shown</span>
          </div>
          <div style={{display:"flex",gap:"0.5rem",flexWrap:"wrap"}}>
            <select style={{...S.inp,flex:2,minWidth:"120px"}} value={filterStd} onChange={e=>setFilterStd(e.target.value)}>
              <option value="">All Standards</option>
              {STANDARDS.map(s=><option key={s} value={s}>{s}</option>)}
            </select>
            <select style={{...S.inp,flex:1,minWidth:"100px"}} value={filterDok} onChange={e=>setFilterDok(e.target.value)}>
              <option value="">All DOK</option>
              {[1,2,3,4].map(d=><option key={d} value={d}>DOK {d}</option>)}
            </select>
            <input style={{...S.inp,flex:2,minWidth:"120px"}} value={filterText} onChange={e=>setFilterText(e.target.value)} placeholder="Search by ID, keyword…"/>
          </div>
          <div style={{display:"flex",gap:"0.5rem",alignItems:"center",flexWrap:"wrap"}}>
            <span style={{fontSize:"0.72rem",color:"#555"}}>
              Auto-fill <input type="number" min={1} max={50} value={autoCount} onChange={e=>setAutoCount(Number(e.target.value))}
                style={{width:"42px",padding:"2px 5px",border:"1px solid #c8d3dd",borderRadius:"3px",fontSize:"0.78rem",textAlign:"center"}}/> questions
            </span>
            <button onClick={autoFill} style={{...S.smBtn,background:"#003865",color:"#fff",borderColor:"#003865"}}>⚡ Auto-fill</button>
            <button onClick={()=>setSelected([])} style={{...S.smBtn,color:"#8b1a1a",borderColor:"#f0b8b8"}}>Clear All</button>
          </div>
        </div>

        <div style={{flex:1,overflowY:"auto",padding:"0.75rem"}}>
          {bank.length===0?(
            <div style={{background:"#fff",border:"1px solid #c8d3dd",borderRadius:"3px",padding:"3rem",textAlign:"center",color:"#aaa"}}>
              <div style={{fontSize:"2rem",marginBottom:"0.5rem"}}>📭</div>
              <div style={{fontWeight:600,color:"#555"}}>Question bank is empty</div>
              <div style={{fontSize:"0.82rem",marginTop:"4px"}}>Use the Question Builder or PDF Importer to add questions.</div>
            </div>
          ):filtered.length===0?(
            <div style={{padding:"2rem",textAlign:"center",color:"#aaa",fontSize:"0.85rem"}}>No questions match your filters.</div>
          ):(
            filtered.map(q=>{
              const sel=isSelected(q.id);
              return (
                <div key={q.id} style={{background:sel?"#ddeaf7":"#fff",border:`2px solid ${sel?"#003865":"#c8d3dd"}`,borderRadius:"4px",padding:"0.7rem 1rem",marginBottom:"0.5rem",display:"flex",alignItems:"flex-start",gap:"0.75rem"}}>
                  <div onClick={()=>toggleSelect(q)} style={{width:"20px",height:"20px",borderRadius:"4px",border:`2px solid ${sel?"#003865":"#bcc8d4"}`,background:sel?"#003865":"#fff",display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0,marginTop:"2px",cursor:"pointer"}}>
                    {sel&&<span style={{color:"#fff",fontSize:"0.7rem",fontWeight:700}}>✓</span>}
                  </div>
                  <div style={{flex:1,minWidth:0,cursor:"pointer"}} onClick={()=>toggleSelect(q)}>
                    <div style={{display:"flex",gap:"0.4rem",marginBottom:"3px",flexWrap:"wrap",alignItems:"center"}}>
                      {q.id&&<span style={{fontSize:"0.65rem",fontWeight:700,fontFamily:"monospace",color:"#fff",background:"#003865",padding:"1px 7px",borderRadius:"3px",letterSpacing:"0.05em"}}>{q.id}</span>}
                      <span style={{fontSize:"0.6rem",fontWeight:700,color:"#003865",background:"#ddeaf7",padding:"1px 6px",borderRadius:"2px",border:"1px solid #b3cde8"}}>{q.standard}</span>
                      {q.dok&&<span style={{fontSize:"0.6rem",fontWeight:700,color:"#7a4e00",background:"#fff3cd",padding:"1px 6px",borderRadius:"2px",border:"1px solid #ffc107"}}>DOK {q.dok}</span>}
                      <span style={{fontSize:"0.6rem",color:"#888"}}>{q.short}</span>
                    </div>
                    <div style={{fontSize:"0.85rem",color:"#1a1a1a",fontFamily:"Georgia,serif",lineHeight:1.5,overflow:"hidden",display:"-webkit-box",WebkitLineClamp:2,WebkitBoxOrient:"vertical"}}>
                      <MathText text={q.question}/>
                    </div>
                  </div>
                  <div style={{display:"flex",gap:"3px",flexShrink:0}}>
                    <button onClick={e=>{e.stopPropagation();setEditingQ(q);}} style={{...S.smBtn,padding:"3px 8px",color:"#003865",borderColor:"#b3cde8",background:"#f0f6ff"}}>✏️</button>
                    <button onClick={e=>{e.stopPropagation();setConfirmDelete(q);}} style={{...S.smBtn,padding:"3px 8px",color:"#8b1a1a",borderColor:"#f0b8b8",background:"#fdf2f2"}}>🗑</button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* ── Right panel ── */}
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
        <div style={{background:"#004e94",display:"flex",alignItems:"flex-end",padding:"0 1rem",gap:"0.15rem",flexShrink:0}}>
          {[["current","📋 Current Test"],["drill","⚡ Fluency Drill"],["library","📚 Test Library"]].map(([key,lbl])=>(
            <button key={key} onClick={()=>setRightTab(key)}
              style={{background:rightTab===key?"#fff":"transparent",color:rightTab===key?"#003865":"#cce0f5",border:"none",padding:"0.55rem 0.9rem",fontSize:"0.75rem",fontWeight:700,cursor:"pointer",borderRadius:"4px 4px 0 0"}}>
              {lbl}{key==="library"&&savedTests.length>0&&<span style={{marginLeft:"5px",background:rightTab===key?"#003865":"rgba(255,255,255,.25)",color:"#fff",borderRadius:"10px",padding:"0px 6px",fontSize:"0.65rem"}}>{savedTests.length}</span>}
            </button>
          ))}
        </div>

        {/* Current Test */}
        {rightTab==="current"&&(
          <>
            <div style={{background:"#fff",borderBottom:"1px solid #c8d3dd",padding:"0.75rem 1rem",flexShrink:0}}>
              <div style={{fontSize:"0.65rem",fontWeight:700,letterSpacing:"0.12em",color:"#003865",marginBottom:"0.4rem"}}>CURRENT SELECTION</div>
              <input style={{...S.inp,fontWeight:600}} value={testTitle} onChange={e=>setTestTitle(e.target.value)} placeholder="Test title…"/>
              <div style={{fontSize:"0.7rem",color:"#888",marginTop:"4px"}}>
                {selected.length} question{selected.length!==1?"s":""} selected
                {savedMsg&&<span style={{marginLeft:"0.75rem",color:"#1a6e2e",fontWeight:700,fontFamily:"monospace"}}>✓ {savedMsg}</span>}
              </div>
            </div>

            <div style={{flex:1,overflowY:"auto",padding:"0.75rem"}}>
              {selected.length===0?(
                <div style={{padding:"3rem 1rem",textAlign:"center",color:"#aaa"}}>
                  <div style={{fontSize:"2rem",marginBottom:"0.5rem"}}>👈</div>
                  <div style={{fontWeight:600,color:"#555",marginBottom:"4px"}}>No questions selected</div>
                  <div style={{fontSize:"0.82rem"}}>Click questions on the left or use Auto-fill.</div>
                </div>
              ):(
                selectedQuestions.map((q,i)=>(
                  <div key={q.id} style={{background:"#fff",border:"1px solid #c8d3dd",borderRadius:"4px",padding:"0.6rem 0.85rem",marginBottom:"0.4rem",display:"flex",alignItems:"center",gap:"0.6rem"}}>
                    <div style={{width:"22px",height:"22px",borderRadius:"50%",background:"#003865",display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0}}>
                      <span style={{color:"#fff",fontSize:"0.65rem",fontWeight:700}}>{i+1}</span>
                    </div>
                    <div style={{flex:1,minWidth:0}}>
                      <div style={{display:"flex",gap:"0.35rem",marginBottom:"2px"}}>
                        <span style={{fontSize:"0.58rem",fontWeight:700,color:"#003865",background:"#ddeaf7",padding:"1px 5px",borderRadius:"2px"}}>{q.standard}</span>
                        {q.dok&&<span style={{fontSize:"0.58rem",fontWeight:700,color:"#7a4e00",background:"#fff3cd",padding:"1px 5px",borderRadius:"2px"}}>DOK {q.dok}</span>}
                      </div>
                      <div style={{fontSize:"0.8rem",color:"#1a1a1a",fontFamily:"Georgia,serif",overflow:"hidden",whiteSpace:"nowrap",textOverflow:"ellipsis"}}>
                        <MathText text={q.question}/>
                      </div>
                    </div>
                    <div style={{display:"flex",gap:"2px",flexShrink:0}}>
                      {i>0&&<button onClick={()=>moveUp(i)} style={{...S.smBtn,padding:"3px 7px"}}>↑</button>}
                      {i<selected.length-1&&<button onClick={()=>moveDown(i)} style={{...S.smBtn,padding:"3px 7px"}}>↓</button>}
                      <button onClick={()=>removeFromTest(q.id)} style={{...S.smBtn,color:"#8b1a1a",borderColor:"#f0b8b8",padding:"3px 7px"}}>✕</button>
                    </div>
                  </div>
                ))
              )}
            </div>

            <div style={{padding:"0.85rem 1rem",borderTop:"2px solid #c8d3dd",background:"#fff",flexShrink:0}}>
              <button onClick={()=>setShowSaveModal(true)} disabled={selected.length===0}
                style={{width:"100%",background:selected.length===0?"#c8d3dd":"#003865",border:"none",borderRadius:"4px",padding:"0.8rem",fontSize:"0.95rem",fontWeight:700,color:"#fff",cursor:selected.length===0?"not-allowed":"pointer"}}>
                💾 Save to Library & Get Code
              </button>
              {selected.length>0&&(
                <div style={{fontSize:"0.7rem",color:"#888",textAlign:"center",marginTop:"5px"}}>
                  Students use the code to access this test
                </div>
              )}
            </div>
          </>
        )}

        {/* Fluency Drill */}
        {rightTab==="drill"&&(
          <div style={{flex:1,overflowY:"auto",padding:"1rem",display:"flex",flexDirection:"column",gap:"1rem"}}>
            <div style={{background:"#fff8e1",border:"1px solid #ffd166",borderRadius:"6px",padding:"0.85rem 1rem",fontSize:"0.8rem",color:"#7a4e00",lineHeight:1.5}}>
              ⚡ <strong>Fluency Drill</strong> — each student gets <em>different numbers</em>, same standard. Great for fluency practice without answer sharing.
            </div>

            {/* Drill name */}
            <div style={{background:"#fff",border:"1px solid #c8d3dd",borderRadius:"4px",padding:"0.9rem 1rem"}}>
              <label style={S.lbl}>DRILL NAME</label>
              <input style={S.inp} value={drillName} onChange={e=>setDrillName(e.target.value)} placeholder="e.g. Multiplication Fluency"/>
            </div>

            {/* Standard picker */}
            <div style={{background:"#fff",border:"1px solid #c8d3dd",borderRadius:"4px",padding:"0.9rem 1rem"}}>
              <label style={S.lbl}>STANDARDS — pick one or more (must have ⚡ generator)</label>
              <div style={{display:"flex",flexWrap:"wrap",gap:"0.4rem",marginTop:"0.4rem"}}>
                {["5.NR.1.1","5.NR.2.1","5.NR.2.2","5.NR.3.1","5.NR.3.2","5.NR.3.3","5.NR.4.1","5.NR.4.2"].map(std=>{
                  const on = drillStds.includes(std);
                  return (
                    <button key={std} onClick={()=>setDrillStds(s=>on?s.filter(x=>x!==std):[...s,std])}
                      style={{padding:"5px 10px",borderRadius:"4px",border:`2px solid ${on?"#1a6e2e":"#c8d3dd"}`,background:on?"#f0faf2":"#fafbfc",color:on?"#1a6e2e":"#555",fontSize:"0.75rem",fontWeight:700,cursor:"pointer"}}>
                      {on?"✓ ":""}{std}
                    </button>
                  );
                })}
              </div>
              {drillStds.length === 0 && <div style={{fontSize:"0.7rem",color:"#8b1a1a",marginTop:"6px"}}>Select at least one standard</div>}
            </div>

            {/* Question count */}
            <div style={{background:"#fff",border:"1px solid #c8d3dd",borderRadius:"4px",padding:"0.9rem 1rem"}}>
              <label style={S.lbl}>QUESTIONS PER SESSION</label>
              <div style={{display:"flex",gap:"0.5rem",marginTop:"0.4rem"}}>
                {[5,10,15,20].map(n=>(
                  <button key={n} onClick={()=>setDrillCount(n)}
                    style={{flex:1,padding:"0.55rem",border:`2px solid ${drillCount===n?"#003865":"#c8d3dd"}`,borderRadius:"4px",background:drillCount===n?"#003865":"#fafbfc",color:drillCount===n?"#fff":"#555",fontSize:"0.85rem",fontWeight:700,cursor:"pointer"}}>
                    {n}
                  </button>
                ))}
              </div>
            </div>

            {/* Code */}
            <div style={{background:"#fff",border:"1px solid #c8d3dd",borderRadius:"4px",padding:"0.9rem 1rem"}}>
              <label style={S.lbl}>STUDENT CODE</label>
              <div style={{display:"flex",gap:"0.5rem",alignItems:"center"}}>
                <input style={{...S.inp,...S.code,flex:1}} value={drillCode}
                  onChange={e=>{ const v=e.target.value.toUpperCase().replace(/[^A-Z0-9]/g,"").slice(0,8); setDrillCode(v); setDrillCodeErr(v.length<4?"Code must be at least 4 characters":""); }}
                  maxLength={8} placeholder="e.g. MULT5"/>
                <button onClick={()=>setDrillCode(genCode())} style={{...S.smBtn,flexShrink:0,padding:"0.5rem 0.75rem"}}>🔀</button>
              </div>
              {drillCodeErr
                ? <div style={{fontSize:"0.7rem",color:"#8b1a1a",marginTop:"4px"}}>⚠ {drillCodeErr}</div>
                : <div style={{fontSize:"0.7rem",color:"#888",marginTop:"4px"}}>Students enter this code to start the drill</div>}
            </div>

            {/* Preview */}
            {drillStds.length > 0 && (
              <div style={{background:"#f0f4f8",borderRadius:"4px",padding:"0.75rem 1rem",fontSize:"0.78rem",color:"#555"}}>
                Each student gets <strong>{drillCount} unique questions</strong> on{" "}
                <strong>{drillStds.join(", ")}</strong> — numbers randomized per student.
              </div>
            )}

            {drillMsg && <div style={{background:"#f0faf2",border:"1px solid #b3dfc0",borderRadius:"4px",padding:"0.65rem 1rem",fontSize:"0.82rem",color:"#1a6e2e",fontWeight:700}}>✓ {drillMsg}</div>}

            <button onClick={saveDrill}
              disabled={drillSaving || drillStds.length===0 || !drillName.trim() || drillCode.length<4}
              style={{background:(drillStds.length===0||!drillName.trim()||drillCode.length<4)?"#c8d3dd":"#1a6e2e",border:"none",borderRadius:"4px",padding:"0.85rem",fontSize:"0.95rem",fontWeight:700,color:"#fff",cursor:"pointer"}}>
              {drillSaving ? "Saving…" : "⚡ Save Fluency Drill & Get Code"}
            </button>
          </div>
        )}

        {/* Library */}
        {rightTab==="library"&&(
          <>
            <div style={{background:"#fff",borderBottom:"1px solid #c8d3dd",padding:"0.75rem 1rem",flexShrink:0}}>
              <div style={{fontSize:"0.65rem",fontWeight:700,letterSpacing:"0.12em",color:"#003865"}}>TEST LIBRARY</div>
              <div style={{fontSize:"0.72rem",color:"#888",marginTop:"2px"}}>{savedTests.length} saved test{savedTests.length!==1?"s":""} · Click Load to select questions</div>
            </div>
            <div style={{flex:1,overflowY:"auto",padding:"0.75rem"}}>
              {savedTests.length===0?(
                <div style={{padding:"3rem 1rem",textAlign:"center",color:"#aaa"}}>
                  <div style={{fontSize:"2rem",marginBottom:"0.5rem"}}>📚</div>
                  <div style={{fontWeight:600,color:"#555",marginBottom:"4px"}}>No saved tests yet</div>
                  <div style={{fontSize:"0.82rem"}}>Build a test and click "Save to Library".</div>
                </div>
              ):(
                savedTests.map(t=>(
                  <div key={t.id} style={{background:"#fff",border:"1px solid #c8d3dd",borderRadius:"4px",padding:"0.85rem 1rem",marginBottom:"0.5rem"}}>
                    <div style={{display:"flex",alignItems:"flex-start",gap:"0.75rem"}}>
                      <div style={{flex:1,minWidth:0}}>
                        <div style={{fontSize:"0.9rem",fontWeight:700,color:"#1a1a1a"}}>{t.name}</div>
                        <div style={{fontSize:"0.72rem",color:"#888",marginTop:"2px"}}>
                          {t.type==="drill"
                            ? `⚡ Fluency Drill · ${t.drill_count||10} questions · Saved ${t.saved_at}`
                            : `${t.count} question${t.count!==1?"s":""} · Saved ${t.saved_at}`}
                        </div>
                      </div>
                      <div style={{display:"flex",gap:"0.4rem",flexShrink:0}}>
                        <button onClick={()=>loadSavedTest(t.id)} style={{...S.smBtn,background:"#003865",color:"#fff",borderColor:"#003865",padding:"5px 12px"}}>Load</button>
                        <button onClick={()=>setConfirmDeleteTest(t)} style={{...S.smBtn,color:"#8b1a1a",borderColor:"#f0b8b8",background:"#fdf2f2",padding:"5px 10px"}}>🗑</button>
                      </div>
                    </div>
                    {/* Code badge */}
                    {t.code&&(
                      <div style={{marginTop:"0.6rem",display:"flex",alignItems:"center",gap:"0.5rem",background:"#f0f4f8",borderRadius:"3px",padding:"0.45rem 0.75rem"}}>
                        <span style={{fontSize:"0.62rem",color:"#555",fontWeight:700,letterSpacing:"0.1em"}}>STUDENT CODE</span>
                        <span style={{...S.code,fontSize:"1rem",letterSpacing:"0.2em",color:"#003865"}}>{t.code}</span>
                        <button onClick={()=>navigator.clipboard.writeText(t.code)}
                          style={{...S.smBtn,marginLeft:"auto",padding:"2px 8px",fontSize:"0.68rem"}}>Copy</button>
                      </div>
                    )}
                    {/* Class assignment */}
                    {t.type !== "drill" && (
                      assigningTest === t.id ? (
                        <div style={{marginTop:"0.5rem",background:"#f8fafc",border:"1px solid #c8d3dd",borderRadius:"3px",padding:"0.6rem"}}>
                          <div style={{fontSize:"0.62rem",fontWeight:700,letterSpacing:"0.1em",color:"#555",marginBottom:"6px"}}>ASSIGN TO CLASSES</div>
                          <div style={{display:"flex",flexDirection:"column",gap:"3px",maxHeight:"110px",overflowY:"auto",marginBottom:"6px"}}>
                            {allClasses.map(cls => {
                              const checked = (t.classIds||[]).includes(cls.id);
                              return (
                                <label key={cls.id} onClick={()=>{
                                  const cur = savedTests.find(x=>x.id===t.id);
                                  if (!cur) return;
                                  const ids = checked ? (cur.classIds||[]).filter(x=>x!==cls.id) : [...(cur.classIds||[]), cls.id];
                                  setSavedTests(prev => prev.map(x => x.id===t.id ? {...x, classIds: ids} : x));
                                }} style={{display:"flex",alignItems:"center",gap:"0.5rem",cursor:"pointer",padding:"3px 5px",borderRadius:"3px",background:checked?"#ddeaf7":"transparent",fontSize:"0.8rem"}}>
                                  <div style={{width:"14px",height:"14px",borderRadius:"2px",border:`2px solid ${checked?"#003865":"#c8d3dd"}`,background:checked?"#003865":"#fff",display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0}}>
                                    {checked && <span style={{color:"#fff",fontSize:"0.55rem",fontWeight:900}}>✓</span>}
                                  </div>
                                  <span style={{fontWeight:checked?700:400,color:checked?"#003865":"#333"}}>{cls.name}</span>
                                </label>
                              );
                            })}
                            {allClasses.length===0 && <div style={{color:"#aaa",fontSize:"0.75rem"}}>No classes found.</div>}
                          </div>
                          <div style={{display:"flex",gap:"0.4rem"}}>
                            <button onClick={()=>assignClasses(t.id, savedTests.find(x=>x.id===t.id)?.classIds||[])}
                              style={{...S.smBtn,background:"#003865",color:"#fff",borderColor:"#003865",flex:1,padding:"4px"}}>Save</button>
                            <button onClick={()=>setAssigningTest(null)}
                              style={{...S.smBtn,padding:"4px 8px"}}>Cancel</button>
                          </div>
                        </div>
                      ) : (
                        <div style={{marginTop:"0.5rem",display:"flex",alignItems:"center",gap:"0.5rem"}}>
                          <span style={{fontSize:"0.68rem",color:(t.classIds||[]).length?"#555":"#e67e00"}}>
                            {(t.classIds||[]).length
                              ? `🏫 ${(t.classIds||[]).map(id=>allClasses.find(c=>c.id===id)?.name||id).join(", ")}`
                              : "⚠ No class assigned"}
                          </span>
                          <button onClick={()=>setAssigningTest(t.id)}
                            style={{...S.smBtn,padding:"2px 8px",fontSize:"0.65rem",marginLeft:"auto",whiteSpace:"nowrap"}}>
                            Assign Classes
                          </button>
                        </div>
                      )
                    )}
                  </div>
                ))
              )}
            </div>
          </>
        )}
      </div>

      {/* Modals */}
      {editingQ&&<EditModal question={editingQ} onSave={handleSaveEdit} onClose={()=>setEditingQ(null)}/>}
      {showSaveModal&&<SaveTestModal count={selected.length} currentTitle={testTitle} savedTests={savedTests} onSave={saveTest} onClose={()=>setShowSaveModal(false)}/>}

      {confirmDelete&&(
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,.55)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:1000}}>
          <div style={{background:"#fff",borderRadius:"6px",width:"100%",maxWidth:"380px",overflow:"hidden",boxShadow:"0 8px 32px rgba(0,0,0,.25)"}}>
            <div style={{background:"#8b1a1a",color:"#fff",padding:"0.9rem 1.25rem"}}>
              <div style={{fontSize:"0.6rem",opacity:.75,letterSpacing:"0.12em",marginBottom:"2px"}}>QUESTION BANK</div>
              <div style={{fontSize:"1rem",fontWeight:700}}>Delete Question?</div>
            </div>
            <div style={{padding:"1.25rem"}}>
              <p style={{fontSize:"0.85rem",color:"#333",margin:"0 0 0.75rem",fontFamily:"Georgia,serif",lineHeight:1.5}}><MathText text={confirmDelete.question}/></p>
              <p style={{fontSize:"0.78rem",color:"#888",margin:0}}>This permanently removes the question and cannot be undone.</p>
            </div>
            <div style={{display:"flex",gap:"0.65rem",padding:"0.9rem 1.25rem",borderTop:"1px solid #dde3e9"}}>
              <button onClick={()=>setConfirmDelete(null)} style={{flex:1,background:"#f0f4f8",border:"1px solid #c8d3dd",borderRadius:"3px",padding:"0.65rem",fontSize:"0.85rem",cursor:"pointer",fontWeight:600,color:"#333"}}>Cancel</button>
              <button onClick={()=>deleteQuestion(confirmDelete.id)} style={{flex:1,background:"#8b1a1a",border:"none",borderRadius:"3px",padding:"0.65rem",fontSize:"0.85rem",cursor:"pointer",color:"#fff",fontWeight:700}}>Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
