document.addEventListener('DOMContentLoaded', () => {
  const state = { projects: [], selectedProjectId: null };
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    loading: $('#loading-state'), empty: $('#empty-state'), dashboard: $('#analytics-dashboard'),
    picker: $('#project-picker'), dialog: $('#project-dialog'), form: $('#project-form'),
    notice: $('#analytics-notice'), scan: $('#scan-button'), firstScan: $('#first-scan-button'),
    reportEmpty: $('#report-empty'), report: $('#report-content'), lastScan: $('#last-scan'),
  };

  async function request(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) { window.location.replace('/login'); throw new Error('Your session has ended.'); }
    if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
    return data;
  }
  function showNotice(message) { elements.notice.textContent = message; elements.notice.hidden = !message; }
  function formatDate(value) { return value ? new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Not scanned yet'; }
  function setScanBusy(busy) { elements.scan.disabled = busy; elements.scan.textContent = busy ? 'Scanning…' : 'Run new scan'; if (elements.firstScan) { elements.firstScan.disabled = busy; elements.firstScan.textContent = busy ? 'Scanning…' : 'Run first scan'; } }
  function esc(value) { const div = document.createElement('div'); div.textContent = value ?? ''; return div.innerHTML; }

  async function loadProjects(preferredId) {
    const data = await request('/api/analytics/projects');
    state.projects = data.projects;
    elements.loading.hidden = true;
    if (!state.projects.length) { elements.empty.hidden = false; elements.dashboard.hidden = true; return; }
    elements.empty.hidden = true; elements.dashboard.hidden = false;
    state.selectedProjectId = Number(preferredId) || state.selectedProjectId || state.projects[0].id;
    if (!state.projects.some((project) => project.id === state.selectedProjectId)) state.selectedProjectId = state.projects[0].id;
    elements.picker.innerHTML = state.projects.map((project) => `<option value="${project.id}">${esc(project.brand_name)} · ${esc(project.domain)}</option>`).join('');
    elements.picker.value = state.selectedProjectId;
    await loadReport(state.selectedProjectId);
  }
  async function loadReport(projectId) {
    const report = await request(`/api/analytics/projects/${projectId}/report`);
    renderReport(report);
  }
  function renderReport(data) {
    const { project, run, engines, prompts, history } = data;
    if (!run) { elements.report.hidden = true; elements.reportEmpty.hidden = false; elements.lastScan.textContent = 'Not scanned yet'; return; }
    elements.report.hidden = false; elements.reportEmpty.hidden = true;
    elements.lastScan.textContent = `Last scan: ${formatDate(run.created_at)}`;
    $('#project-name').textContent = `${project.brand_name} · ${project.domain}`;
    $('#visibility-score').textContent = run.visibility_score; $('#mention-rate').textContent = run.mention_rate;
    $('#citation-rate').textContent = run.citation_rate; $('#share-of-voice').textContent = run.share_of_voice;
    $('#report-summary').textContent = run.summary; $('#trend-current').textContent = `${run.visibility_score}% current`;
    const chart = $('#trend-chart');
    chart.innerHTML = history.length ? history.map((entry) => `<div class="trend-bar" style="height:${Math.max(18, entry.visibility_score)}%"><span>${entry.visibility_score}</span></div>`).join('') : '<span class="trend-empty">Run another scan to start a trend.</span>';
    $('#engine-list').innerHTML = engines.map((engine) => { const sign = engine.change > 0 ? '+' : ''; const direction = engine.change < 0 ? 'down' : ''; return `<div class="engine-row"><strong>${esc(engine.engine)}</strong><div class="engine-track"><span style="width:${engine.visibility_score}%"></span></div><span class="engine-score">${engine.visibility_score}% <small class="engine-change ${direction}">${sign}${engine.change}</small></span></div>`; }).join('');
    $('#prompt-count').textContent = `${prompts.length} tracked prompts`;
    $('#prompt-list').innerHTML = prompts.map((prompt) => `<tr><td>${esc(prompt.prompt)}</td><td><span class="tag">${esc(prompt.intent)}</span></td><td>#${prompt.position}</td><td class="cite-${prompt.cited === 'Yes' ? 'yes' : 'no'}">${esc(prompt.cited)}</td><td>${esc(prompt.leading_brand)}</td><td>${esc(prompt.opportunity)}</td></tr>`).join('');
  }
  async function scan() {
    if (!state.selectedProjectId) return;
    setScanBusy(true); showNotice('');
    try { const data = await request(`/api/analytics/projects/${state.selectedProjectId}/scan`, { method: 'POST' }); renderReport(data.report); await loadProjects(state.selectedProjectId); }
    catch (error) { showNotice(error.message); } finally { setScanBusy(false); }
  }
  function openDialog() { $('#project-error').hidden = true; elements.form.reset(); elements.dialog.showModal(); $('#project-domain').focus(); }
  function closeDialog() { elements.dialog.close(); }

  $('#open-project-dialog').addEventListener('click', openDialog); $('#empty-add-project').addEventListener('click', openDialog); $('#close-project-dialog').addEventListener('click', closeDialog);
  elements.form.addEventListener('submit', async (event) => { event.preventDefault(); const error = $('#project-error'); error.hidden = true; const button = $('#save-project'); button.disabled = true; button.textContent = 'Creating…'; try { const project = await request('/api/analytics/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.fromEntries(new FormData(elements.form))) }); closeDialog(); await loadProjects(project.project.id); } catch (err) { error.textContent = err.message; error.hidden = false; } finally { button.disabled = false; button.textContent = 'Create workspace'; } });
  elements.picker.addEventListener('change', () => { state.selectedProjectId = Number(elements.picker.value); loadReport(state.selectedProjectId).catch((error) => showNotice(error.message)); });
  elements.scan.addEventListener('click', scan); elements.firstScan.addEventListener('click', scan);
  $('#delete-project').addEventListener('click', async () => { const project = state.projects.find((item) => item.id === state.selectedProjectId); if (!project || !window.confirm(`Remove ${project.domain} and all of its reports?`)) return; try { await request(`/api/analytics/projects/${project.id}`, { method: 'DELETE' }); state.selectedProjectId = null; await loadProjects(); } catch (error) { showNotice(error.message); } });
  $('#logout-button').addEventListener('click', async () => { try { await request('/api/logout', { method: 'POST' }); window.location.assign('/'); } catch (error) { showNotice(error.message); } });
  loadProjects().catch((error) => { elements.loading.hidden = true; showNotice(error.message); });
});
