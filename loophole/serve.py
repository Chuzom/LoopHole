"""Phase 1 visual — ``loophole serve``: a live web "Forge" over the event stream.

Pure stdlib (http.server) + a self-contained Canvas page (no web framework, no CDN
— matches loophole's near-zero-deps ethos). The browser polls a small JSON snapshot
of the goal/tasks/events the store already records and renders THE FORGE: agent
lanes feeding a central VERIFY GATE that flashes PASS (green) / REJECT (red), with a
boundary shield that lights up when a cheat is blocked. The brand, made visible.
"""

from __future__ import annotations

import http.server
import json
import time
from typing import Any, Optional

from .contract import GoalContract


_BOUNDARY_KINDS = ("write_glob_violation", "merge_gate_reject", "soft_fail_closed")


def _event_payload(e: Any) -> Optional[dict]:
    raw = e["payload"] if "payload" in e.keys() else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def build_snapshot(store: Any, goal_id: str) -> Optional[dict]:
    """A JSON-able snapshot the single-run Forge renders from (no engine changes)."""
    g = store.get_goal(goal_id)
    if not g:
        return None
    contract = GoalContract.from_json(g["contract"])
    tasks = [{"id": t.id, "description": t.description, "status": t.status}
             for t in store.tasks_for_goal(goal_id)]
    events = [{"seq": e["seq"], "kind": e["kind"],
               "task_id": (e["task_id"] if "task_id" in e.keys() else None),
               "payload": _event_payload(e), "ts": e["ts"]}
              for e in store.events(goal_id)]
    return {"goal": contract.goal, "status": g["status"], "tasks": tasks, "events": events}


def _run_summary(store: Any, g: Any) -> dict:
    """One compact run card for the fleet view."""
    gid = g["id"]
    try:
        goal_text = GoalContract.from_json(g["contract"]).goal
    except Exception:
        goal_text = "(unparseable contract)"
    counts: dict = {}
    for t in store.tasks_for_goal(gid):
        counts[t.status] = counts.get(t.status, 0) + 1
    verdict, score, rounds, saves = None, 0, 0, 0
    for e in store.events(gid):
        k = e["kind"]
        if k == "verify_run":
            rounds += 1
            p = _event_payload(e)
            verdict = "pass" if (p and p.get("passed")) else "fail"
            if p:
                score = int(p.get("score", 0))
        if k in _BOUNDARY_KINDS:
            saves += 1
    if g["status"] == "done":
        verdict = "pass"
    total = sum(counts.values())
    return {"id": gid, "goal": goal_text, "status": g["status"],
            "running": counts.get("running", 0), "done": counts.get("done", 0),
            "failed": counts.get("failed", 0), "total": total, "verdict": verdict,
            "score": score, "rounds": rounds, "saves": saves,
            "updated_at": g["updated_at"]}


def fleet_snapshot(store: Any) -> list:
    """All runs, newest-updated first — the control-plane fleet view feed."""
    rows = list(store.list_goals())
    rows.sort(key=lambda g: g["updated_at"], reverse=True)
    return [_run_summary(store, g) for g in rows]


INDEX_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>loophole — THE FORGE</title>
<style>
  html,body{margin:0;height:100%;background:#0a0e14;color:#cbd5e1;
    font:14px ui-monospace,SFMono-Regular,Menlo,monospace;overflow:hidden}
  canvas{display:block;position:fixed;inset:0;width:100vw;height:100vh}
  #gl{z-index:0}#c{z-index:1}
  #hud{position:fixed;top:10px;left:14px;right:14px;z-index:2;display:flex;
    justify-content:space-between;pointer-events:none;text-shadow:0 0 8px #000}
  .tag{color:#64748b}
</style></head><body>
<canvas id="gl"></canvas>
<canvas id="c"></canvas>
<div id="hud"><div><a href="/fleet" style="color:#64748b;text-decoration:none;pointer-events:auto">← fleet</a> &nbsp;<b style="color:#e2e8f0">loophole</b> <span class="tag">— THE FORGE</span></div>
<div id="status" class="tag"></div></div>
<script>
const Q=new URLSearchParams(location.search),GOAL=Q.get('goal')||'';
let S={goal:'',status:'',tasks:[],events:[]},t0=performance.now();
let gateFlash=0,gateColor='#475569',gateRGB=[0.97,0.45,0.13],shieldFlash=0,lastSeq=0;

// ---------- transport: SSE with polling fallback ----------
function applyState(ns){ derive(ns); S=ns; }
function poll(){ fetch('/api/state'+(GOAL?('?goal='+encodeURIComponent(GOAL)):''))
  .then(r=>r.ok?r.json():null).then(ns=>{if(ns)applyState(ns);})
  .catch(()=>{}).finally(()=>setTimeout(poll,700)); }
(function connect(){ if(!window.EventSource){return poll();}
  let es; try{ es=new EventSource('/events'+(GOAL?('?goal='+encodeURIComponent(GOAL)):'')); }
  catch(e){ return poll(); }
  es.onmessage=ev=>{ try{applyState(JSON.parse(ev.data));}catch(e){} };
  es.onerror=()=>{ try{es.close();}catch(e){} poll(); }; })();

function derive(ns){ let verdict=null,shield=false,maxseq=lastSeq;
  for(const e of ns.events){ if(e.seq<=lastSeq) continue; maxseq=Math.max(maxseq,e.seq);
    if(e.kind==='verify_run') verdict=(e.payload&&e.payload.passed)?'pass':'fail';
    if(e.kind==='merge_gate_reject') verdict='fail';
    if(['write_glob_violation','merge_gate_reject','soft_fail_closed'].includes(e.kind)) shield=true; }
  if(ns.status==='done') verdict='pass';
  if(verdict){ gateColor=verdict==='pass'?'#22c55e':'#ef4444';
    gateRGB=verdict==='pass'?[0.13,0.77,0.37]:[0.94,0.27,0.27]; gateFlash=1; }
  if(shield) shieldFlash=1; lastSeq=maxseq; }

// ---------- WebGL ember layer (graceful fallback if unavailable) ----------
const glc=document.getElementById('gl');
let gl=null,prog=null,N=1400,uT,uC,uF;
try{ gl=glc.getContext('webgl')||glc.getContext('experimental-webgl'); }catch(e){}
function sh(type,src){const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);return s;}
if(gl){ initGL(); }
function initGL(){
  const vs="attribute vec2 a;attribute float ph;attribute float sp;uniform float uT;"+
    "void main(){float y=mod(a.y+uT*sp*0.06,1.0);float x=a.x+0.03*sin((uT*sp+ph)*6.28);"+
    "gl_Position=vec4(x*2.0-1.0,y*2.0-1.0,0.0,1.0);gl_PointSize=1.5+5.0*(1.0-y);}";
  const fs="precision mediump float;uniform vec3 uC;uniform float uF;"+
    "void main(){vec2 d=gl_PointCoord-0.5;float r=length(d);float a=smoothstep(0.5,0.0,r);"+
    "gl_FragColor=vec4(uC*(1.0+uF*1.5),a*0.5);}";
  prog=gl.createProgram();gl.attachShader(prog,sh(gl.VERTEX_SHADER,vs));
  gl.attachShader(prog,sh(gl.FRAGMENT_SHADER,fs));gl.linkProgram(prog);gl.useProgram(prog);
  const A=new Float32Array(N*2),PH=new Float32Array(N),SP=new Float32Array(N);
  for(let i=0;i<N;i++){A[i*2]=Math.random();A[i*2+1]=Math.random();PH[i]=Math.random();SP[i]=0.4+Math.random();}
  function buf(data,loc,size){const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);
    gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);const l=gl.getAttribLocation(prog,loc);
    gl.enableVertexAttribArray(l);gl.vertexAttribPointer(l,size,gl.FLOAT,false,0,0);}
  buf(A,'a',2);buf(PH,'ph',1);buf(SP,'sp',1);
  uT=gl.getUniformLocation(prog,'uT');uC=gl.getUniformLocation(prog,'uC');uF=gl.getUniformLocation(prog,'uF');
  gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE);
}
function drawGL(t){ if(!gl||!prog) return; gl.viewport(0,0,glc.width,glc.height);
  gl.clearColor(0.04,0.055,0.08,1);gl.clear(gl.COLOR_BUFFER_BIT);gl.useProgram(prog);
  // ember color: forge-orange at rest, tinted toward the gate color on a flash
  const o=[0.97,0.45,0.13],f=Math.min(1,gateFlash);
  gl.uniform3f(uC,o[0]+(gateRGB[0]-o[0])*f,o[1]+(gateRGB[1]-o[1])*f,o[2]+(gateRGB[2]-o[2])*f);
  gl.uniform1f(uT,t);gl.uniform1f(uF,gateFlash);gl.drawArrays(gl.POINTS,0,N); }

// ---------- 2D overlay: gate, lanes, text ----------
const cv=document.getElementById('c'),x=cv.getContext('2d');
function fit(){const d=devicePixelRatio||1;for(const el of [cv,glc]){el.width=innerWidth*d;el.height=innerHeight*d;}
  x.setTransform(d,0,0,d,0,0);}
addEventListener('resize',fit);fit();
function lane(i,n){return 110+i*Math.min(64,(innerHeight-260)/Math.max(n,1));}
function draw(){const W=innerWidth,H=innerHeight,t=(performance.now()-t0)/1000;
  drawGL(t); x.clearRect(0,0,W,H);
  x.fillStyle='#e2e8f0';x.font='600 18px ui-monospace,monospace';
  x.fillText('GOAL  '+(S.goal||'…'),20,64);
  const running=S.tasks.filter(t=>t.status==='running');
  const done=S.tasks.filter(t=>t.status==='done').length;
  const failed=S.tasks.filter(t=>t.status==='failed').length;
  x.font='13px ui-monospace,monospace';x.fillStyle='#94a3b8';x.fillText('THE SWARM',20,98);
  running.forEach((tk,i)=>{const y=lane(i,running.length);
    const pulse=0.55+0.45*Math.sin(i+performance.now()/300);
    x.beginPath();x.arc(40,y,7,0,7);x.fillStyle='rgba(56,189,248,'+pulse+')';x.fill();
    x.fillStyle='#38bdf8';x.fillText('⚙ '+(tk.description||'').slice(0,42),58,y+4);});
  if(!running.length){x.fillStyle='#475569';x.fillText('· no agents active',58,128);}
  const gx=W*0.68,gy=H*0.5,gw=250,gh=124; gateFlash*=0.94; shieldFlash*=0.95;
  x.save();x.shadowColor=gateColor;x.shadowBlur=18+70*gateFlash;
  x.strokeStyle=gateColor;x.lineWidth=3;x.strokeRect(gx-gw/2,gy-gh/2,gw,gh);x.restore();
  x.fillStyle=gateColor;x.textAlign='center';x.font='700 22px ui-monospace,monospace';
  x.fillText('VERIFY GATE',gx,gy-12);
  const verd=S.status==='done'?'✓ DONE':(gateColor==='#22c55e'?'✓ PASS':(gateColor==='#ef4444'?'✗ REJECT':'…'));
  x.font='700 28px ui-monospace,monospace';x.fillText(verd,gx,gy+26);x.textAlign='left';
  const saves=S.events.filter(e=>['write_glob_violation','merge_gate_reject','soft_fail_closed'].includes(e.kind)).length;
  x.fillStyle='rgba(34,197,94,'+(0.35+0.65*shieldFlash)+')';x.fillText('⛨ BOUNDARY  held ×'+saves,20,H-70);
  const vr=[...S.events].reverse().find(e=>e.kind==='verify_run');
  const score=vr&&vr.payload?Math.round(vr.payload.score||0):0;
  const rounds=S.events.filter(e=>e.kind==='verify_run').length;
  x.fillStyle='#94a3b8';
  x.fillText('merged '+done+'   failed '+failed+'   round '+rounds+'   score '+score+'   ['+(S.status||'…')+']',20,H-44);
  x.fillStyle='#64748b';x.fillText("'done' means the verifier passed — not that an LLM said so.",20,H-20);
  document.getElementById('status').textContent=(S.status||'').toUpperCase();
  requestAnimationFrame(draw);}
draw();
</script></body></html>
"""


FLEET_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>loophole — FLEET</title>
<style>
  :root{--bg:#0a0e14}
  html,body{margin:0;min-height:100%;background:var(--bg);color:#cbd5e1;
    font:14px ui-monospace,SFMono-Regular,Menlo,monospace}
  header{padding:18px 22px;display:flex;justify-content:space-between;align-items:baseline}
  h1{font-size:18px;margin:0;color:#e2e8f0}.tag{color:#64748b}
  #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
    gap:14px;padding:0 22px 28px}
  .card{background:#0f1620;border:1px solid #1e293b;border-left-width:5px;border-radius:10px;
    padding:14px 16px;cursor:pointer;transition:transform .08s,box-shadow .15s}
  .card:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.4)}
  .goal{color:#e2e8f0;font-size:14px;margin:0 0 10px;line-height:1.35;
    display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .row{display:flex;justify-content:space-between;color:#94a3b8;font-size:12px;margin-top:6px}
  .pill{font-size:11px;padding:2px 8px;border-radius:999px;font-weight:700}
  .bar{height:6px;background:#1e293b;border-radius:4px;overflow:hidden;margin:8px 0 4px}
  .bar>i{display:block;height:100%;background:#22c55e}
  .empty{color:#475569;padding:0 22px}
  .verd{font-weight:700}
</style></head><body>
<header><h1>loophole <span class="tag">— FLEET</span></h1>
  <span class="tag" id="count"></span></header>
<div id="grid"></div><div id="empty" class="empty"></div>
<script>
const C={running:'#f59e0b',done:'#22c55e',failed:'#ef4444',paused:'#64748b'};
function pct(d,t){return t?Math.round(100*d/t):0;}
function card(r){
  const col=C[r.status]||'#475569';
  const verd=r.status==='done'?'✓ DONE':(r.verdict==='pass'?'✓ pass':(r.verdict==='fail'?'✗ reject':'…'));
  const vc=r.verdict==='pass'||r.status==='done'?'#22c55e':(r.verdict==='fail'?'#ef4444':'#64748b');
  const d=document.createElement('div');d.className='card';d.style.borderLeftColor=col;
  d.onclick=()=>location.href='/run?goal='+encodeURIComponent(r.id);
  d.innerHTML=
    '<p class="goal">'+esc(r.goal||'(no goal)')+'</p>'+
    '<div class="row"><span class="pill" style="background:'+col+'22;color:'+col+'">'+r.status+'</span>'+
      '<span class="verd" style="color:'+vc+'">VERIFY GATE '+verd+'</span></div>'+
    '<div class="bar"><i style="width:'+pct(r.done,r.total)+'%;background:'+col+'"></i></div>'+
    '<div class="row"><span>'+r.done+'/'+r.total+' merged · '+r.running+' running · '+r.failed+' failed</span>'+
      '<span>⛨ '+r.saves+' · r'+r.rounds+'</span></div>'+
    '<div class="row tag"><span>'+r.id+'</span><span>score '+r.score+'</span></div>';
  return d;}
function esc(s){const e=document.createElement('div');e.textContent=s;return e.innerHTML;}
async function refresh(){try{const r=await fetch('/api/fleet');if(!r.ok)return;const fleet=await r.json();
  const grid=document.getElementById('grid');grid.innerHTML='';
  document.getElementById('count').textContent=fleet.length+' run'+(fleet.length===1?'':'s');
  document.getElementById('empty').textContent=fleet.length?'':'no runs yet — start one with `loophole run`';
  fleet.forEach(r=>grid.appendChild(card(r)));}catch(e){}finally{setTimeout(refresh,1500);}}
refresh();
</script></body></html>
"""


def make_handler(store: Any, default_goal: Optional[str] = None):
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console quiet
            pass

        def _send(self, code: int, ctype: str, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def _goal(self):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            return (q.get("goal", [None])[0]) or default_goal

        def do_GET(self):
            from urllib.parse import urlparse
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                # `loophole serve <goal>` opens that run; bare `serve` opens the fleet.
                page = INDEX_HTML if default_goal else FLEET_HTML
                self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
            elif path == "/fleet":
                self._send(200, "text/html; charset=utf-8", FLEET_HTML.encode("utf-8"))
            elif path == "/run":
                self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
            elif path == "/api/fleet":
                self._send(200, "application/json",
                           json.dumps(fleet_snapshot(store)).encode("utf-8"))
            elif path == "/api/state":
                snap = build_snapshot(store, self._goal()) if self._goal() else None
                if snap is None:
                    self._send(404, "application/json", b'{"error":"no such goal"}')
                else:
                    self._send(200, "application/json", json.dumps(snap).encode("utf-8"))
            elif path == "/events":
                self._stream_events(self._goal())
            else:
                self._send(404, "text/plain", b"not found")

        def _stream_events(self, goal_id, interval: float = 0.7, max_ticks: int = 0):
            """Server-Sent Events: push the full snapshot each tick (simple + robust;
            the page just replaces its state). Stops when the client disconnects.
            max_ticks>0 bounds the loop (used by tests)."""
            if not goal_id or build_snapshot(store, goal_id) is None:
                self._send(404, "text/plain", b"no such goal")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            ticks = 0
            try:
                while True:
                    snap = build_snapshot(store, goal_id)
                    if snap is None:
                        break
                    self.wfile.write(("data: " + json.dumps(snap) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    ticks += 1
                    if max_ticks and ticks >= max_ticks:
                        break
                    time.sleep(interval)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # client went away — end the stream

    return _Handler


def make_server(store: Any, default_goal: Optional[str] = None,
                host: str = "127.0.0.1", port: int = 8765):
    return http.server.ThreadingHTTPServer((host, port), make_handler(store, default_goal))


def serve(store: Any, goal_id: Optional[str] = None, host: str = "127.0.0.1",
          port: int = 8765, open_browser: bool = True) -> None:
    """Serve the fleet (no goal_id) or a single run's Forge (goal_id pins `/`)."""
    srv = make_server(store, goal_id, host, port)
    url = "http://{}:{}/".format(host, srv.server_address[1])
    label = "FLEET" if not goal_id else "THE FORGE"
    print("loophole serve ({}) → {}   (Ctrl-C to stop)".format(label, url))
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
