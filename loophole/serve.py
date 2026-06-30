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
from typing import Any, Optional

from .contract import GoalContract


def build_snapshot(store: Any, goal_id: str) -> Optional[dict]:
    """A JSON-able snapshot the front end renders from (no engine changes)."""
    g = store.get_goal(goal_id)
    if not g:
        return None
    contract = GoalContract.from_json(g["contract"])
    tasks = [{"id": t.id, "description": t.description, "status": t.status}
             for t in store.tasks_for_goal(goal_id)]
    events = []
    for e in store.events(goal_id):
        payload = None
        raw = e["payload"] if "payload" in e.keys() else None
        if raw:
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                payload = None
        events.append({
            "seq": e["seq"], "kind": e["kind"],
            "task_id": (e["task_id"] if "task_id" in e.keys() else None),
            "payload": payload, "ts": e["ts"]})
    return {"goal": contract.goal, "status": g["status"], "tasks": tasks, "events": events}


INDEX_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>loophole — THE FORGE</title>
<style>
  html,body{margin:0;height:100%;background:#0a0e14;color:#cbd5e1;
    font:14px ui-monospace,SFMono-Regular,Menlo,monospace;overflow:hidden}
  #c{display:block;width:100vw;height:100vh}
  #hud{position:fixed;top:10px;left:14px;right:14px;display:flex;justify-content:space-between;
    pointer-events:none;text-shadow:0 0 8px #000}
  .tag{color:#64748b}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud"><div><b style="color:#e2e8f0">loophole</b> <span class="tag">— THE FORGE</span></div>
<div id="status" class="tag"></div></div>
<script>
const cv=document.getElementById('c'),x=cv.getContext('2d');
const Q=new URLSearchParams(location.search);const GOAL=Q.get('goal')||'';
let S={goal:'',status:'',tasks:[],events:[]};let t0=performance.now();
let gateFlash=0,gateColor='#475569',shieldFlash=0,lastSeq=0;
function fit(){cv.width=innerWidth*devicePixelRatio;cv.height=innerHeight*devicePixelRatio;
  x.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);}
addEventListener('resize',fit);fit();
async function poll(){try{const r=await fetch('/api/state'+(GOAL?('?goal='+encodeURIComponent(GOAL)):''));
  if(r.ok){const ns=await r.json();derive(ns);S=ns;}}catch(e){}setTimeout(poll,600);}
function derive(ns){
  // detect newest verdict / boundary event for the gate + shield animations
  let verdict=null,shield=false,maxseq=lastSeq;
  for(const e of ns.events){ if(e.seq<=lastSeq) continue; maxseq=Math.max(maxseq,e.seq);
    if(e.kind==='verify_run'){verdict=e.payload&&e.payload.passed?'pass':'fail';}
    if(e.kind==='merge_gate_reject'){verdict='fail';}
    if(e.kind==='write_glob_violation'||e.kind==='merge_gate_reject'||e.kind==='soft_fail_closed'){shield=true;}
  }
  if(ns.status==='done') verdict='pass';
  if(verdict){gateColor=verdict==='pass'?'#22c55e':'#ef4444';gateFlash=1;}
  if(shield) shieldFlash=1;
  lastSeq=maxseq;
}
function lane(i,n){return 90+i*Math.min(70,(innerHeight-220)/Math.max(n,1));}
function draw(){const W=innerWidth,H=innerHeight,t=(performance.now()-t0)/1000;
  // forge background glow
  x.fillStyle='#0a0e14';x.fillRect(0,0,W,H);
  const g=x.createRadialGradient(W*0.66,H*0.5,40,W*0.66,H*0.5,Math.max(W,H)*0.6);
  g.addColorStop(0,'rgba(249,115,22,'+(0.07+0.03*Math.sin(t*2))+')');g.addColorStop(1,'rgba(0,0,0,0)');
  x.fillStyle=g;x.fillRect(0,0,W,H);
  // goal
  x.fillStyle='#e2e8f0';x.font='600 18px ui-monospace,monospace';
  x.fillText('GOAL  '+(S.goal||'…'),20,64);
  // swarm lanes (running + recent)
  const running=S.tasks.filter(t=>t.status==='running');
  const done=S.tasks.filter(t=>t.status==='done').length;
  const failed=S.tasks.filter(t=>t.status==='failed').length;
  x.font='13px ui-monospace,monospace';x.fillStyle='#94a3b8';x.fillText('THE SWARM',20,98);
  running.forEach((t,i)=>{const y=lane(i,running.length)+20;
    const pulse=0.6+0.4*Math.sin(t0/1+i+performance.now()/300);
    x.beginPath();x.arc(40,y,7,0,7);x.fillStyle='rgba(56,189,248,'+pulse+')';x.fill();
    x.fillStyle='#38bdf8';x.fillText('⚙ '+t.description.slice(0,42),58,y+4);
    // merge-train particle toward the gate
    const px=58+((performance.now()/12+i*120)%(W*0.5-120));
    x.fillStyle='rgba(96,165,250,0.5)';x.fillRect(px+220,y-2,8,4);});
  if(!running.length){x.fillStyle='#475569';x.fillText('· no agents active',58,128);}
  // THE GATE
  const gx=W*0.66,gy=H*0.5,gw=240,gh=120;
  gateFlash*=0.94; shieldFlash*=0.95;
  const glow=20+60*gateFlash;
  x.save();x.shadowColor=gateColor;x.shadowBlur=glow;
  x.strokeStyle=gateColor;x.lineWidth=3;x.strokeRect(gx-gw/2,gy-gh/2,gw,gh);
  x.restore();
  x.fillStyle=gateColor;x.font='700 22px ui-monospace,monospace';x.textAlign='center';
  x.fillText('VERIFY GATE',gx,gy-12);
  const verd=S.status==='done'?'✓ DONE':(gateColor==='#22c55e'?'✓ PASS':(gateColor==='#ef4444'?'✗ REJECT':'…'));
  x.font='700 28px ui-monospace,monospace';x.fillText(verd,gx,gy+26);x.textAlign='left';
  // boundary shield
  const saves=S.events.filter(e=>['write_glob_violation','merge_gate_reject','soft_fail_closed'].includes(e.kind)).length;
  x.fillStyle='rgba(34,197,94,'+(0.35+0.65*shieldFlash)+')';
  x.fillText('⛨ BOUNDARY  held ×'+saves,20,H-70);
  // stats
  const vr=[...S.events].reverse().find(e=>e.kind==='verify_run');
  const score=vr&&vr.payload?Math.round(vr.payload.score||0):0;
  const rounds=S.events.filter(e=>e.kind==='verify_run').length;
  x.fillStyle='#94a3b8';
  x.fillText('merged '+done+'   failed '+failed+'   round '+rounds+'   score '+score+'   ['+(S.status||'…')+']',20,H-44);
  x.fillStyle='#64748b';x.fillText("'done' means the verifier passed — not that an LLM said so.",20,H-20);
  document.getElementById('status').textContent=(S.status||'').toUpperCase();
  requestAnimationFrame(draw);}
poll();draw();
</script></body></html>
"""


def make_handler(store: Any, goal_id: str):
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

        def do_GET(self):
            from urllib.parse import urlparse
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
            elif path == "/api/state":
                snap = build_snapshot(store, goal_id)
                if snap is None:
                    self._send(404, "application/json", b'{"error":"no such goal"}')
                else:
                    self._send(200, "application/json", json.dumps(snap).encode("utf-8"))
            else:
                self._send(404, "text/plain", b"not found")

    return _Handler


def make_server(store: Any, goal_id: str, host: str = "127.0.0.1", port: int = 8765):
    return http.server.ThreadingHTTPServer((host, port), make_handler(store, goal_id))


def serve(store: Any, goal_id: str, host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = True) -> None:
    srv = make_server(store, goal_id, host, port)
    url = "http://{}:{}/".format(host, srv.server_address[1])
    print("loophole serve → " + url + "   (Ctrl-C to stop)")
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
