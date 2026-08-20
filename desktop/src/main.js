import { invoke } from "@tauri-apps/api/core";
import { openPath } from "@tauri-apps/plugin-opener";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import "./styles.css";

const $ = (selector) => document.querySelector(selector);
const ACTIVE = new Set(["DOWNLOADING", "ANALYZING", "RENDERING"]);
let jobs = [];
let localFiles = [];
let settings = null;
let pollBusy = false;
let localScanBusy = false;
let authNotificationSent = false;
let reviewNotificationSent = false;
const shownProgress = new Map();

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

async function notifyWindows(title, body) {
  let granted = await isPermissionGranted();
  if (!granted) granted = (await requestPermission()) === "granted";
  if (granted) sendNotification({ title, body });
}

async function notifyNeedsReview() {
  await notifyWindows(
    "Silence Cutter",
    "Video sạch dài hơn 20 phút. Hãy kiểm tra trước khi định dạng 3 phần.",
  );
}

function formatDuration(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const seconds = Math.max(0, Math.round(Number(value)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}` : `${minutes}:${String(rest).padStart(2, "0")}`;
}

function folderOf(path) {
  return path?.replace(/[\\/][^\\/]+$/, "") || "";
}

function stageDetail(job) {
  if (job.formatter_status === "NEEDS_REVIEW") return "Automatic processing complete";
  if (["PLANNING", "RENDERING"].includes(job.formatter_status)) {
    const count = job.formatter_part_count || job.formatted_outputs?.length || 3;
    return job.formatter_status === "PLANNING"
      ? "PLANNING FINAL PARTS"
      : `FORMATTING • PART ${job.formatter_current_part || 1}/${count}`;
  }
  return String(job.stage || job.status || "").replaceAll("_", " ").toUpperCase();
}

function overallProgress(job) {
  const incoming = Math.max(0, Math.min(100, Number(job.overall_progress) || 0));
  const value = Math.max(shownProgress.get(job.id) || 0, incoming);
  shownProgress.set(job.id, value);
  return value;
}

function overallProgressMarkup(job) {
  const value = overallProgress(job);
  const estimating = job.eta_status === "ESTIMATING" && !job.finished_at;
  return `<div class="progress${estimating ? " indeterminate" : ""}"><i${estimating ? "" : ` style="width:${value}%"`}></i></div><span class="stage-label">${estimating ? "ESTIMATING…" : `${Math.round(value)}%`} • ${stageDetail(job)}</span>`;
}

function totalTimerMarkup(job) {
  const elapsedValue = job.started_at ? (job.total_elapsed_seconds ?? ((Date.now() - Date.parse(job.started_at)) / 1000)) : null;
  const needsReview = job.formatter_status === "NEEDS_REVIEW";
  const eta = needsReview ? "—" : (job.total_eta_seconds == null
    ? "Estimating…"
    : `${Number(job.total_eta_seconds) > 0 ? "~" : ""}${formatDuration(job.total_eta_seconds)}`);
  const totalValue = job.total_job_time ?? job.estimated_total_job_time;
  const total = totalValue == null ? "Estimating…" : `${job.total_job_time == null ? "~" : ""}${formatDuration(totalValue)}`;
  return `<div class="job-timer"><span>Elapsed <b>${elapsedValue == null ? "—" : formatDuration(elapsedValue)}</b></span><span>ETA <b>${eta}</b></span><span>Total <b>${total}</b></span></div>`;
}

function button(label, action, id, disabled = false) {
  return `<button class="row-action" data-action="${action}" data-id="${id}" ${disabled ? "disabled" : ""}>${label}</button>`;
}

function partButton(label, action, id, index) {
  return `<button class="row-action" data-action="${action}" data-id="${id}" data-part="${index}">${label}</button>`;
}

function renderJobs() {
  const body = $("#queueBody");
  $("#queueCount").textContent = `${jobs.length} ${jobs.length === 1 ? "job" : "jobs"}`;
  $("#emptyState").hidden = jobs.length > 0;
  body.innerHTML = jobs.map((job) => {
    const local = job.input_mode === "LOCAL_FOLDER";
    const active = ACTIVE.has(job.status);
    const formatterActive = ["PLANNING", "RENDERING"].includes(job.formatter_status);
    const youtubeStatus = {
      auth_required: "YOUTUBE LOGIN REQUIRED",
      auth_failed: "YOUTUBE LOGIN REQUIRED",
      profile_locked: "YOUTUBE PROFILE LOCKED",
      profile_error: "YOUTUBE PROFILE ERROR",
    }[job.stage];
    const authProblem = Boolean(youtubeStatus);
    const partCount = job.formatter_part_count || job.formatted_outputs?.length || 3;
    const visibleStatus = job.formatter_status === "NEEDS_REVIEW" ? "NEEDS REVIEW" : (youtubeStatus || (formatterActive ? "FORMATTING" : (job.status === "RENDERING" ? "RENDERING CLEAN VIDEO" : job.status)));
    const statusClass = formatterActive ? "formatting" : job.status.toLowerCase();
    const retryable = ["FAILED", "CANCELLED", "INTERRUPTED"].includes(job.status);
    const formatted = (job.formatted_outputs || []).map((part) =>
      partButton(`Part ${part.index} Play`, "play-part", job.id, part.index)
      + partButton(`Part ${part.index} Folder`, "folder-part", job.id, part.index)
    ).join("");
    const needsReview = job.formatter_status === "NEEDS_REVIEW";
    const cleanupStatus = job.source_cleanup_status
      ? `Source cleanup: ${job.source_cleanup_status}${job.source_cleanup_error ? ` — ${job.source_cleanup_error}` : ""}`
      : "";
    const actions = [
      local && job.status === "DONE" && !needsReview && job.output_folder ? button("Open Output", "open-output", job.id) : "",
      local && job.status === "DONE" && !needsReview && job.source_path ? button("Open Source Folder", "source-folder", job.id) : "",
      local && needsReview && job.source_path ? button("Open Source", "open-source", job.id) : "",
      local && needsReview && job.output_folder ? button("Open Output Folder", "open-output", job.id) : "",
      !local && job.status === "DONE" && job.output_path ? button("Play", "play", job.id) : "",
      !local && job.status === "DONE" && job.output_path ? button("Folder", "folder", job.id) : "",
      job.status === "DONE" && job.formatter_status !== "DONE" ? button(job.formatter_status === "NEEDS_REVIEW" ? "Format Anyway" : "Format", "format", job.id, formatterActive) : "",
      formatted,
      button("Log", "log", job.id),
      authProblem ? button("Open Profile", "profile", job.id) : "",
      retryable ? button("Retry", "retry", job.id) : "",
      active || ["QUEUED", "READY"].includes(job.status) ? button("Cancel", "cancel", job.id) : "",
      button("Remove", "remove", job.id, active || formatterActive || job.status === "READY"),
    ].join("");
    return `<tr>
      <td><span class="status status-${statusClass}"><i></i>${visibleStatus}</span></td>
      <td class="title-cell"><strong title="${escapeHtml(job.display_name || job.title || job.url)}">${escapeHtml(job.display_name || job.title || job.url)}</strong><small>ID: ${escapeHtml((job.id || "").slice(0, 8))}${formatterActive ? ` • PART ${job.formatter_current_part || 1}/${partCount}` : ""}</small>${job.error || job.formatter_error ? `<em title="${escapeHtml(job.error || job.formatter_error)}">${escapeHtml(job.error || job.formatter_error)}</em>` : ""}${cleanupStatus ? `<em title="${escapeHtml(cleanupStatus)}">${escapeHtml(cleanupStatus)}</em>` : ""}</td>
      <td>${formatDuration(job.duration)}</td>
      <td class="progress-cell">${overallProgressMarkup(job)}</td>
      <td>${totalTimerMarkup(job)}</td>
      <td><div class="row-actions">${actions}</div></td>
    </tr>`;
  }).join("");
}

function renderLocalFiles(result) {
  localFiles = result.files || [];
  const counts = result.counts || {};
  $("#detectedCount").textContent = counts.total_files || 0;
  $("#readyCount").textContent = counts.READY || 0;
  $("#processingCount").textContent = counts.PROCESSING || 0;
  $("#doneCount").textContent = counts.DONE || 0;
  $("#reviewCount").textContent = counts.NEEDS_REVIEW || 0;
  $("#failedCount").textContent = counts.FAILED || 0;
  $("#detectedFiles").innerHTML = localFiles.length
    ? localFiles.map((file) => `<div class="detected-file"><strong title="${escapeHtml(file.path)}">${escapeHtml(file.filename)}</strong><span>${formatDuration(file.duration)}</span><b class="file-status file-status-${String(file.status).toLowerCase()}">${escapeHtml(file.status)}</b></div>`).join("")
    : "<span>No supported video files detected.</span>";
}

async function refreshLocalFiles() {
  if (localScanBusy) return;
  localScanBusy = true;
  try {
    renderLocalFiles(await rpc("scan_local_folder"));
  } catch (error) {
    toast(String(error), true);
  } finally {
    localScanBusy = false;
  }
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
    const authRequired = jobs.some((job) => ["auth_required", "auth_failed", "profile_locked", "profile_error"].includes(job.stage));
    if (authRequired && !authNotificationSent) {
      authNotificationSent = true;
      await notifyWindows(
        "YouTube login required",
        "Open the YouTube profile in Silence Cutter, sign in, then retry the job.",
      );
    }
    if (!authRequired) authNotificationSent = false;
    const needsReview = jobs.some((job) => job.formatter_status === "NEEDS_REVIEW");
    if (needsReview && !reviewNotificationSent) {
      reviewNotificationSent = true;
      await notifyNeedsReview();
    }
    if (!needsReview) reviewNotificationSent = false;
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

async function refreshYoutubeProfile() {
  try {
    const result = await rpc("youtube_login_status");
    $("#youtubeLoginStatus").textContent = result.status;
    $("#youtubeLastSessionTest").textContent = result.last_session_test
      ? `Last tested: ${new Date(result.last_session_test).toLocaleString()}`
      : "Session has not been tested.";
    const warning = ["LOGIN REQUIRED", "PROFILE LOCKED", "PROFILE ERROR"].includes(result.status);
    $("#youtubeProfileButton").dataset.state = warning ? "warning" : (result.profile_ready ? "ready" : "empty");
    $("#youtubeProfileButton").title = `YouTube Profile — ${result.status}`;
  } catch (_error) {
    $("#youtubeLoginStatus").textContent = "LOGIN REQUIRED";
    $("#youtubeProfileButton").dataset.state = "warning";
  }
}

async function showYoutubeProfile() {
  await refreshYoutubeProfile();
  $("#youtubeProfileDialog").showModal();
}

async function openYoutubeLogin() {
  try {
    await rpc("open_youtube_login");
    toast("Dedicated YouTube login browser opened");
    await refreshYoutubeProfile();
  } catch (error) {
    toast(String(error), true);
  }
}

async function testYoutubeAccess() {
  const button = $("#youtubeTestButton");
  button.disabled = true;
  try {
    const result = await rpc("test_youtube_access");
    await refreshYoutubeProfile();
    toast(result.accessible ? "YouTube session is valid" : (result.error || result.status), !result.accessible);
  } catch (error) {
    toast(String(error), true);
  } finally {
    button.disabled = false;
  }
}

async function resetYoutubeProfile() {
  if (!confirm("Reset the dedicated YouTube profile? This signs it out from Silence Cutter and cannot be undone.")) return;
  try {
    await rpc("reset_youtube_profile", { confirmed: true });
    await refreshYoutubeProfile();
    toast("YouTube profile reset");
  } catch (error) {
    toast(String(error), true);
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

async function persistLocalSettings() {
  settings = await rpc("save_settings", {
    input_folder: $("#inputFolder").value,
    output_folder: $("#mainOutputFolder").value,
    workspace_folder: settings.workspace_folder,
    watch_input_folder: $("#watchFolder").checked,
    keep_clean_master: Boolean(settings.keep_clean_master),
    max_concurrent_jobs: 1,
  });
  $("#inputSetting").value = settings.input_folder;
  $("#outputSetting").value = settings.output_folder;
  $("#outputFolder").textContent = settings.output_folder;
}

async function browseLocalFolder(target) {
  try {
    const result = await rpc("browse_folder", { initial_path: target.value });
    if (!result.path) return;
    target.value = result.path;
    await persistLocalSettings();
    await refreshLocalFiles();
  } catch (error) {
    toast(String(error), true);
  }
}

async function startLocalProcessing() {
  const button = $("#startProcessingButton");
  button.disabled = true;
  try {
    await persistLocalSettings();
    const result = await rpc("start_local_processing");
    $("#inputMessage").textContent = `${result.enqueued} new file(s) queued`;
    renderLocalFiles(result);
    await refreshJobs();
  } catch (error) {
    toast(String(error), true);
  } finally {
    button.disabled = false;
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
    if (target.dataset.action === "open-output") await openPath(job.output_folder);
    if (target.dataset.action === "source-folder") await openPath(folderOf(job.source_path));
    if (target.dataset.action === "open-source") await openPath(job.source_path);
    if (target.dataset.action === "play-part" || target.dataset.action === "folder-part") {
      const part = (job.formatted_outputs || []).find((item) => String(item.index) === target.dataset.part);
      if (!part) throw new Error("Formatted part is missing");
      await openPath(target.dataset.action === "play-part" ? part.path : folderOf(part.path));
    }
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
      toast(result.formatter_status === "DONE" ? "Đã tạo xong 3 video" : (result.formatter_error || result.formatter_status), result.formatter_status === "FAILED");
    }
    if (target.dataset.action === "retry") await rpc("retry_job", { id: job.id });
    if (target.dataset.action === "profile") await showYoutubeProfile();
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
  $("#inputFolder").value = settings.input_folder;
  $("#mainOutputFolder").value = settings.output_folder;
  $("#watchFolder").checked = Boolean(settings.watch_input_folder);
  $("#outputFolder").textContent = settings.output_folder;
  $("#inputSetting").value = settings.input_folder;
  $("#outputSetting").value = settings.output_folder;
  $("#workspaceSetting").value = settings.workspace_folder;
  $("#keepCleanMasterSetting").checked = Boolean(settings.keep_clean_master);
}

async function saveSettings(event) {
  event.preventDefault();
  if (jobs.some((job) => ACTIVE.has(job.status) || ["QUEUED", "READY"].includes(job.status))) {
    return toast("Wait for queued and active jobs before changing folders", true);
  }
  try {
    settings = await rpc("save_settings", {
      input_folder: $("#inputSetting").value,
      output_folder: $("#outputSetting").value,
      workspace_folder: $("#workspaceSetting").value,
      watch_input_folder: $("#watchFolder").checked,
      keep_clean_master: $("#keepCleanMasterSetting").checked,
      max_concurrent_jobs: 1,
    });
    $("#outputFolder").textContent = settings.output_folder;
    $("#inputFolder").value = settings.input_folder;
    $("#mainOutputFolder").value = settings.output_folder;
    $("#settingsDialog").close();
    toast("Settings saved");
    await refreshJobs();
  } catch (error) {
    toast(String(error), true);
  }
}

$("#addButton").addEventListener("click", addJobs);
$("#clearButton").addEventListener("click", () => { $("#urlInput").value = ""; $("#inputMessage").textContent = ""; });
$("#startProcessingButton").addEventListener("click", startLocalProcessing);
$("#browseInputButton").addEventListener("click", () => browseLocalFolder($("#inputFolder")));
$("#browseOutputButton").addEventListener("click", () => browseLocalFolder($("#mainOutputFolder")));
$("#watchFolder").addEventListener("change", async () => {
  try {
    await persistLocalSettings();
    await refreshLocalFiles();
  } catch (error) {
    toast(String(error), true);
  }
});
$("#refreshButton").addEventListener("click", async () => { await Promise.all([refreshJobs(), refreshLocalFiles()]); });
$("#queueBody").addEventListener("click", handleAction);
$("#settingsButton").addEventListener("click", () => $("#settingsDialog").showModal());
$("#youtubeProfileButton").addEventListener("click", showYoutubeProfile);
$("#settingsForm").addEventListener("submit", saveSettings);
$("#benchmarkButton").addEventListener("click", runHardwareBenchmark);
$("#youtubeLoginButton").addEventListener("click", openYoutubeLogin);
$("#youtubeTestButton").addEventListener("click", testYoutubeAccess);
$("#youtubeResetButton").addEventListener("click", resetYoutubeProfile);
$("#closeYoutubeProfileButton").addEventListener("click", () => $("#youtubeProfileDialog").close());
$("#closeLogButton").addEventListener("click", () => $("#logDialog").close());

await loadSettings();
await Promise.all([refreshHealth(), refreshYoutubeProfile(), refreshJobs(), refreshLocalFiles()]);
setInterval(refreshJobs, 1000);
setInterval(refreshLocalFiles, 2000);
setInterval(refreshHealth, 15000);
setInterval(refreshYoutubeProfile, 15000);
setInterval(() => { if (jobs.some((job) => !job.finished_at && job.started_at)) renderJobs(); }, 1000);
