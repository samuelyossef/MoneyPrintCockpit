/* Task-oriented Cockpit screens composed from the existing, API-backed views. */
window.CockpitFlow = (function () {
  const V = window.CKV;
  const D = window.CK;
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));

  function criar(scope) {
    const channels = D.CHANNELS.filter(channel => channel.status === "connected");
    const options = channels.map(channel => `<option value="${esc(channel.id)}">${esc(channel.name)} · ${esc(channel.platform)}</option>`).join("");
    return `<div class="card" id="createRoot">
      <div class="card-hd"><span class="card-title">Comece pelo canal</span></div>
      <div class="card-bd" style="display:flex;flex-direction:column;gap:16px">
        <div class="grid2" style="margin:0">
          <div class="md-field"><label for="createChannel">1. Canal conectado</label><select class="set-select" id="createChannel" style="width:100%"><option value="">Selecione um canal</option>${options}</select></div>
          <div class="md-field"><label for="createFormat">2. Formato</label><select class="set-select" id="createFormat" style="width:100%"><option value="short">Short · 9:16</option><option value="long">Vídeo longo · 16:9</option></select></div>
        </div>
        <div class="card-note" id="createProfileNote" role="status">O perfil do canal preencherá voz, legendas, duração e parâmetros do vídeo.</div>
        <div><button class="btn primary" id="createContinue" type="button" disabled>Continuar para criação</button></div>
        ${channels.length ? "" : '<div class="card-note">Conecte um canal para criar vídeos. <button class="btn sm" type="button" data-view="clientes">Abrir Canais</button></div>'}
      </div></div>`;
  }

  function producoes(scope) {
    const format = scope.productionFormat === "long" ? "long" : "short";
    const tab = ["jobs", "reviews", "library"].includes(scope.productionTab) ? scope.productionTab : "jobs";
    return `<div id="productionsRoot">
      <div class="view-tabs" id="productionTabs" style="margin-bottom:16px">
        <button class="view-tab ${tab === "jobs" ? "is-active" : ""}" type="button" data-production-tab="jobs">Trabalhos</button>
        <button class="view-tab ${tab === "reviews" ? "is-active" : ""}" type="button" data-production-tab="reviews">Revisões</button>
        <button class="view-tab ${tab === "library" ? "is-active" : ""}" type="button" data-production-tab="library">Biblioteca e publicação</button>
      </div>
      <div data-production-pane="jobs" ${tab === "jobs" ? "" : "hidden"}>${V.jobsWorkers()}</div>
      <div data-production-pane="reviews" ${tab === "reviews" ? "" : "hidden"}>${V.agentes(["revisoes"])}</div>
      <div data-production-pane="library" ${tab === "library" ? "" : "hidden"}>
        <div class="view-tabs" style="margin-bottom:14px"><button class="view-tab ${format === "short" ? "is-active" : ""}" type="button" data-library-format="short">Shorts</button><button class="view-tab ${format === "long" ? "is-active" : ""}" type="button" data-library-format="long">Vídeos longos</button></div>
        <div data-format="${format}">${V.libraryCard(format)}</div>${V.overview(scope)}
      </div>
    </div>`;
  }

  function canais(scope) {
    return `${V.canais(scope)}<div class="dr-sec" style="margin:24px 0 12px">Automação por canal</div>${V.agentes(["agendamento", "operador"])}`;
  }

  function crescimento(scope) {
    return `${V.agentes(["estrategia"])}<div class="dr-sec" style="margin:24px 0 12px">Growth OS</div>${V.growth(scope)}`;
  }

  function resultados(scope) {
    return `${V.custosIA(scope)}<div class="dr-sec" style="margin:24px 0 12px">Análise dos vídeos publicados</div>${V.agentes(["analytics"])}`;
  }

  return { criar, producoes, canais, crescimento, resultados };
}());
