// Utilidades compartidas: cliente Supabase, sesion, API y escape de HTML.
const sb = window.supabase.createClient(window.AIAUDIT.supabaseUrl, window.AIAUDIT.supabaseKey);

// Todo texto que viene del cliente (nombres de archivo, cuestionario) pasa por esc() antes de ir a innerHTML.
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const $ = sel => document.querySelector(sel);

function mensaje(el, texto, error = false) {
  el.textContent = texto;
  el.classList.toggle("error", error);
}

async function sesion() {
  const { data } = await sb.auth.getSession();
  if (!data.session) {
    location.href = "/login";
    throw new Error("sin sesion");
  }
  return data.session;
}

async function api(ruta, opciones = {}) {
  const s = await sesion();
  const r = await fetch(ruta, {
    ...opciones,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${s.access_token}`, ...(opciones.headers || {}) },
  });
  const cuerpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(cuerpo.detail || `Error ${r.status}`);
  return cuerpo;
}

async function salir() {
  await sb.auth.signOut();
  location.href = "/login";
}

function fecha(iso) {
  return iso ? new Date(iso).toLocaleString("es", { dateStyle: "short", timeStyle: "short" }) : "";
}

function mb(bytes) {
  return bytes == null ? "" : `${(bytes / 1048576).toFixed(1)} MB`;
}

const ESTADOS = {
  recibiendo: ["Recibiendo archivos", "aviso"], enviado: ["Enviado", "aviso"], procesando: ["Procesando", "aviso"],
  analizado: ["Analizado", "ok"], entregado: ["Entregado", "ok"],
  pendiente: ["Pendiente", "aviso"], ejecutando: ["Ejecutando", "aviso"], ok: ["Completado", "ok"], error: ["Error", "error"],
};

function chipEstado(estado) {
  const [texto, clase] = ESTADOS[estado] || [estado, ""];
  return `<span class="estado ${clase}">${esc(texto)}</span>`;
}
