"use strict";

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  if (response.status === 401 && path !== "/api/login") {
    window.location.assign("/login");
    throw new Error("세션이 만료되었습니다.");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "요청에 실패했습니다.");
  return result;
}

function value(id, content) {
  const node = document.getElementById(id);
  if (node) node.textContent = String(content);
}

function showError(message) {
  const node = document.getElementById("api-error");
  if (node) { node.textContent = message; node.hidden = false; }
}

async function loginPage() {
  const form = document.getElementById("login-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("login-error");
    error.hidden = true;
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await api("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value.trim(),
          ["password"]: document.getElementById("password").value,
          totp: document.getElementById("totp").value.trim()
        })
      });
      document.getElementById("password").value = "";
      window.location.assign("/ops");
    } catch (_) {
      document.getElementById("password").value = "";
      error.textContent = "계정, 비밀번호 또는 일회용 코드를 확인하세요.";
      error.hidden = false;
    } finally { button.disabled = false; }
  });
}

async function workspace(page) {
  let session;
  try { session = await api("/api/session"); }
  catch (_) { return; }
  value("user-badge", `${session.username} · ${session.role}`);
  document.getElementById("logout").addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST",
        headers: { "X-CSRF-Token": session.csrf } });
      window.location.assign("/login");
    } catch (_) { showError("로그아웃에 실패했습니다."); }
  });
  try {
    if (page === "ops") {
      const overview = await api("/api/ros/overview");
      value("ros-mode", overview.mode);
      value("ros-tm", overview.tm_icd);
      value("ros-tc", overview.tc_send_available ? "사용 가능" : "차단");
    } else {
      const [catalog, passes] = await Promise.all([
        api("/api/mps/catalog"), api("/api/mps/passes")
      ]);
      value("mps-version", catalog.version);
      value("mps-commands", catalog.commands.length);
      value("mps-passes", passes.passes.length);
      value("mps-catalog-status", catalog.status);
    }
  } catch (_) { showError("서브시스템 상태를 읽지 못했습니다."); }
}

const page = document.body.dataset.page;
if (page === "login") loginPage();
if (page === "ops" || page === "mps") workspace(page);
