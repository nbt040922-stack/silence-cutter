import { invoke } from "@tauri-apps/api/core";
import { openPath } from "@tauri-apps/plugin-opener";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import "./styles.css";

const $ = (selector) => document.querySelector(selector);
const ACTIVE = new Set(["DOWNLOADING", "ANALYZING", "RENDERING"]);
let jobs = [];
let settings = null;
let pollBusy = false;

async function rpc(operation, payload = {}) {
  return invoke("backend_rpc", { request: { operation, payload } });
}

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast visible${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.className = "toast", 3200);
}

async function notifyNeedsReview() {
  let granted = await isPermissionGranted();
  if (!granted) granted = (await requestPermission()) === "granted";
  if (granted) sendNotification({
    title: "Silence Cutter",
    body: "Video sạch dài hơn 20 phút. Hãy kiểm tra trước khi định dạng 3 phần.",
  });
}

function formatDuration(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const seconds = Math.max(0, Math.round(Number(value)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}` : `${minutes}:${String(rest).padStart(2, "0")}`;
}

function elapsed(job) {
  if (!job.started_at) return "—";
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  return formatDuration((end - Date.parse(job.started_at)) / 1000);
}

function folderOf(path) {
  return path?.replace(/[\\/][^\\/]+$/, "") || "";
}

function progressMarkup(job) {
  if (job.progress == null) {
    return `<div class="progress indeterminate"><i></i></div><span class="stage-label">${job.stage}</span>`;
  }
  const value = Math.max(0, Math.min(100, Number(job.progress)));
  return `<div class="progress"><i style="width:${value}%"></i></div><span class="stage-label">${Math.round(value)}%</span>`;
}

function button(label, action, id, disabled = false) {
  return `<button class="row-action" data-action="${action}" data-id="${id}" ${disabled ? "disabled" : ""}>${label}</button>`;
}

function renderJobs() {
  const body = $("#queueBody");
  $("#queueCount").textContent = `${jobs.length} ${jobs.length === 1 ? "job" : "jobs"}`;
  $("#emptyState").hidden = jobs.length > 0;
  body.innerHTML = jobs.map((job) => {
    const active = ACTIVE.has(job.status);
    const retryable = ["FAILED", "CANCELLED", "INTERRUPTED"].includes(job.status);
    const actions = [
      job.status === "DONE" ? button("Play", "play", job.id) : "",
      job.status === "DONE" ? button("Folder", "folder", job.id) : "",
      job.status === "DONE" ? button("Format", "format", job.id) : "",
      button("Log", "log", job.id),
      retryable ? button("Retry", "retry", job.id) : "",
      active || ["QUEUED", "READY"].includes(job.status) ? button("Cancel", "cancel", job.id) : "",
      button("Remove", "remove", job.id, active || job.status === "READY"),
    ].join("");
    return `<tr>
      <td><span class="status status-${job.status.toLowerCase()}"><i></i>${job.status}</span></td>
      <td class="title-cell"><strong title="${escapeHtml(job.title || job.url)}">${escapeHtml(job.title || job.url)}</strong><small title="${escapeHtml(job.url)}">${escapeHtml(job.url)}</small>${job.error ? `<em title="${escapeHtml(job.error)}">${escapeHtml(job.error)}</em>` : ""}</td>
      <td>${formatDuration(job.duration)}</td>
      <td class="progress-cell">${progressMarkup(job)}</td>
      <td>${elapsed(job)}</td>
      <td><div class="row-actions">${actions}</div></td>
    </tr>`;
  }).join("");
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

async function refreshJobs() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    jobs = await rpc("list_jobs");
    renderJobs();
  } catch (error) {
    toast(String(error), true);
  } finally {
    pollBusy = false;
  }
}

async function refreshHealth() {
  try {
    const health = await rpc("health");
    const items = [["GPU", health.gpu], ["CUDA", health.cuda], ["NVENC", health.nvenc], ["Pipeline", health.pipeline && health.python && health.sensevoice_model]];
    $("#health").innerHTML = items.map(([name, ready]) => `<span class="health-item ${ready ? "ready" : "missing"}"><i></i>${name}<b>${ready ? "READY" : "NOT READY"}</b></span>`).join("");
  } catch (error) {
    $("#health").innerHTML = `<span class="health-item missing">Backend NOT READY</span>`;
  }
}

async function runHardwareBenchmark() {
  const button = $("#benchmarkButton");
  const result = $("#benchmarkResult");
  button.disabled = true;
  button.textContent = "Benchmark runningâ€¦";
  result.hidden = false;
  result.textContent = "Running the fixed production benchmark. Keep the machine idle.";
  try {
    const report = await rpc("hardware_benchmark");
    result.textContent = [
      `${report.performance_class} Â· ${Number(report.total_time).toFixed(2)}s total`,
      `Render ${Number(report.render_time).toFixed(2)}s Â· Timeline ${report.timeline_hash}`,
      report.report_path,
    ].join("\n");
    toast("Hardware benchmark completed");
    await refreshHealth();
  } catch (error) {
    result.textContent = String(error);
    toast(String(error), true);
  } finally {
    button.disabled = false;
    button.textContent = "Run Hardware Benchmark";
  }
}

async function addJobs() {
  const urls = $("#urlInput").value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!urls.length) return toast("Paste at least one URL", true);
  $("#addButton").disabled = true;
  try {
    const created = await rpc("create_jobs", { urls });
    $("#urlInput").value = "";
    $("#inputMessage").textContent = `${created.length} job(s) added`;
    await refreshJobs();
  } catch (error) {
    toast(String(error), true);
  } finally {
    $("#addButton").disabled = false;
  }
}

async function handleAction(event) {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const job = jobs.find((item) => item.id === target.dataset.id);
  if (!job) return;
  try {
    if (target.dataset.action === "play") await openPath(job.output_path);
    if (target.dataset.action === "folder") await openPath(folderOf(job.output_path));
    if (target.dataset.action === "format") {
      let result = await rpc("plan_tiktok_formatter", { id: job.id });
      if (result.formatter_status === "NEEDS_REVIEW") {
        await notifyNeedsReview();
        if (!confirm("Video sạch dài hơn 20 phút. Vẫn định dạng thành đúng 3 phần?")) {
          toast("Formatter đang chờ bạn xem lại");
          return;
        }
        result = await rpc("plan_tiktok_formatter", { id: job.id, format_anyway: true });
      }
      toast(result.formatter_status === "PLANNED" ? "Đã tạo kế hoạch 3 phần" : result.formatter_status);
    }
    if (target.dataset.action === "retry") await rpc("retry_job", { id: job.id });
    if (target.dataset.action === "cancel") await rpc("cancel_job", { id: job.id });
    if (target.dataset.action === "remove" && confirm(`Remove “${job.title || job.url}” and its local job files?`)) {
      await rpc("remove_job", { id: job.id });
    }
    if (target.dataset.action === "log") {
      const logs = await rpc("read_log", { id: job.id });
      $("#conciseLog").textContent = logs.concise || "No concise log entries yet.";
      $("#advancedLog").textContent = logs.advanced || "No advanced log entries yet.";
      $("#logDialog").showModal();
    }
    await refreshJobs();
  } catch (error) {
    toast(String(error), true);
  }
}

async function loadSettings() {
  settings = await rpc("get_settings");
  $("#outputFolder").textContent = settings.output_folder;
  $("#outputSetting").value = settings.output_folder;
  $("#workspaceSetting").value = settings.workspace_folder;
}

async function saveSettings(event) {
  event.preventDefault();
  if (jobs.some((job) => ACTIVE.has(job.status) || ["QUEUED", "READY"].includes(job.status))) {
    return toast("Wait for queued and active jobs before changing folders", true);
  }
  try {
    settings = await rpc("save_settings", { output_folder: $("#outputSetting").value, workspace_folder: $("#workspaceSetting").value, max_concurrent_jobs: 1 });
    $("#outputFolder").textContent = settings.output_folder;
    $("#settingsDialog").close();
    toast("Settings saved");
    await refreshJobs();
  } catch (error) {
    toast(String(error), true);
  }
}

$("#addButton").addEventListener("click", addJobs);
$("#clearButton").addEventListener("click", () => { $("#urlInput").value = ""; $("#inputMessage").textContent = ""; });
$("#refreshButton").addEventListener("click", refreshJobs);
$("#queueBody").addEventListener("click", handleAction);
$("#settingsButton").addEventListener("click", () => $("#settingsDialog").showModal());
$("#settingsForm").addEventListener("submit", saveSettings);
$("#benchmarkButton").addEventListener("click", runHardwareBenchmark);
$("#closeLogButton").addEventListener("click", () => $("#logDialog").close());

await Promise.all([loadSettings(), refreshHealth(), refreshJobs()]);
setInterval(refreshJobs, 1000);
setInterval(refreshHealth, 15000);
setInterval(() => { if (jobs.some((job) => !job.finished_at && job.started_at)) renderJobs(); }, 1000);
