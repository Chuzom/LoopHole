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
  html,body{margin:0;height:100%;background:#0a0e1a;color:#cbd5e1;
    font:14px ui-monospace,SFMono-Regular,Menlo,monospace;overflow:hidden}
  #c{display:block;width:100vw;height:100vh}
  #hud{position:fixed;top:10px;left:14px;right:14px;display:flex;justify-content:space-between;
    pointer-events:none;text-shadow:0 0 8px #000;z-index:2}
  .tag{color:#64748b}
  a{color:#64748b;text-decoration:none;pointer-events:auto}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud">
  <div><a href="/fleet">&#8592; fleet</a> &nbsp;<b style="color:#e2e8f0">loophole</b> <span class="tag">— THE FORGE</span></div>
  <div id="status" class="tag"></div>
</div>
<script>
const cv=document.getElementById('c'),x=cv.getContext('2d');
const Q=new URLSearchParams(location.search),GOAL=Q.get('goal')||'';
let S={goal:'',status:'',tasks:[],events:[]};
let gateColor='#475569',gateFlash=0,frightenedUntil=0,happyUntil=0,lastSeq=0,sparkles=[];

// ---- transport: SSE with polling fallback (data layer unchanged) ----
function applyState(ns){derive(ns);S=ns;}
function poll(){fetch('/api/state'+(GOAL?('?goal='+encodeURIComponent(GOAL)):''))
  .then(r=>r.ok?r.json():null).then(ns=>{if(ns)applyState(ns);})
  .catch(()=>{}).finally(()=>setTimeout(poll,700));}
(function connect(){if(!window.EventSource)return poll();
  let es;try{es=new EventSource('/events'+(GOAL?('?goal='+encodeURIComponent(GOAL)):''));}
  catch(e){return poll();}
  es.onmessage=ev=>{try{applyState(JSON.parse(ev.data));}catch(e){}};
  es.onerror=()=>{try{es.close();}catch(e){}poll();};})();

function derive(ns){let verdict=null,save=false,maxseq=lastSeq;
  for(const e of ns.events){if(e.seq<=lastSeq)continue;maxseq=Math.max(maxseq,e.seq);
    if(e.kind==='verify_run')verdict=(e.payload&&e.payload.passed)?'pass':'fail';
    if(e.kind==='merge_gate_reject')verdict='fail';
    if(['write_glob_violation','merge_gate_reject','soft_fail_closed'].includes(e.kind))save=true;}
  if(ns.status==='done')verdict='pass';
  const now=performance.now();
  if(verdict==='pass'){gateColor='#22c55e';gateFlash=1;happyUntil=now+2200;}
  if(verdict==='fail'){gateColor='#ef4444';gateFlash=1;frightenedUntil=now+2600;}
  if(save){for(let i=0;i<8;i++)sparkles.push({a:1,init:true});}
  lastSeq=maxseq;}

// ---- the Pac-Man swarm ----
const PAL=['#ff5d5d','#ffb8ff','#5de0ff','#ffb852','#a0ff7a','#c39bff','#ffe14d'];
let ghosts=[],pac=null;
function rnd(a,b){return a+Math.random()*(b-a);}
function field(){return {x0:60,y0:150,x1:innerWidth*0.58,y1:innerHeight-120};}
function spawnGhost(i){const f=field();return{
  x:rnd(f.x0,f.x1),y:rnd(f.y0,f.y1),vx:rnd(-45,45),vy:rnd(-32,32),
  color:PAL[i%PAL.length],ph:Math.random()*6.28,blink:0,next:rnd(0.5,1.8)};}
function syncGhosts(n){while(ghosts.length<n)ghosts.push(spawnGhost(ghosts.length));
  if(ghosts.length>n)ghosts.length=n;}

function drawGhost(g,frightened,dt){const f=field();
  if((g.next-=dt)<=0){g.vx=rnd(-60,60);g.vy=rnd(-42,42);g.next=rnd(0.5,1.9);
    if(Math.random()<0.5)g.blink=0.16;}
  g.x+=g.vx*dt;g.y+=g.vy*dt;g.ph+=dt*6;
  if(g.x<f.x0){g.x=f.x0;g.vx=Math.abs(g.vx);}if(g.x>f.x1){g.x=f.x1;g.vx=-Math.abs(g.vx);}
  if(g.y<f.y0){g.y=f.y0;g.vy=Math.abs(g.vy);}if(g.y>f.y1){g.y=f.y1;g.vy=-Math.abs(g.vy);}
  const r=16,bob=Math.sin(g.ph)*2.2,gx=g.x,gy=g.y+bob;
  x.fillStyle=frightened?'#2a44ff':g.color;
  x.beginPath();x.arc(gx,gy-2,r,Math.PI,0);x.lineTo(gx+r,gy+r);
  const bumps=4,w=2*r/bumps;
  for(let i=0;i<bumps;i++){const bx=gx+r-w*i;x.lineTo(bx,gy+r);x.lineTo(bx-w/2,gy+r-6);}
  x.lineTo(gx-r,gy-2);x.closePath();x.fill();
  const dir=Math.atan2(g.vy,g.vx),ex=Math.cos(dir),ey=Math.sin(dir);
  if(g.blink>0)g.blink-=dt;const blink=g.blink>0;
  for(const s of [-1,1]){const cx=gx+s*r*0.4,cy=gy-r*0.12;
    x.fillStyle=frightened?'#ffd0e6':'#fff';x.beginPath();
    x.ellipse(cx,cy,r*0.28,blink?r*0.05:r*0.34,0,0,6.3);x.fill();
    if(!blink){x.fillStyle=frightened?'#fff':'#2233aa';x.beginPath();
      x.arc(cx+ex*r*0.12,cy+ey*r*0.16,r*0.13,0,6.3);x.fill();}}}

function drawPac(dt){const f=field();if(!pac)pac={x:f.x0,goRight:true};
  pac.x+=(pac.goRight?1:-1)*72*dt;
  if(pac.x>f.x1)pac.goRight=false;if(pac.x<f.x0)pac.goRight=true;
  const r=18,gy=(f.y0+f.y1)/2+Math.sin(performance.now()/650)*22;
  x.fillStyle='rgba(148,163,184,0.22)';
  for(let px=f.x0;px<f.x1;px+=34){x.beginPath();x.arc(px,gy,2.4,0,6.3);x.fill();}
  const mouth=Math.abs(Math.sin(performance.now()/110))*0.32+0.04;
  x.save();x.translate(pac.x,gy);x.rotate(pac.goRight?0:Math.PI);
  x.fillStyle='#ffe14d';x.beginPath();x.moveTo(0,0);
  x.arc(0,0,r,mouth,6.2832-mouth);x.closePath();x.fill();x.restore();}

function fit(){const d=devicePixelRatio||1;cv.width=innerWidth*d;cv.height=innerHeight*d;
  x.setTransform(d,0,0,d,0,0);}
addEventListener('resize',fit);fit();
let lastT=performance.now();
function draw(){const W=innerWidth,H=innerHeight,now=performance.now();
  const dt=Math.min(0.05,(now-lastT)/1000);lastT=now;
  x.fillStyle='#0a0e1a';x.fillRect(0,0,W,H);
  x.fillStyle='rgba(56,80,140,0.10)';
  for(let gx=30;gx<W;gx+=28)for(let gy=160;gy<H-90;gy+=28)x.fillRect(gx,gy,2,2);
  x.fillStyle='#e2e8f0';x.font='600 18px ui-monospace,monospace';
  x.fillText('GOAL  '+(S.goal||'…'),20,64);
  x.fillStyle='#94a3b8';x.font='13px ui-monospace,monospace';x.fillText('THE SWARM',20,104);

  const running=S.tasks.filter(t=>t.status==='running').length;
  const done=S.tasks.filter(t=>t.status==='done').length;
  const failed=S.tasks.filter(t=>t.status==='failed').length;
  const frightened=now<frightenedUntil;
  syncGhosts(running);drawPac(dt);
  for(const g of ghosts)drawGhost(g,frightened,dt);
  if(!running){x.fillStyle='#475569';x.fillText('· no agents active',60,134);}

  const f=field();
  for(const s of sparkles){if(s.init){s.x=rnd(f.x0,f.x1);s.y=rnd(f.y0,f.y1);s.init=false;}
    s.a-=dt*0.8;x.fillStyle='rgba(255,225,77,'+Math.max(0,s.a)+')';
    x.beginPath();x.arc(s.x,s.y,4+5*(1-s.a),0,6.3);x.fill();}
  sparkles=sparkles.filter(s=>s.a>0);

  const Gx=W*0.80,Gy=H*0.5,gw=230,gh=120;gateFlash*=0.94;
  x.save();x.shadowColor=gateColor;x.shadowBlur=16+70*gateFlash;
  x.strokeStyle=gateColor;x.lineWidth=3;x.strokeRect(Gx-gw/2,Gy-gh/2,gw,gh);x.restore();
  x.fillStyle=gateColor;x.textAlign='center';x.font='700 22px ui-monospace,monospace';
  x.fillText('VERIFY GATE',Gx,Gy-12);
  const verd=S.status==='done'?'✓ DONE':(gateColor==='#22c55e'?'✓ PASS':(gateColor==='#ef4444'?'✗ REJECT':'…'));
  x.font='700 28px ui-monospace,monospace';x.fillText(verd,Gx,Gy+26);
  if(now<happyUntil){x.font='22px ui-monospace,monospace';x.fillText('\u{1F389}',Gx,Gy-52);}
  x.textAlign='left';

  const saves=S.events.filter(e=>['write_glob_violation','merge_gate_reject','soft_fail_closed'].includes(e.kind)).length;
  x.fillStyle='#facc15';x.fillText('⛨ BOUNDARY held ×'+saves,20,H-70);
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
