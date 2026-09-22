(function () {
  const html = document.documentElement;
  const themeBtn = document.getElementById("themeBtn"), themeIcon = document.getElementById("themeIcon");
  const sun = '<circle cx="8" cy="8" r="3"/><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3.22 3.22l1.06 1.06M11.72 11.72l1.06 1.06M11.72 4.28l-1.06 1.06M4.28 11.72l-1.06 1.06"/>';
  const moon = '<path d="M13 8a5 5 0 1 1-5-5 3.5 3.5 0 0 0 5 5Z"/>';
  function applyTheme(theme) { html.setAttribute("data-theme", theme); localStorage.setItem("lattice-theme", theme); themeIcon.innerHTML = theme === "dark" ? moon : sun; }
  applyTheme(localStorage.getItem("lattice-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  themeBtn.addEventListener("click", () => applyTheme(html.getAttribute("data-theme") === "dark" ? "light" : "dark"));

  const password = document.getElementById("password"), toggle = document.getElementById("pwToggle"), icon = document.getElementById("pwIcon");
  toggle.addEventListener("click", () => { const show = password.type === "password"; password.type = show ? "text" : "password"; icon.innerHTML = show ? '<path d="M2 2l12 12"/>' : '<path d="M1 8s2.8-5 7-5 7 5 7 5-2.8 5-7 5-7-5-7-5Z"/><circle cx="8" cy="8" r="2"/>'; });
  const form = document.getElementById("loginForm"), submit = document.getElementById("submitBtn"), inner = document.getElementById("btnInner"), error = document.getElementById("errorMsg"), errorText = document.getElementById("errorText");
  function setError(message) { errorText.textContent = message; error.classList.add("visible"); password.classList.add("err"); }
  function clearError() { error.classList.remove("visible"); password.classList.remove("err"); document.getElementById("email").classList.remove("err"); }
  form.addEventListener("submit", async event => {
    event.preventDefault(); clearError();
    const email = document.getElementById("email").value.trim(), pass = password.value;
    if (!email || !pass) { setError("Informe e-mail e senha."); return; }
    submit.disabled = true; inner.textContent = "Entrando…";
    try {
      const response = await fetch("/api/auth/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password: pass }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Não foi possível entrar.");
      sessionStorage.setItem("cockpit-csrf", body.csrf_token || "");
      window.location.replace("/");
    } catch (failure) { setError(failure.message); submit.disabled = false; inner.textContent = "Entrar"; }
  });
  ["email", "password"].forEach(id => document.getElementById(id).addEventListener("input", clearError));
}());
