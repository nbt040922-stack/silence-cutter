from __future__ import annotations

import json
import os
import random
import secrets
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


HOST = os.environ.get("SILENCE_CUTTER_LAN_HOST", "0.0.0.0")
PORT = int(os.environ.get("SILENCE_CUTTER_LAN_PORT", "8780"))
INPUT_ROOT = Path(os.environ.get("SILENCE_CUTTER_LAN_INPUT_ROOT", "")).expanduser() if os.environ.get("SILENCE_CUTTER_LAN_INPUT_ROOT") else None
YTDOWNLOAD_BASE_URL = os.environ.get("YTDOWNLOAD_BRIDGE_URL", "http://127.0.0.1:8790").rstrip("/")
SILENCE_BASE_URL = os.environ.get("SILENCE_CUTTER_BRIDGE_URL", "http://127.0.0.1:8791").rstrip("/")
DISCOVERY_PAUSE_MIN_SECONDS = 60
DISCOVERY_PAUSE_MAX_SECONDS = 120


def _load_token() -> str:
    configured = os.environ.get("SILENCE_CUTTER_LAN_TOKEN", "").strip()
    if configured:
        return configured
    configured_root = os.environ.get("SILENCE_CUTTER_DATA_DIR", "").strip()
    if configured_root:
        root = Path(configured_root).expanduser()
    else:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        root = (Path(local_app_data) / "SilenceCutter") if local_app_data else (Path.home() / ".silence_cutter")
    path = root / "lan-token"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    return token


TOKEN = _load_token()

UI_HTML = """<!doctype html><meta charset='utf-8'><title>Silence Cutter LAN</title>
<style>body{font:14px system-ui;max-width:1500px;margin:24px auto;padding:0 16px;background:#0b1220;color:#e8eefb}main{background:transparent;padding:0}.topbar{display:flex;align-items:center;justify-content:space-between;margin:0 6px 18px}.topbar h1{font-size:18px;margin:0}.topbar p{font-size:12px;color:#35d889;margin:0}.create-card,.jobs-card{background:#172236;border:1px solid #253552;border-radius:8px;padding:18px;margin-bottom:16px;box-shadow:0 8px 24px #05091466}.create-card{border-left:2px solid #2f7df6}.create-card h2,.jobs-heading h2{font-size:13px;margin:0 0 14px;color:#f2f6ff}.create-card label{display:block;font-size:11px;color:#c7d1e4;margin:8px 0 4px}.create-card input{padding:9px;background:#111a2a;border-color:#2d3d58;color:#eef2ff}.button-row{display:flex;gap:8px;margin-top:10px}.button-row button{font-size:12px;padding:8px 12px;border:1px solid #3d5272}.button-row .secondary{background:#122033}.button-row .danger{background:#7d2632}.jobs-heading{display:flex;justify-content:space-between;align-items:center}.jobs-heading h2{margin-bottom:0}.jobs-grid{width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;text-align:center;background:#111b2b;border:1px solid #2a3a56;border-radius:6px;overflow:hidden}.jobs-grid th{font-size:10px;font-weight:600;color:#dbe5f7;background:#122039}.jobs-grid th,.jobs-grid td{padding:10px 8px;border-bottom:1px solid #293750;text-align:center;vertical-align:middle}.jobs-grid tr:last-child td{border-bottom:0}.jobs-grid tbody tr:hover{background:#182944}.jobs-grid button{font-size:11px;padding:6px 10px}.output-path{word-break:break-word;overflow-wrap:anywhere;white-space:pre-wrap;vertical-align:top}.ok{color:#35d889}.err{color:#ff7885}</style>
<main><div class='topbar'><h1>Silence Cutter — LAN Jobs</h1><p id='health'>Đang kiểm tra…</p></div>
<section class='create-card'><h2>Tạo job mới</h2>
<label>Token LAN</label><input id='token' type='password' autocomplete='off' placeholder='Dán token từ máy xử lý'>
<label>URL YouTube</label><input id='url' placeholder='https://www.youtube.com/watch?v=...'><div class='button-row'><button onclick='submitJob()'>➤ Gửi job</button> <button class='secondary' onclick='loadJobs()'>↻ Làm mới</button> <button class='danger' onclick='clearHistory()'>▣ Xoá lịch sử</button></div></section>
<p id='message'></p><section class='jobs-card'><div class='jobs-heading'><h2>Jobs</h2><span id='updated'></span></div><pre id='jobs'>Chưa tải.</pre></section></main>
<script>
const $=id=>document.getElementById(id), auth=()=>({Authorization:'Bearer '+$('token').value});
async function loadJobs(){try{let r=await fetch('/jobs',{headers:auth()});let d=await r.json();if(!r.ok)throw Error(d.error);$('jobs').textContent=JSON.stringify(d.jobs||[],null,2);$('message').className='ok';$('message').textContent='Đã tải '+(d.jobs||[]).length+' job.'}catch(e){$('message').className='err';$('message').textContent=e.message}}
async function submitJob(){try{let r=await fetch('/jobs',{method:'POST',headers:{...auth(),'Content-Type':'application/json'},body:JSON.stringify({url:$('url').value})});let d=await r.json();if(!r.ok)throw Error(d.error);$('message').className='ok';$('message').textContent='Đã tạo job: '+d.job_id;loadJobs()}catch(e){$('message').className='err';$('message').textContent=e.message}}
async function clearHistory(){if(!confirm('Xoá tất cả job đã kết thúc? Job đang chạy sẽ được giữ lại.'))return;try{let r=await fetch('/jobs/history',{method:'DELETE',headers:auth()});let d=await r.json();if(!r.ok)throw Error(d.error);$('message').className='ok';$('message').textContent='Đã xoá '+d.removed+' job lịch sử.';loadJobs()}catch(e){$('message').className='err';$('message').textContent=e.message}}
fetch('/health').then(r=>r.json()).then(d=>$('health').textContent='API: '+d.status+' • Cổng '+d.port).catch(()=>$('health').textContent='API không kết nối');
function renderJobs(items){$('jobs').style.whiteSpace='normal';$('jobs').innerHTML=(items||[]).map(j=>{const p=Math.max(0,Math.min(100,Number(j.progress||0)));const title=j.display_name||j.title||j.url||j.id;const error=j.error||j.formatter_error||j.download_error_code||'';const output=j.output_folder||j.output_path||'';return `<div style="background:#0e1520;padding:12px;margin:8px 0;border-radius:8px"><b>${title}</b><br>Trạng thái: ${j.status||'—'} • Giai đoạn: ${j.stage||'—'} • Tiến độ: ${p.toFixed(0)}%<div style="height:7px;background:#38465b;border-radius:5px;margin-top:7px"><div style="height:7px;width:${p}%;background:#46c77a;border-radius:5px"></div></div>${error?`<br><span style="color:#ff8888">Lỗi: ${error}</span>`:''}${output?`<br>Kết quả: ${output}`:''}</div>`}).join('')||'Chưa có job.'}
window.loadJobs=async function(){try{let r=await fetch('/jobs',{headers:auth()});let d=await r.json();if(!r.ok)throw Error(d.error);renderJobs(d.jobs);$('message').className='ok';$('message').textContent='Cập nhật '+new Date().toLocaleTimeString();if($('updated'))$('updated').textContent='↻ Cập nhật: '+new Date().toLocaleTimeString()}catch(e){$('message').className='err';$('message').textContent=e.message}}
function renderJobs(items){$('jobs').style.whiteSpace='normal';$('jobs').innerHTML=(items||[]).map(j=>{const p=Math.max(0,Math.min(100,Number(j.progress||0)));const title=j.display_name||j.title||j.url||j.id;const error=j.error||j.formatter_error||j.download_error_code||'';const output=j.output_folder||j.output_path||'';const terminal=['DONE','FAILED','CANCELLED','INTERRUPTED'].includes(j.status);const action=terminal?`<button onclick="deleteJob('${j.id}')" style="background:#a63b3b">Xoá</button>`:`<button onclick="cancelJob('${j.id}')" style="background:#8a5a20">Huỷ</button>`;return `<div style="background:#0e1520;padding:12px;margin:8px 0;border-radius:8px"><b>${title}</b><br>Trạng thái: ${j.status||'—'} • Giai đoạn: ${j.stage||'—'} • Tiến độ: ${p.toFixed(0)}%<div style="height:7px;background:#38465b;border-radius:5px;margin-top:7px"><div style="height:7px;width:${p}%;background:#46c77a;border-radius:5px"></div></div>${error?`<br><span style="color:#ff8888">Lỗi: ${error}</span>`:''}${output?`<br>Kết quả: ${output}`:''}<br>${action}</div>`}).join('')||'Chưa có job.'}
async function cancelJob(id){if(!confirm('Huỷ job này?'))return;try{let r=await fetch('/jobs/'+encodeURIComponent(id)+'/cancel',{method:'POST',headers:auth()});let d=await r.json();if(!r.ok)throw Error(d.error);loadJobs()}catch(e){$('message').className='err';$('message').textContent=e.message}}
async function deleteJob(id){if(!confirm('Xoá job lịch sử này?'))return;try{let r=await fetch('/jobs/'+encodeURIComponent(id),{method:'DELETE',headers:auth()});let d=await r.json();if(!r.ok)throw Error(d.error);loadJobs()}catch(e){$('message').className='err';$('message').textContent=e.message}}
setInterval(()=>{window.loadJobs()},5000); window.loadJobs();
// Bảng quản lý LAN: STT, IP máy gửi và Retry cho job lỗi/gián đoạn.
function escapeCell(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function renderJobs(items){
  const rows=(items||[]).map((j,i)=>{
    const terminal=['FAILED','INTERRUPTED','CANCELLED'].includes(j.status) || j.formatter_status==='FAILED' || j.stage==='formatter_failed';
    const action=terminal?`<button onclick="retryJob('${escapeCell(j.id)}')">Retry</button>`:
      (['DONE','CANCELLED'].includes(j.status)?`<button onclick="deleteJob('${escapeCell(j.id)}')" style="background:#a63b3b">Xóa</button>`:`<button onclick="cancelJob('${escapeCell(j.id)}')" style="background:#8a5a20">Hủy</button>`);
    const progress=Math.max(0,Math.min(100,Number(j.progress??j.overall_progress??0)));
    return `<tr><td>${i+1}</td><td>${escapeCell(j.display_name||j.title||j.url||j.id)}</td><td>${escapeCell(j.status||'—')}</td><td>${escapeCell(j.stage||'—')}</td><td>${progress.toFixed(0)}%</td><td>${escapeCell(j.submitter_ip||'—')}</td><td>${action}</td></tr>`;
  }).join('');
  $('jobs').innerHTML=`<table style="width:100%;border-collapse:collapse"><thead><tr><th>STT</th><th>Tiêu đề</th><th>Trạng thái</th><th>Giai đoạn</th><th>Tiến độ</th><th>IP máy gửi</th><th>Thao tác</th></tr></thead><tbody>${rows||'<tr><td colspan="7">Chưa có job.</td></tr>'}</tbody></table>`;
}
// Bảng rộng: hiển thị rõ IP gửi và nơi lưu output, đường dẫn dài tự xuống dòng.
function renderJobs(items){
  const rows=(items||[]).map((j,i)=>{
    const terminal=['FAILED','INTERRUPTED','CANCELLED'].includes(j.status) || j.formatter_status==='FAILED' || j.stage==='formatter_failed';
    const action=terminal?`<button onclick="retryJob('${escapeCell(j.id)}')">Retry</button>`:
      (['DONE','CANCELLED'].includes(j.status)?`<button onclick="deleteJob('${escapeCell(j.id)}')" style="background:#a63b3b">Xóa</button>`:`<button onclick="cancelJob('${escapeCell(j.id)}')" style="background:#8a5a20">Hủy</button>`);
    const progress=Math.max(0,Math.min(100,Number(j.progress??j.overall_progress??0)));
    const output=escapeCell(j.output_folder||j.output_path||'—');
    return `<tr><td>${i+1}</td><td>${escapeCell(j.display_name||j.title||j.url||j.id)}</td><td>${escapeCell(j.status||'—')}</td><td>${escapeCell(j.stage||'—')}</td><td>${progress.toFixed(0)}%</td><td>${escapeCell(j.submitter_ip||'—')}</td><td class="output-path">${output}</td><td>${action}</td></tr>`;
  }).join('');
  $('jobs').innerHTML=`<table style="width:100%;border-collapse:collapse;table-layout:fixed"><thead><tr><th>STT</th><th>Tiêu đề</th><th>Trạng thái</th><th>Giai đoạn</th><th>Tiến độ</th><th>IP máy gửi</th><th>Nơi lưu output</th><th>Thao tác</th></tr></thead><tbody>${rows||'<tr><td colspan="8">Chưa có job.</td></tr>'}</tbody></table>`;
}
// Bảng quản lý dạng grid, căn giữa toàn bộ dữ liệu.
function formatCompleted(value){
  if(!value)return '—';
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return '—';
  // Định dạng hiển thị: HH:MM - DD/MM/YYYY.
  const hhmm=String(date.getHours()).padStart(2,'0')+':'+String(date.getMinutes()).padStart(2,'0');
  return hhmm+' - '+String(date.getDate()).padStart(2,'0')+'/'+String(date.getMonth()+1).padStart(2,'0')+'/'+date.getFullYear();
}
function renderJobs(items){
  const rows=(items||[]).map((j,i)=>{
    const terminal=['FAILED','INTERRUPTED','CANCELLED'].includes(j.status) || j.formatter_status==='FAILED' || j.stage==='formatter_failed';
    const action=terminal?`<button onclick="retryJob('${escapeCell(j.id)}')">Retry</button>`:
      (['DONE','CANCELLED'].includes(j.status)?`<button onclick="deleteJob('${escapeCell(j.id)}')" style="background:#a63b3b">Xóa</button>`:`<button onclick="cancelJob('${escapeCell(j.id)}')" style="background:#8a5a20">Hủy</button>`);
    const progress=Math.max(0,Math.min(100,Number(j.progress??j.overall_progress??0)));
    const machine=j.submitter_name||j.submitter_host||j.machine_name||j.hostname||j.submitter_ip||'—';
    const outputItems=Array.isArray(j.formatted_outputs)?j.formatted_outputs.map(x=>x&& (x.path||x.output_path)).filter(Boolean):[];
    const rawOutput=String(j.output_folder||j.output_path||'').trim();
    const hasOutput=Boolean(outputItems.length||rawOutput);
    return `<tr><td>${i+1}</td><td>${escapeCell(j.display_name||j.title||j.url||j.id)}</td><td>${escapeCell(j.status||'—')}</td><td>${progress.toFixed(0)}%</td><td>${escapeCell(machine)}</td><td class="output-path">${escapeCell(output)}</td><td>${escapeCell(formatCompleted(j.finished_at||j.completed_at))}</td><td>${action}</td></tr>`;
  }).join('');
  $('jobs').innerHTML=`<table class="jobs-grid"><thead><tr><th>STT</th><th>Tiêu đề</th><th>Trạng thái</th><th>Tiến độ</th><th>Máy gửi</th><th>Output</th><th>Ngày hoàn thành</th><th>Thao tác</th></tr></thead><tbody>${rows||'<tr><td colspan="8">Chưa có job.</td></tr>'}</tbody></table>`;
}
function renderJobs(items){
  const list=items||[];
  const rows=list.map((j,i)=>{
    const terminal=['FAILED','INTERRUPTED','CANCELLED'].includes(j.status) || j.formatter_status==='FAILED' || j.stage==='formatter_failed';
    const retryButton=`<button onclick="retryJob('${escapeCell(j.id)}')" style="border:1px solid #b83b50;background:transparent;color:#ff7885;border-radius:5px;padding:5px 10px">Retry</button>`;
    const deleteButton=`<button onclick="deleteJob('${escapeCell(j.id)}')" style="margin-left:4px;border:1px solid #b83b50;background:transparent;color:#ff7885;border-radius:5px;padding:5px 10px">Xóa</button>`;
    const action=j.status==='CANCELLED'?retryButton+deleteButton:(terminal?retryButton:(j.status==='DONE'?deleteButton:`<button onclick="cancelJob('${escapeCell(j.id)}')" style="border:1px solid #a8762b;background:transparent;color:#f5b94c;border-radius:5px;padding:5px 10px">Hủy</button>`));
    const progress=Math.max(0,Math.min(100,Number(j.progress??j.overall_progress??0)));
    const machine=j.submitter_name||j.submitter_host||j.machine_name||j.hostname||j.submitter_ip||'—';
    const outputItems=Array.isArray(j.formatted_outputs)?j.formatted_outputs.map(x=>x&& (x.path||x.output_path)).filter(Boolean):[];
    const rawOutput=String(j.output_folder||j.output_path||'').trim();
    const hasOutput=Boolean(outputItems.length||rawOutput);
    const status=String(j.status||'—').toLowerCase();
    const statusStyle=status==='done'?'background:#123b2b;color:#43dc8c;border:1px solid #1b6848':'background:#26344b;color:#dbe5f7;border:1px solid #3d5272';
    const folderButton=hasOutput?`<button data-path="${escapeCell(rawOutput)}" onclick="openClientFolder(this.dataset.path)" style="border:1px solid #3d5272;background:#122033;color:#dbe5f7;border-radius:5px;padding:5px 9px">📁 Mở thư mục</button><button data-path="${escapeCell(rawOutput)}" onclick="copyFolderPath(this.dataset.path)" style="margin-left:4px;border:1px solid #3d5272;background:transparent;color:#9fb0c9;border-radius:5px;padding:5px 7px">📋</button>`:'—';
    return `<tr><td>${i+1}</td><td class="title-cell" style="white-space:normal;overflow-wrap:anywhere;word-break:break-word">${escapeCell(j.display_name||j.title||j.url||j.id)}</td><td><span style="display:inline-block;padding:3px 9px;border-radius:4px;font-size:11px;${statusStyle}">${escapeCell(j.status||'—')}</span></td><td><div style="display:flex;align-items:center;justify-content:center;gap:8px"><span>${progress.toFixed(0)}%</span><div style="width:90px;height:5px;background:#293750;border-radius:4px;overflow:hidden"><i style="display:block;height:100%;width:${progress}%;background:#35d889;border-radius:4px"></i></div></div></td><td>${escapeCell(machine)}</td><td class="output-path">${folderButton}</td><td>${escapeCell(formatCompleted(j.finished_at||j.completed_at))}</td><td>${action}</td></tr>`;
  }).join('');
  const count=list.length;
  $('jobs').innerHTML=`<table class="jobs-grid"><thead><tr><th>STT</th><th>Tiêu đề</th><th>Trạng thái</th><th>Tiến độ</th><th>Máy gửi</th><th>Output</th><th>Ngày hoàn thành</th><th>Thao tác</th></tr></thead><tbody>${rows||'<tr><td colspan="8">Chưa có job.</td></tr>'}</tbody></table><div style="display:flex;justify-content:space-between;align-items:center;padding:12px 2px 0;color:#8fa0bb;font-size:11px"><span>Hiển thị 1 đến ${count} của ${count} jobs</span><div><button disabled style="padding:4px 8px;background:transparent;color:#63728a">‹</button><button disabled style="padding:4px 8px;background:transparent;color:#63728a">‹</button><button style="padding:4px 9px;background:#2f7df6;color:#fff;border-radius:4px">1</button><button disabled style="padding:4px 8px;background:transparent;color:#63728a">›</button><button disabled style="padding:4px 8px;background:transparent;color:#63728a">›</button></div></div>`;
}
function folderUrl(job){
  const raw=String(job.output_folder||job.output_path||'').trim();
  if(!raw)return '';
  const normalized=raw.replace(/\\\\/g,'/');
  return normalized.startsWith('//')?'file:'+encodeURI(normalized):'file:///'+encodeURI(normalized);
}
async function copyFolderPath(value){try{await navigator.clipboard.writeText(value);$('message').className='ok';$('message').textContent='Đã sao chép đường dẫn output'}catch(e){window.prompt('Sao chép đường dẫn này:',value)}}
async function openClientFolder(value){try{const r=await fetch('http://127.0.0.1:8793/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:value})});const d=await r.json();if(!r.ok)throw Error(d.error);$('message').className='ok';$('message').textContent='Đã mở thư mục trên máy này'}catch(e){const notice='Không thể mở thư mục trên máy này. Helper chưa chạy trên máy này; hãy chạy start_folder_helper_hidden.vbs rồi thử lại.';$('message').className='err';$('message').textContent=notice;window.alert(notice)}}
async function retryJob(id){try{let r=await fetch('/jobs/'+encodeURIComponent(id)+'/retry',{method:'POST',headers:auth()});let d=await r.json();if(!r.ok)throw Error(d.error);loadJobs()}catch(e){$('message').className='err';$('message').textContent=e.message}}
</script>"""


def authorize(value: str, expected: str) -> bool:
    return bool(value and expected) and secrets.compare_digest(value, expected)


def is_local_address(address: str) -> bool:
    return address in {"127.0.0.1", "::1"}


def validate_submission(payload: dict[str, Any]) -> dict[str, str | None]:
    url = str(payload.get("url") or "").strip() or None
    source_path = str(payload.get("source_path") or "").strip() or None
    if not url and not source_path:
        raise ValueError("job requires url or source_path")
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("url must use http or https")
    if source_path:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("source_path does not exist")
        if INPUT_ROOT is not None:
            root = INPUT_ROOT.resolve()
            if source != root and root not in source.parents:
                raise ValueError("source_path is outside the configured input root")
        source_path = str(source)
    return {"url": url, "source_path": source_path}


def validate_discovery_submission(payload: dict[str, Any]) -> dict[str, list[str]]:
    channels = payload.get("channels")
    if not isinstance(channels, list):
        raise ValueError("channels must be a list")
    values = list(dict.fromkeys(str(value or "").strip() for value in channels if str(value or "").strip()))
    if not values:
        raise ValueError("at least one channel is required")
    if len(values) > 50:
        raise ValueError("maximum 50 channels per scan")
    if any(not value.startswith(("http://", "https://")) for value in values):
        raise ValueError("channel URLs must use http or https")
    return {"channels": values}


def _backend():
    from backend import job_runner
    return job_runner


def resolve_submitter_name(ip: str | None) -> str | None:
    """Resolve a LAN client IP to a Windows hostname, with safe fallback."""
    value = str(ip or '').strip()
    if not value or is_local_address(value):
        return None
    previous_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(0.5)
        hostname, _aliases, _addresses = socket.gethostbyaddr(value)
    except (OSError, socket.herror, socket.gaierror):
        return None
    finally:
        socket.setdefaulttimeout(previous_timeout)
    hostname = str(hostname or '').strip()
    return hostname or None


def _youtube_video_id(url: str) -> str:
    parsed = urlparse(str(url))
    value = (parse_qs(parsed.query).get("v") or [""])[0].strip()
    if value:
        return value
    if parsed.netloc.lower() in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/", 1)[0]
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[-2] in {"shorts", "live", "embed"}:
        return parts[-1]
    return ""


def _discovery_history_path() -> Path:
    configured_root = os.environ.get("SILENCE_CUTTER_DATA_DIR", "").strip()
    if configured_root:
        root = Path(configured_root).expanduser()
    else:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        root = (Path(local_app_data) / "SilenceCutter") if local_app_data else (Path.home() / ".silence_cutter")
    return root / "channel-discovery-history.json"


def _load_discovery_history() -> dict[str, dict[str, Any]]:
    path = _discovery_history_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return {}
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_discovery_history(history: dict[str, dict[str, Any]]) -> None:
    path = _discovery_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _videos_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    path = parsed.path.rstrip("/")
    if not path.endswith("/videos"):
        path += "/videos"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _popular_videos_url(value: str) -> str:
    parsed = urlparse(_videos_url(value))
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "view=0&sort=p&flow=grid", ""))


def _candidate_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def fetch_channel_candidates(
    channel_url: str, *, since: datetime, until: datetime, limit: int = 100,
) -> list[dict[str, Any]]:
    """Pick the first unseen-by-date candidate from the channel's popular tab."""
    backend = _backend()
    command_factory = getattr(backend, "_yt_dlp_command", None)
    command = command_factory() if callable(command_factory) else None
    if not command:
        raise RuntimeError("yt-dlp is unavailable")
    result = subprocess.run(
        [*command, "--dump-json", "--flat-playlist", "--skip-download", "--no-warnings", "--playlist-end", str(limit),
         _popular_videos_url(channel_url)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "channel scan failed").strip()[-500:])
    ranked: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = str(payload.get("id") or _youtube_video_id(payload.get("webpage_url") or payload.get("url"))).strip()
        if not video_id:
            continue
        url = str(payload.get("webpage_url") or payload.get("original_url") or payload.get("url") or f"https://www.youtube.com/watch?v={video_id}")
        ranked.append({
            "video_id": video_id, "url": url, "title": str(payload.get("title") or video_id),
            "channel": str(payload.get("channel") or payload.get("uploader") or "YouTube"),
            "channel_id": payload.get("channel_id"), "published_at": None,
            "view_count": int(payload.get("view_count") or 0), "thumbnail": payload.get("thumbnail"),
            "_published": payload.get("upload_date") or payload.get("release_date")
                or payload.get("release_timestamp") or payload.get("timestamp"),
        })
    ranked.sort(key=lambda item: (item.get("view_count", 0), item["video_id"]), reverse=True)
    candidates: list[dict[str, Any]] = []
    for item in ranked:
        published = _candidate_date(item.pop("_published", None))
        if published is None:
            date_result = subprocess.run(
                [*command, "--skip-download", "--no-warnings", "--no-playlist", "--print", "%(upload_date)s", item["url"]],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if date_result.returncode != 0:
                continue
            lines = [line.strip() for line in date_result.stdout.splitlines() if line.strip()]
            published = _candidate_date(lines[-1] if lines else None)
        if published is None or not (since <= published <= until):
            continue
        item["published_at"] = published.isoformat()
        candidates.append(item)
        break
    return candidates


def discover_channel_jobs(
    channel_urls: list[str], *, now: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    pause_seconds: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Select one unseen high-view video per channel and enqueue it through MANUAL LAN."""
    until = now or datetime.now(timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    since = until - timedelta(days=365 * 2)
    history = _load_discovery_history()
    used_ids = set(history)
    backend = _backend()
    for job in getattr(backend, "list_jobs", lambda: [])():
        if job.get("origin") == "MANUAL_LAN":
            job_id = _youtube_video_id(job.get("url") or "")
            if job_id:
                used_ids.add(job_id)
    result: dict[str, Any] = {"created": [], "skipped": [], "errors": [], "total": 0}
    seen_channels: set[str] = set()
    unique_channels: list[str] = []
    for raw_url in channel_urls:
        channel_url = _videos_url(raw_url)
        if not channel_url or channel_url in seen_channels:
            continue
        seen_channels.add(channel_url)
        unique_channels.append(channel_url)
    pause_seconds = pause_seconds or (lambda: random.uniform(DISCOVERY_PAUSE_MIN_SECONDS, DISCOVERY_PAUSE_MAX_SECONDS))
    for index, channel_url in enumerate(unique_channels):
        try:
            all_candidates = fetch_channel_candidates(channel_url, since=since, until=until)
            candidates = [item for item in all_candidates if item["video_id"] not in used_ids]
            if not candidates:
                reason = "already_used" if all_candidates else "no_video"
                result["skipped"].append({"channel_url": channel_url, "reason": reason})
                continue
            selected = max(candidates, key=lambda item: (item.get("view_count", 0), item.get("published_at") or "", item["video_id"]))
            submitted = create_remote_job({
                "url": selected["url"], "discovery": "channel_top_view",
                "channel_name": selected.get("channel") or "YouTube",
                "display_name": selected.get("channel") or "YouTube",
            })
            selected = {**selected, "job_id": submitted["job_id"], "status": submitted.get("status"), "selected_at": until.isoformat()}
            history[selected["video_id"]] = selected
            used_ids.add(selected["video_id"])
            _save_discovery_history(history)
            result["created"].append(selected)
            result["total"] += 1
            if index < len(unique_channels) - 1:
                sleeper(max(0.0, float(pause_seconds())))
        except Exception as error:
            result["errors"].append({"channel_url": channel_url, "error": str(error)[:500]})
    if result["created"]:
        _save_discovery_history(history)
    return result


def fetch_youtube_metadata(url: str) -> dict[str, Any]:
    """Return preview metadata using the same yt-dlp command as the backend."""
    backend = _backend()
    command_factory = getattr(backend, "_yt_dlp_command", None)
    command = command_factory() if callable(command_factory) else None
    if not command:
        raise RuntimeError("yt-dlp is unavailable")
    result = subprocess.run(
        [*command, "--dump-single-json", "--no-playlist", "--skip-download", url],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "metadata lookup failed").strip()[-500:])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("metadata response was invalid") from error
    seconds = payload.get("duration")
    duration_seconds = float(seconds) if isinstance(seconds, (int, float)) else None
    duration = "--"
    if duration_seconds is not None:
        total = max(0, int(round(duration_seconds)))
        duration = f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60:02d}:{total % 60:02d}"
    thumbnails = payload.get("thumbnails") or []
    thumbnail = payload.get("thumbnail") or (thumbnails[-1].get("url") if thumbnails and isinstance(thumbnails[-1], dict) else None)
    return {
        "title": str(payload.get("title") or "YouTube video"),
        "channel": str(payload.get("uploader") or payload.get("channel") or "YouTube"),
        "duration_seconds": duration_seconds,
        "duration": duration,
        "thumbnail": thumbnail,
        "url": url,
    }


def create_remote_job(payload: dict[str, Any], *, submitter_ip: str | None = None) -> dict[str, Any]:
    value = validate_submission(payload)
    backend = _backend()
    if value["url"]:
        video_id = _youtube_video_id(value["url"])
        if video_id:
            list_jobs = getattr(backend, "list_jobs", None)
            for existing in list_jobs() if callable(list_jobs) else []:
                existing_url = str(existing.get("url") or "")
                if (
                    existing.get("origin") == "MANUAL_LAN"
                    and _youtube_video_id(existing_url) == video_id
                ):
                    return {
                        "job_id": existing["id"],
                        "status": existing.get("status"),
                        "deduplicated": True,
                    }
        metadata = None
        try:
            metadata = fetch_youtube_metadata(value["url"])
        except Exception:
            # Metadata enriches the record but must not prevent queue submission.
            metadata = None
        job = backend.create_jobs([value["url"]])[0]
        if metadata:
            discovery_name = str(payload.get("display_name") or metadata["channel"]).strip()
            display_name = discovery_name if payload.get("discovery") else metadata["title"]
            job.update(
                title=metadata["title"], display_name=display_name,
                channel_name=metadata["channel"], duration=metadata["duration_seconds"],
                thumbnail=metadata.get("thumbnail"),
            )
        elif payload.get("discovery") and payload.get("display_name"):
            job["display_name"] = str(payload["display_name"]).strip()
            job["channel_name"] = str(payload.get("channel_name") or payload["display_name"]).strip()
    else:
        settings = backend.load_settings()
        source = Path(str(value["source_path"]))
        duration = backend._probe_local_media(source)
        if duration is None:
            raise ValueError("source media could not be probed")
        job = backend._create_local_job(
            source, backend._local_fingerprint(source, source.stat().st_size, source.stat().st_mtime_ns), duration, settings,
        )
    job["remote_callback_url"] = str(payload.get("callback_url") or "") or None
    job["remote_callback_token"] = str(payload.get("callback_token") or "") or None
    job["submitter_ip"] = submitter_ip or str(payload.get("submitter_ip") or "") or None
    job["submitter_name"] = (
        str(payload.get("submitter_name") or "").strip()
        or resolve_submitter_name(job["submitter_ip"])
        or None
    )
    job["origin"] = "MANUAL_LAN"
    job["silence_external_id"] = None
    job["download_external_id"] = None
    backend._write_job(job)
    return {"job_id": job["id"], "status": job["status"]}


def _manual_scheduler_payload(job: dict[str, Any]) -> dict[str, Any]:
    backend = _backend()
    source = Path(str(job.get("source_path") or "")).expanduser().resolve()
    if not source.is_file():
        raise ValueError("manual source is not finalized")
    url = str(job.get("url") or "")
    video_id = (parse_qs(urlparse(url).query).get("v") or [""])[0]
    if len(video_id) != 11:
        raise ValueError("manual URL missing video id")
    settings = backend.load_settings()
    output_root = Path(str(settings["output_folder"])).expanduser().resolve()
    display_name = str(job.get("display_name") or job.get("title") or video_id).strip()
    folder_factory = getattr(backend, "_user_output_folder", None)
    output_dir = (
        folder_factory(output_root, display_name, str(job["id"]))
        if callable(folder_factory)
        else output_root / display_name
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "handoff_id": f"manual-{job['id']}", "origin": "MANUAL_LAN",
        "source_file": str(source), "channel_name": str(job.get("title") or "Manual Recovery"),
        "output_dir": str(output_dir), "video_id": video_id,
        "video_title": str(job.get("title") or video_id),
        "enhanced_content_selection": True,
    }


def _scheduler_request(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urlrequest.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urlrequest.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _manual_download_payload(job: dict[str, Any]) -> dict[str, Any]:
    backend = _backend()
    settings = backend.load_settings()
    job_dir = backend._job_path(job["id"], settings).parent
    work_dir = job_dir / "download-work"
    final_dir = job_dir / "download-final"
    work_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    url = str(job.get("url") or "")
    video_id = (parse_qs(urlparse(url).query).get("v") or [""])[0]
    return {
        "handoff_id": f"manual-download-{job['id']}", "video_id": video_id,
        "video_url": url, "channel_name": str(job.get("title") or "Manual Recovery"),
        "work_dir": str(work_dir.resolve()), "final_output_dir": str(final_dir.resolve()),
    }


def _write_manual_job(job: dict[str, Any]) -> None:
    _backend()._write_job(job)


def _sync_manual_jobs_once() -> None:
    backend = _backend()
    for job in backend.list_jobs():
        if job.get("origin") != "MANUAL_LAN" or job.get("status") in {"DONE", "FAILED", "CANCELLED"}:
            continue
        try:
            if not job.get("source_path"):
                external = job.get("download_external_id")
                if not external:
                    response = _scheduler_request("POST", f"{YTDOWNLOAD_BASE_URL}/api/download-jobs", _manual_download_payload(job))
                    job.update(
                        download_external_id=response.get("external_id"), status="DOWNLOADING",
                        stage="downloading", progress=0, error=None,
                    )
                    _write_manual_job(job)
                    continue
                response = _scheduler_request("GET", f"{YTDOWNLOAD_BASE_URL}/api/download-jobs/{external}")
                state = str(response.get("state") or "").upper()
                if state == "DONE":
                    source = str(response.get("downloaded_file_path") or "")
                    if not source or not Path(source).is_file():
                        raise ValueError("YTDOWNLOAD returned no finalized local path")
                    job.update(source_path=source, status="READY", stage="ready", progress=100, error=None)
                    _write_manual_job(job)
                elif state in {"FAILED", "CANCELLED"}:
                    job.update(status="FAILED", stage="download_failed", error=response.get("error") or state, finished_at=backend._now())
                    _write_manual_job(job)
                else:
                    job.update(status="DOWNLOADING", stage="downloading", progress=float(response.get("progress_percent") or 0))
                    _write_manual_job(job)
                continue
            external = job.get("silence_external_id")
            if not external:
                response = _scheduler_request("POST", f"{SILENCE_BASE_URL}/api/process-jobs", _manual_scheduler_payload(job))
                job.update(
                    silence_external_id=response.get("external_id"), status="QUEUED",
                    stage="waiting_silence", progress=0, error=None,
                )
                _write_manual_job(job)
                continue
            response = _scheduler_request("GET", f"{SILENCE_BASE_URL}/api/process-jobs/{external}")
            state = str(response.get("state") or "").upper()
            job["scheduler_state"] = state or None
            if state == "DONE":
                outputs = [str(value) for value in response.get("processed_files") or [] if str(value)]
                if not outputs:
                    job.update(
                        status="FAILED", stage="processing_failed", progress=0,
                        error="PROCESSING_FAILED", scheduler_failure_detail="scheduler DONE without processed_files",
                        finished_at=backend._now(),
                    )
                    _write_manual_job(job)
                    continue
                job.update(
                    status="DONE", stage="done", progress=100, formatted_outputs=[{"path": value} for value in outputs],
                    output_path=outputs[0] if outputs else response.get("processed_file_path"),
                    output_folder=Path(outputs[0]).parent.as_posix() if outputs else None,
                    finished_at=backend._now(), error=None,
                )
                _write_manual_job(job)
            elif state in {"FAILED", "CANCELLED"}:
                job.update(
                    status="FAILED" if state == "FAILED" else "CANCELLED", stage=state.lower(),
                    error=response.get("error") or state,
                    scheduler_failure_detail=response.get("failure_detail") or response.get("error") or state,
                    finished_at=backend._now(),
                )
                _write_manual_job(job)
            else:
                job.update(status="PROCESSING" if state in {"PROCESSING", "FINALIZING"} else "QUEUED", stage=state.lower() or "waiting_silence", progress=float(response.get("progress_percent") or 0), error=None)
                _write_manual_job(job)
        except Exception as exc:
            job.update(status="QUEUED", stage="waiting_silence", error=f"WAITING_CONTENTOPS: {exc}")
            _write_manual_job(job)


def _watch_shared_scheduler() -> None:
    while True:
        try:
            _sync_manual_jobs_once()
        except Exception:
            pass
        time.sleep(1.0)


def retry_remote_job(job_id: str) -> dict[str, Any]:
    return _backend().retry_job(job_id)


def get_remote_job(job_id: str) -> dict[str, Any]:
    return _backend()._read_job(_backend()._job_path(job_id))


def open_remote_job_folder(job_id: str) -> dict[str, bool]:
    """Open a job's output directory on the machine hosting this API."""
    job = get_remote_job(job_id)
    raw = str(job.get("output_folder") or job.get("output_path") or "").strip()
    if not raw:
        raise ValueError("job has no output folder")
    target = Path(raw)
    if job.get("output_path") and target.suffix:
        target = target.parent
    if hasattr(os, "startfile"):
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        raise RuntimeError("opening folders is supported on Windows only")
    return {"ok": True}


def _notify_callback(job: dict[str, Any]) -> None:
    callback = job.get("remote_callback_url")
    if not callback:
        return
    body = json.dumps({
        "job_id": job["id"], "status": job.get("status"),
        "stage": job.get("stage"), "progress": job.get("progress"),
        "output_folder": job.get("output_folder"), "output_path": job.get("output_path"),
        "error": job.get("error") or job.get("formatter_error"),
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if job.get("remote_callback_token"):
        headers["Authorization"] = f"Bearer {job['remote_callback_token']}"
    try:
        urlrequest.urlopen(urlrequest.Request(callback, data=body, headers=headers, method="POST"), timeout=10).read()
    except Exception:
        pass


def _watch_callbacks() -> None:
    terminal = {"DONE", "FAILED", "CANCELLED"}
    sent: set[str] = set()
    while True:
        try:
            for job in _backend().list_jobs():
                if job.get("id") not in sent and job.get("status") in terminal and job.get("remote_callback_url"):
                    _notify_callback(job)
                    sent.add(job["id"])
        except Exception:
            pass
        time.sleep(1.0)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _reply(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply_html(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        return is_local_address(self.client_address[0]) or authorize(
            self.headers.get("Authorization", "").removeprefix("Bearer "), TOKEN
        )

    def do_GET(self) -> None:
        if self.path == "/ui":
            self._reply_html(200, UI_HTML)
            return
        if self.path == "/":
            self._reply(200, {
                "service": "Silence Cutter LAN Job API",
                "status": "READY",
                "health": "/health",
                "jobs": "/jobs (Bearer token required)",
            })
            return
        if self.path == "/health":
            self._reply(200, {"status": "READY", "port": PORT})
            return
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return
        if self.path.startswith("/metadata"):
            url = parse_qs(urlparse(self.path).query).get("url", [""])[0].strip()
            if not url:
                self._reply(400, {"error": "url is required"})
                return
            try:
                self._reply(200, fetch_youtube_metadata(url))
            except Exception as error:
                self._reply(502, {"error": str(error)})
            return
        if self.path.startswith("/jobs/"):
            try:
                self._reply(200, get_remote_job(self.path.rsplit("/", 1)[-1]))
            except FileNotFoundError:
                self._reply(404, {"error": "job not found"})
            return
        if self.path == "/jobs":
            try:
                self._reply(200, {"jobs": _backend().list_jobs()})
            except Exception as error:
                self._reply(500, {"error": str(error)})
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return
        if self.path == "/discover-jobs":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 64 * 1024:
                    raise ValueError("invalid request size")
                payload = validate_discovery_submission(json.loads(self.rfile.read(size)))
                self._reply(200, discover_channel_jobs(payload["channels"]))
            except (ValueError, json.JSONDecodeError) as error:
                self._reply(400, {"error": str(error)})
            except Exception as error:
                self._reply(500, {"error": str(error)})
            return
        if self.path.startswith("/jobs/") and self.path.endswith("/cancel"):
            try:
                self._reply(200, _backend().cancel_job(self.path.split("/")[2]))
            except FileNotFoundError:
                self._reply(404, {"error": "job not found"})
            except Exception as error:
                self._reply(400, {"error": str(error)})
            return
        if self.path.startswith("/jobs/") and self.path.endswith("/retry"):
            try:
                self._reply(200, retry_remote_job(self.path.split("/")[2]))
            except FileNotFoundError:
                self._reply(404, {"error": "job not found"})
            except Exception as error:
                self._reply(400, {"error": str(error)})
            return
        if self.path.startswith("/jobs/") and self.path.endswith("/open-folder"):
            try:
                self._reply(200, open_remote_job_folder(self.path.split("/")[2]))
            except FileNotFoundError:
                self._reply(404, {"error": "job not found"})
            except Exception as error:
                self._reply(400, {"error": str(error)})
            return
        if self.path != "/jobs":
            self._reply(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 64 * 1024:
                raise ValueError("invalid request size")
            result = create_remote_job(
                json.loads(self.rfile.read(size)), submitter_ip=self.client_address[0]
            )
            self._reply(201, result)
        except (ValueError, json.JSONDecodeError) as error:
            self._reply(400, {"error": str(error)})
        except Exception as error:
            self._reply(500, {"error": str(error)})

    def do_DELETE(self) -> None:
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return
        if self.path.startswith("/jobs/") and self.path != "/jobs/history":
            try:
                self._reply(200, _backend().remove_job(self.path.rsplit("/", 1)[-1]))
            except FileNotFoundError:
                self._reply(404, {"error": "job not found"})
            except Exception as error:
                self._reply(400, {"error": str(error)})
            return
        if self.path != "/jobs/history":
            self._reply(404, {"error": "not found"})
            return
        try:
            self._reply(200, _backend().clear_history())
        except Exception as error:
            self._reply(500, {"error": str(error)})


def main() -> None:
    threading.Thread(target=_watch_callbacks, daemon=True).start()
    threading.Thread(target=_watch_shared_scheduler, daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), _Handler).serve_forever()


if __name__ == "__main__":
    main()
