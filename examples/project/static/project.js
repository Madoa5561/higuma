"use strict";
document.querySelector("#login")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const endpoints = event.currentTarget.dataset;
  const status = document.querySelector("#status");
  const form = new FormData(event.currentTarget);
  status.textContent = "ログイン中…";
  try {
    const tokenResponse = await fetch(endpoints.tokenUrl, { credentials: "same-origin" });
    if (!tokenResponse.ok) throw new Error("トークンを取得できませんでした");
    const { csrf_token: token } = await tokenResponse.json();
    const response = await fetch(endpoints.loginUrl, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify({ name: form.get("name"), password: form.get("password") }),
    });
    if (!response.ok) throw new Error("ログインできませんでした");
    location.assign(endpoints.notesUrl);
  } catch (error) { status.textContent = error.message; }
});
document.querySelector("#note")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const endpoints = event.currentTarget.dataset;
  const form = new FormData(event.currentTarget);
  const status = document.querySelector("#status");
  try {
    const tokenResponse = await fetch(endpoints.tokenUrl, { credentials: "same-origin" });
    if (!tokenResponse.ok) throw new Error("トークンを取得できませんでした");
    const { csrf_token: token } = await tokenResponse.json();
    const response = await fetch(endpoints.apiUrl, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify({ title: form.get("title") }),
    });
    if (!response.ok) throw new Error("ノートを保存できませんでした");
    location.reload();
  } catch (error) { status.textContent = error.message; }
});
