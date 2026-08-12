document.addEventListener('DOMContentLoaded', () => {
  const state = {
    projects: [],
    selectedProjectId: null,
    activeView: 'overview',
    audit: null,
    searchConsole: null,
    tracking: null,
    evidence: null,
    startupNotice: null,
    loadSequence: 0,
    pollingJobs: new Set(),
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const elements = {
    loading: $('#loading-state'), empty: $('#empty-state'), dashboard: $('#analytics-dashboard'),
    picker: $('#project-picker'), dialog: $('#project-dialog'), form: $('#project-form'),
    notice: $('#analytics-notice'), scan: $('#scan-button'), firstScan: $('#first-scan-button'),
    reportEmpty: $('#report-empty'), report: $('#report-content'), lastScan: $('#last-scan'),
    projectList: $('#sidebar-project-list'), evidenceDialog: $('#evidence-dialog'),
  };

  async function apiRequest(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.location.replace('/login');
      throw new Error('Your session has ended.');
    }
    if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
    return data;
  }

  function jsonOptions(method, body) {
    return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  }

  function esc(value) {
    const div = document.createElement('div');
    div.textContent = value ?? '';
    return div.innerHTML;
  }

  function safeHref(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function formatDate(value, fallback = 'Not available') {
    if (!value) return fallback;
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return fallback;
    return parsed.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  function formatNumber(value, fallback = 'Unavailable') {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return fallback;
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(Number(value));
  }

  function metricValue(value) {
    return value === null || value === undefined ? 'Unavailable' : formatNumber(value);
  }

  function setPercentMetric(selector, value) {
    const element = $(selector);
    element.textContent = metricValue(value);
    element.parentElement?.classList.toggle('metric-unavailable', value === null || value === undefined);
  }

  function yesNoUnavailable(value) {
    if (value === null || value === undefined) return 'Unavailable';
    return value ? 'Yes' : 'No';
  }

  function showNotice(message) {
    elements.notice.textContent = message || '';
    elements.notice.hidden = !message;
  }

  function setSourceStatus(element, status, detail) {
    element.classList.remove('pending', 'connected', 'partial', 'running', 'error');
    element.classList.add(status);
    $('small', element).textContent = detail;
  }

  function setAuditBusy(busy, progress = null) {
    elements.scan.disabled = busy;
    elements.scan.textContent = busy
      ? `Auditing pages${progress === null ? '…' : ` · ${progress}%`}`
      : 'Run full audit';
    if (elements.firstScan) {
      elements.firstScan.disabled = busy;
      elements.firstScan.textContent = busy ? 'Auditing pages…' : 'Run full audit';
    }
  }

  function renderProjectList() {
    elements.projectList.innerHTML = state.projects.map((project) => {
      const active = project.id === state.selectedProjectId ? ' active' : '';
      const run = project.latest_run;
      let auditState = 'Needs full audit';
      if (run?.status === 'failed') auditState = 'Audit unavailable';
      else if (run?.visibility_score !== null && run?.visibility_score !== undefined) auditState = `${formatNumber(run.visibility_score)}% audited`;
      return `<button class="sidebar-project${active}" type="button" data-project-id="${project.id}">
        <strong>${esc(project.brand_name)}</strong><span>${esc(project.website_url || project.domain)}</span><small>${esc(auditState)}</small>
      </button>`;
    }).join('');
  }

  async function loadProjects(preferredId) {
    const data = await apiRequest('/api/analytics/projects');
    state.projects = data.projects || [];
    elements.loading.hidden = true;
    if (!state.projects.length) {
      elements.empty.hidden = false;
      elements.dashboard.hidden = true;
      return;
    }
    elements.empty.hidden = true;
    elements.dashboard.hidden = false;
    state.selectedProjectId = Number(preferredId) || state.selectedProjectId || state.projects[0].id;
    if (!state.projects.some((project) => project.id === state.selectedProjectId)) state.selectedProjectId = state.projects[0].id;
    elements.picker.innerHTML = state.projects.map((project) => `<option value="${project.id}">${esc(project.brand_name)} · ${esc(project.website_url || project.domain)}</option>`).join('');
    elements.picker.value = String(state.selectedProjectId);
    renderProjectList();
    await loadWorkspace(state.selectedProjectId);
  }

  async function loadWorkspace(projectId) {
    const sequence = ++state.loadSequence;
    if (!state.startupNotice) showNotice('');
    const endpoints = [
      `/api/analytics/projects/${projectId}/audit`,
      `/api/analytics/projects/${projectId}/search-console`,
      `/api/analytics/projects/${projectId}/tracking`,
      `/api/analytics/projects/${projectId}/evidence`,
    ];
    const results = await Promise.all(endpoints.map((url) => apiRequest(url).catch((error) => ({ _error: error }))));
    if (sequence !== state.loadSequence || projectId !== state.selectedProjectId) return;
    const [auditResult, searchResult, trackingResult, evidenceResult] = results;
    if (auditResult._error) throw auditResult._error;
    state.audit = auditResult;
    state.searchConsole = searchResult._error ? null : searchResult.search_console;
    state.tracking = trackingResult._error ? null : trackingResult.tracking;
    state.evidence = evidenceResult._error ? null : evidenceResult.evidence;
    renderAudit(auditResult);
    renderSearchConsole(state.searchConsole, searchResult._error);
    renderTracking(state.tracking, trackingResult._error);
    renderEvidence(state.evidence, evidenceResult._error);
    if (searchResult._error || trackingResult._error || evidenceResult._error) {
      const firstError = searchResult._error || trackingResult._error || evidenceResult._error;
      showNotice(`Some evidence sources could not load: ${firstError.message}`);
    } else if (state.startupNotice) {
      showNotice(state.startupNotice);
      state.startupNotice = null;
    }
    if (auditResult.active_job) pollJob(auditResult.active_job.id, 'site_audit');
    if (!evidenceResult._error && evidenceResult.active_job) pollJob(evidenceResult.active_job.id, 'prompt_scan');
  }

  function renderAudit(data) {
    const audit = data.audit;
    const activeJob = data.active_job;
    if (activeJob) {
      setSourceStatus($('#crawl-source-status'), 'running', `${activeJob.status} · ${activeJob.progress || 0}%`);
      setAuditBusy(true, activeJob.progress || 0);
    } else {
      setAuditBusy(false);
    }
    if (!audit?.run) {
      elements.report.hidden = true;
      elements.reportEmpty.hidden = false;
      elements.lastScan.textContent = activeJob ? 'Full audit in progress' : 'Not audited yet';
      if (!activeJob) setSourceStatus($('#crawl-source-status'), 'pending', 'Not audited');
      renderPages(null);
      return;
    }

    const run = audit.run;
    const failed = run.status === 'failed' || run.readiness_score === null;
    setSourceStatus(
      $('#crawl-source-status'),
      activeJob ? 'running' : (failed ? 'error' : (run.status === 'partial' ? 'partial' : 'connected')),
      activeJob ? `${activeJob.progress || 0}% complete` : `${run.status} · ${run.pages_audited} page${run.pages_audited === 1 ? '' : 's'} · ${formatDate(run.created_at)}`,
    );
    elements.report.hidden = false;
    elements.reportEmpty.hidden = true;
    elements.lastScan.textContent = `Last full audit: ${formatDate(run.created_at)}`;
    $('#project-name').textContent = `${data.project.brand_name} · ${data.project.website_url || data.project.domain}`;
    setPercentMetric('#visibility-score', run.readiness_score);
    setPercentMetric('#mention-rate', run.metadata_score);
    setPercentMetric('#citation-rate', run.content_score);
    setPercentMetric('#share-of-voice', run.crawlability_score);
    $('#report-summary').textContent = run.summary;
    $('#report-source-badge').textContent = `${run.pages_audited} fetched · ${run.pages_failed} unavailable`;
    $('#trend-current').textContent = run.readiness_score === null ? 'Unavailable' : `${formatNumber(run.readiness_score)}% current`;

    const history = (audit.history || []).filter((entry) => entry.readiness_score !== null && entry.readiness_score !== undefined);
    $('#trend-chart').innerHTML = history.length
      ? history.map((entry) => `<div class="trend-bar" style="height:${Math.max(10, Number(entry.readiness_score))}%" title="${esc(formatDate(entry.created_at))}"><span>${formatNumber(entry.readiness_score)}</span></div>`).join('')
      : '<span class="trend-empty">No completed score is available yet.</span>';

    const factors = [
      ['Metadata', run.metadata_score], ['Content', run.content_score],
      ['Crawlability', run.crawlability_score], ['Structured data', run.structured_data_score],
    ];
    $('#engine-list').innerHTML = factors.map(([label, value]) => `<div class="engine-row"><strong>${label}</strong><div class="engine-track"><span style="width:${value ?? 0}%"></span></div><span class="engine-score">${value === null ? 'Unavailable' : `${formatNumber(value)}%`}</span></div>`).join('');

    const findings = audit.findings || [];
    $('#prompt-count').textContent = `${findings.length} finding${findings.length === 1 ? '' : 's'}`;
    $('#prompt-list').innerHTML = findings.length
      ? findings.map((finding) => `<tr><td>${esc(finding.code.replaceAll('_', ' '))}</td><td><span class="tag">${esc(finding.area)}</span></td><td><span class="status-pill ${esc(finding.severity)}">${esc(finding.severity)}</span></td><td>${esc(finding.evidence)}</td><td>${esc(finding.recommendation)}</td></tr>`).join('')
      : '<tr><td colspan="5">No selected-page findings were recorded in this audit.</td></tr>';
    renderPages(audit);
  }

  function renderPages(audit) {
    const pages = audit?.pages || [];
    const run = audit?.run;
    $('#page-audit-metrics').innerHTML = [
      ['Discovered', run?.pages_discovered ?? '—'],
      ['Selected', pages.length],
      ['Fetched', run?.pages_audited ?? '—'],
      ['Unavailable', run?.pages_failed ?? '—'],
    ].map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${esc(value)}</strong></div>`).join('');
    const sitemaps = audit?.sitemaps || [];
    $('#sitemap-list').innerHTML = sitemaps.length
      ? sitemaps.map((sitemap) => {
          const href = safeHref(sitemap.url);
          const label = href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(sitemap.url)}</a>` : `<span>${esc(sitemap.url)}</span>`;
          return `<div class="sitemap-row">${label}<small>${sitemap.status === 'fetched' ? `${sitemap.urls_discovered} URL entries` : esc(sitemap.error || 'Unavailable')}</small></div>`;
        }).join('')
      : '<p class="trend-empty">No readable sitemap was found; internal links can still seed the bounded crawl.</p>';
    $('#page-count').textContent = `${pages.length} selected`;
    $('#page-audit-list').innerHTML = pages.length
      ? pages.map((page) => {
          const href = safeHref(page.final_url || page.url);
          const title = page.title || page.url;
          const link = href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>` : `<span>${esc(title)}</span>`;
          const fetchState = page.fetched ? `<span class="status-pill">HTTP ${page.http_status || 200}</span>` : '<span class="status-pill error">Unavailable</span>';
          const words = page.fetched ? formatNumber(page.word_count, '—') : '—';
          const schema = page.fetched ? formatNumber(page.schema_blocks, '—') : '—';
          return `<tr><td><div class="page-url">${link}<small>${esc(page.url)}</small></div></td><td>${fetchState}</td><td>${page.readiness_score === null ? 'Unavailable' : `${formatNumber(page.readiness_score)}%`}</td><td>${words}</td><td>${schema}</td><td>${formatNumber(page.issues_count, '—')}</td><td>${esc(formatDate(page.fetched_at))}</td></tr>`;
        }).join('')
      : '<tr><td colspan="7">Run a full audit to store selected-page evidence.</td></tr>';
  }

  function renderSearchConsole(gsc, loadError) {
    const chip = $('#gsc-source-status');
    const connect = $('#connect-search-console');
    const sync = $('#sync-search-console');
    const disconnect = $('#disconnect-search-console');
    const propertyRow = $('#gsc-property-row');
    if (loadError) {
      setSourceStatus(chip, 'error', 'Could not load');
      return;
    }
    if (!gsc?.configured) {
      setSourceStatus(chip, 'pending', 'Server setup required');
      $('#gsc-connection-copy').textContent = 'Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_OAUTH_REDIRECT_URI, and OAUTH_TOKEN_ENCRYPTION_KEY on the server.';
      connect.textContent = 'Configuration required';
      connect.setAttribute('aria-disabled', 'true');
      connect.href = '#';
      sync.hidden = true; disconnect.hidden = true; propertyRow.hidden = true;
      $('#gsc-score-grid').hidden = true; $('#gsc-query-card').hidden = true;
      return;
    }
    connect.removeAttribute('aria-disabled');
    connect.href = `/api/analytics/integrations/google/start?project_id=${state.selectedProjectId}`;
    if (gsc.status === 'disconnected') {
      setSourceStatus(chip, 'pending', 'Not connected');
      $('#gsc-connection-copy').textContent = 'Connect a verified Search Console property with read-only access. trySearch stores refresh tokens encrypted.';
      connect.textContent = 'Connect Google'; connect.hidden = false;
      sync.hidden = true; disconnect.hidden = true; propertyRow.hidden = true;
      $('#gsc-score-grid').hidden = true; $('#gsc-query-card').hidden = true;
      return;
    }
    setSourceStatus(chip, gsc.status === 'error' ? 'error' : 'connected', gsc.last_sync ? `Synced ${formatDate(gsc.last_sync.completed_at || gsc.last_sync.created_at)}` : 'Connected · sync required');
    $('#gsc-connection-copy').textContent = gsc.last_error || 'Read-only owned-search data. Ordinary web query data is not labelled as isolated AI Overview traffic.';
    connect.hidden = true; sync.hidden = false; disconnect.hidden = false; propertyRow.hidden = false;
    const picker = $('#gsc-property-picker');
    picker.innerHTML = (gsc.properties || []).map((property) => `<option value="${esc(property.site_url)}" ${property.site_url === gsc.property ? 'selected' : ''}>${esc(property.site_url)} · ${esc(property.permission_level)}</option>`).join('');
    $('#gsc-last-sync').textContent = gsc.last_sync ? `${gsc.last_sync.start_date} → ${gsc.last_sync.end_date}` : 'Not synced yet';
    const metrics = gsc.metrics;
    $('#gsc-score-grid').hidden = !metrics;
    $('#gsc-query-card').hidden = !(gsc.queries || []).length;
    if (metrics) {
      $('#gsc-clicks').textContent = formatNumber(metrics.clicks);
      $('#gsc-impressions').textContent = formatNumber(metrics.impressions);
      $('#gsc-ctr').textContent = formatNumber(metrics.ctr);
      $('#gsc-position').textContent = formatNumber(metrics.position);
    }
    if (gsc.last_sync) $('#gsc-date-range').textContent = `${gsc.last_sync.start_date} → ${gsc.last_sync.end_date}`;
    $('#search-query-list').innerHTML = (gsc.queries || []).map((row) => {
      const href = safeHref(row.page);
      const page = href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(row.page)}</a>` : esc(row.page || '—');
      return `<tr><td>${esc(row.query)}</td><td class="page-url">${page}</td><td>${formatNumber(row.clicks)}</td><td>${formatNumber(row.impressions)}</td><td>${formatNumber(Number(row.ctr) * 100)}%</td><td>${formatNumber(row.position)}</td></tr>`;
    }).join('');
  }

  function renderTracking(tracking, loadError) {
    if (loadError || !tracking) {
      $('#provider-config-strip').innerHTML = `<div class="provider-card"><strong>Configuration unavailable</strong><span>${esc(loadError?.message || 'Could not load tracking configuration.')}</span></div>`;
      return;
    }
    const providers = tracking.providers || {};
    $('#provider-config-strip').innerHTML = [
      ['Perplexity Search + Agent API', providers.perplexity?.model, providers.perplexity?.configured, 'Original ranked sources and web-grounded answers'],
      ['Open-weight analysis', providers.open_model?.model, providers.open_model?.configured, providers.open_model?.purpose],
    ].map(([name, model, configured, purpose]) => `<div class="provider-card"><strong>${esc(name)}</strong><span>${esc(model || 'Not selected')} · ${esc(purpose)}</span><small class="${configured ? '' : 'unavailable'}">${configured ? 'Server configured' : 'Server key required'}</small></div>`).join('');
    $('#topic-list').innerHTML = tracking.topics.length
      ? tracking.topics.map((topic) => `<span class="entity-chip"><span>${esc(topic.name)}</span><button type="button" data-delete-topic="${topic.id}" aria-label="Remove ${esc(topic.name)}">×</button></span>`).join('')
      : '<span class="trend-empty">No topics yet.</span>';
    $('#competitor-list').innerHTML = tracking.competitors.length
      ? tracking.competitors.map((competitor) => `<span class="entity-chip"><span>${esc(competitor.name)}${competitor.domain ? ` · ${esc(competitor.domain)}` : ''}</span><button type="button" data-delete-competitor="${competitor.id}" aria-label="Remove ${esc(competitor.name)}">×</button></span>`).join('')
      : '<span class="trend-empty">No competitors yet.</span>';
    const promptTopicPicker = $('#prompt-topic-picker');
    const currentTopic = promptTopicPicker.value;
    promptTopicPicker.innerHTML = '<option value="">No topic</option>' + tracking.topics.map((topic) => `<option value="${topic.id}">${esc(topic.name)}</option>`).join('');
    if ([...promptTopicPicker.options].some((option) => option.value === currentTopic)) promptTopicPicker.value = currentTopic;
    $('#tracked-prompt-count').textContent = `${tracking.prompts.length} prompt${tracking.prompts.length === 1 ? '' : 's'}`;
    $('#analytics-prompt-list').innerHTML = tracking.prompts.length
      ? tracking.prompts.map((prompt) => `<tr><td>${esc(prompt.prompt)}</td><td>${esc(prompt.topic_name || 'Unassigned')}</td><td><span class="tag">${esc(prompt.intent)}</span></td><td><button class="status-pill ${prompt.active ? '' : 'pending'}" type="button" data-toggle-prompt="${prompt.id}" data-active="${prompt.active ? 'true' : 'false'}">${prompt.active ? 'Active' : 'Paused'}</button></td><td><button class="text-action" type="button" data-delete-prompt="${prompt.id}">Remove</button></td></tr>`).join('')
      : '<tr><td colspan="5">Add the exact questions you want to measure.</td></tr>';
    const schedule = tracking.schedule;
    const form = $('#scan-schedule-form');
    form.elements.enabled.checked = Boolean(schedule?.enabled);
    form.elements.frequency.value = schedule?.frequency || 'weekly';
    form.elements.region.value = schedule?.region || '';
    const activePrompts = tracking.prompts.filter((prompt) => prompt.active).length;
    $('#run-prompt-scan').disabled = !providers.perplexity?.configured || !activePrompts;
  }

  function renderEvidence(evidence, loadError) {
    const chip = $('#perplexity-source-status');
    if (loadError) {
      setSourceStatus(chip, 'error', 'Could not load');
      return;
    }
    const run = evidence?.run;
    if (!run) {
      setSourceStatus(chip, 'pending', 'No provider scan');
      $('#evidence-empty').hidden = false;
      $('#evidence-content').hidden = true;
      renderOpportunities([]);
      return;
    }
    const hasEvidence = (evidence.answers || []).some((answer) => ['succeeded', 'partial'].includes(answer.status));
    const sourceState = !hasEvidence || run.status === 'failed' ? 'error' : (run.status === 'partial' ? 'partial' : 'connected');
    setSourceStatus(chip, sourceState, `${run.status} · ${run.completed_count}/${run.prompt_count} prompts · ${formatDate(run.completed_at || run.created_at)}`);
    $('#evidence-empty').hidden = true;
    $('#evidence-content').hidden = false;
    $('#evidence-provider-badge').textContent = `${run.provider} · ${run.model}`;
    setPercentMetric('#evidence-mention-rate', run.mention_rate);
    setPercentMetric('#evidence-citation-rate', run.citation_rate);
    setPercentMetric('#evidence-source-rate', run.source_presence_rate);
    setPercentMetric('#evidence-share-voice', run.share_of_voice);
    const competitorNames = (run.competitor_set || []).map((item) => item.name).filter(Boolean);
    const comparisonSet = competitorNames.length ? competitorNames.join(', ') : 'no saved competitors';
    $('#evidence-method').textContent = `Measured from ${run.completed_count} saved prompt result(s). Provider: ${run.provider}; returned model: ${run.model}; Search API region: ${run.region || 'provider default'}; comparison set: ${comparisonSet}; run time: ${formatDate(run.created_at)}. Failed prompts are excluded from answer denominators.`;
    $('#evidence-count').textContent = `${evidence.answers.length} record${evidence.answers.length === 1 ? '' : 's'}`;
    $('#evidence-list').innerHTML = evidence.answers.length
      ? evidence.answers.map((answer) => `<tr><td>${esc(answer.prompt)}</td><td>${esc(answer.topic_name || 'Unassigned')}</td><td>${esc(answer.provider)}<br><small>${esc(answer.model)}</small></td><td>${esc(yesNoUnavailable(answer.brand_mentioned))}</td><td>${esc(yesNoUnavailable(answer.brand_cited))}</td><td>${answer.best_source_rank ? `#${answer.best_source_rank}` : (answer.source_present === null ? 'Unavailable' : 'Not present')}</td><td>${esc(formatDate(answer.completed_at || answer.created_at))}</td><td><button class="evidence-view" type="button" data-evidence-id="${answer.id}">View evidence</button></td></tr>`).join('')
      : '<tr><td colspan="8">No evidence records were completed.</td></tr>';
    renderOpportunities(evidence.opportunities || []);
  }

  function renderOpportunities(opportunities) {
    $('#opportunities-empty').hidden = Boolean(opportunities.length);
    $('#opportunity-list').innerHTML = opportunities.map((item) => `<article class="opportunity-card"><span class="status-pill ${esc(item.priority)}">${esc(item.priority)} priority</span><h3>${esc(item.title)}</h3><p>${esc(item.rationale)}</p><span class="evidence-ref">${esc(item.evidence_refs)}</span></article>`).join('');
    if (opportunities.length) $('#opportunity-source-badge').textContent = opportunities[0].source || 'Stored evidence';
  }

  function setActiveView(view) {
    const allowed = new Set(['overview', 'pages', 'search', 'prompts', 'evidence', 'opportunities']);
    state.activeView = allowed.has(view) ? view : 'overview';
    $$('.analytics-view-tabs [data-view]').forEach((button) => {
      const active = button.dataset.view === state.activeView;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    $$('.analytics-view-panel').forEach((panel) => {
      const active = panel.dataset.panel === state.activeView;
      panel.classList.toggle('active', active);
      panel.hidden = !active;
    });
  }

  async function pollJob(jobId, jobType) {
    const key = `${jobType}:${jobId}`;
    if (!jobId || state.pollingJobs.has(key)) return;
    state.pollingJobs.add(key);
    try {
      while (true) {
        const data = await apiRequest(`/api/analytics/jobs/${jobId}`);
        const job = data.job;
        const visible = Number(job.project_id) === Number(state.selectedProjectId);
        if (visible) {
          if (jobType === 'site_audit') {
            setAuditBusy(['queued', 'running'].includes(job.status), job.progress || 0);
            setSourceStatus($('#crawl-source-status'), ['queued', 'running'].includes(job.status) ? 'running' : (job.status === 'succeeded' ? 'connected' : 'error'), `${job.status.replaceAll('_', ' ')} · ${job.progress || 0}%`);
          } else {
            $('#run-prompt-scan').disabled = ['queued', 'running'].includes(job.status);
            setSourceStatus($('#perplexity-source-status'), ['queued', 'running'].includes(job.status) ? 'running' : (job.status === 'succeeded' ? 'connected' : 'error'), `${job.status.replaceAll('_', ' ')} · ${job.progress || 0}%`);
          }
        }
        if (!['queued', 'running'].includes(job.status)) {
          if (visible) {
            if (job.error) showNotice(job.error);
            await loadProjects(state.selectedProjectId);
          }
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
      }
    } catch (error) {
      showNotice(error.message);
    } finally {
      state.pollingJobs.delete(key);
    }
  }

  async function startAudit() {
    if (!state.selectedProjectId) return;
    setAuditBusy(true, 0); showNotice('');
    try {
      const data = await apiRequest(`/api/analytics/projects/${state.selectedProjectId}/audits`, { method: 'POST' });
      const jobId = data.job_id || data.job?.id;
      pollJob(jobId, 'site_audit');
    } catch (error) {
      setAuditBusy(false); showNotice(error.message);
    }
  }

  async function mutateAndReload(url, options, focusView = state.activeView) {
    showNotice('');
    try {
      await apiRequest(url, options);
      await loadWorkspace(state.selectedProjectId);
      setActiveView(focusView);
      return true;
    } catch (error) {
      showNotice(error.message);
      return false;
    }
  }

  async function openEvidence(answerId) {
    try {
      const data = await apiRequest(`/api/analytics/projects/${state.selectedProjectId}/evidence/${answerId}`);
      const record = data.evidence;
      $('#evidence-dialog-title').textContent = record.prompt;
      $('#evidence-provider-meta').textContent = `${record.provider} · ${record.model} · ${formatDate(record.completed_at || record.created_at)} · request ${record.answer_request_id || record.search_request_id || 'not supplied'}`;
      $('#evidence-prompt').textContent = record.prompt;
      $('#evidence-answer').textContent = record.answer_text || record.error || 'No answer text was returned.';
      $('#evidence-sources').innerHTML = (record.sources || []).length
        ? record.sources.map((source) => {
            const href = safeHref(source.url);
            const link = href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.url)}</a>` : `<strong>${esc(source.title || source.url)}</strong>`;
            return `<div class="evidence-source"><span>${source.rank}</span><div>${link}<small>${esc(source.source_kind)} · ${esc(source.url)}</small></div></div>`;
          }).join('')
        : '<p class="trend-empty">The provider returned no valid HTTP source URLs.</p>';
      $('#evidence-raw-json').textContent = JSON.stringify(record.raw_response, null, 2);
      elements.evidenceDialog.showModal();
    } catch (error) {
      showNotice(error.message);
    }
  }

  function openProjectDialog() {
    $('#project-error').hidden = true;
    elements.form.reset();
    elements.dialog.showModal();
    $('#project-domain').focus();
  }

  async function submitSmallForm(form, url, body, focusView) {
    const button = $('button[type="submit"]', form);
    button.disabled = true;
    try {
      const saved = await mutateAndReload(url, jsonOptions('POST', body), focusView);
      if (saved) form.reset();
    } finally {
      button.disabled = false;
    }
  }

  $$('.analytics-view-tabs [data-view]').forEach((button) => button.addEventListener('click', () => setActiveView(button.dataset.view)));
  $('#open-project-dialog').addEventListener('click', openProjectDialog);
  $('#empty-add-project').addEventListener('click', openProjectDialog);
  $('#sidebar-add-project').addEventListener('click', openProjectDialog);
  $('#close-project-dialog').addEventListener('click', () => elements.dialog.close());
  $('#close-evidence-dialog').addEventListener('click', () => elements.evidenceDialog.close());
  elements.scan.addEventListener('click', startAudit);
  elements.firstScan.addEventListener('click', startAudit);

  elements.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const error = $('#project-error');
    const button = $('#save-project');
    error.hidden = true; button.disabled = true; button.textContent = 'Creating…';
    try {
      const data = await apiRequest('/api/analytics/projects', jsonOptions('POST', Object.fromEntries(new FormData(elements.form))));
      elements.dialog.close();
      await loadProjects(data.project.id);
    } catch (requestError) {
      error.textContent = requestError.message; error.hidden = false;
    } finally {
      button.disabled = false; button.textContent = 'Create workspace';
    }
  });

  elements.picker.addEventListener('change', async () => {
    state.selectedProjectId = Number(elements.picker.value);
    renderProjectList();
    try { await loadWorkspace(state.selectedProjectId); } catch (error) { showNotice(error.message); }
  });

  elements.projectList.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-project-id]');
    if (!button) return;
    state.selectedProjectId = Number(button.dataset.projectId);
    elements.picker.value = String(state.selectedProjectId);
    renderProjectList();
    try { await loadWorkspace(state.selectedProjectId); } catch (error) { showNotice(error.message); }
  });

  $('#delete-project').addEventListener('click', async () => {
    const project = state.projects.find((item) => item.id === state.selectedProjectId);
    if (!project || !window.confirm(`Remove ${project.domain} and all of its stored audit, Search Console, and provider evidence?`)) return;
    try {
      await apiRequest(`/api/analytics/projects/${project.id}`, { method: 'DELETE' });
      state.selectedProjectId = null;
      await loadProjects();
    } catch (error) { showNotice(error.message); }
  });

  $('#connect-search-console').addEventListener('click', (event) => {
    if (event.currentTarget.getAttribute('aria-disabled') === 'true') {
      event.preventDefault(); showNotice('Configure the Google OAuth environment variables on the Flask server first.');
    }
  });
  $('#sync-search-console').addEventListener('click', async (event) => {
    const button = event.currentTarget; button.disabled = true; button.textContent = 'Syncing…';
    try {
      const data = await apiRequest(`/api/analytics/projects/${state.selectedProjectId}/search-console/sync`, jsonOptions('POST', {}));
      state.searchConsole = data.search_console; renderSearchConsole(state.searchConsole);
    } catch (error) { showNotice(error.message); }
    finally { button.disabled = false; button.textContent = 'Sync last 28 days'; }
  });
  $('#disconnect-search-console').addEventListener('click', async () => {
    if (!window.confirm('Disconnect Search Console and remove its saved query rows for this project?')) return;
    await mutateAndReload(`/api/analytics/projects/${state.selectedProjectId}/search-console`, { method: 'DELETE' }, 'search');
  });
  $('#gsc-property-picker').addEventListener('change', async (event) => {
    await mutateAndReload(`/api/analytics/projects/${state.selectedProjectId}/search-console/property`, jsonOptions('PUT', { site_url: event.target.value }), 'search');
  });

  $('#topic-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    await submitSmallForm(event.currentTarget, `/api/analytics/projects/${state.selectedProjectId}/topics`, { name: event.currentTarget.elements.name.value }, 'prompts');
  });
  $('#competitor-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    await submitSmallForm(event.currentTarget, `/api/analytics/projects/${state.selectedProjectId}/competitors`, { name: event.currentTarget.elements.name.value, domain: event.currentTarget.elements.domain.value }, 'prompts');
  });
  $('#tracked-prompt-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    await submitSmallForm(form, `/api/analytics/projects/${state.selectedProjectId}/tracked-prompts`, Object.fromEntries(new FormData(form)), 'prompts');
  });

  $('#topic-list').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-delete-topic]');
    if (button) await mutateAndReload(`/api/analytics/projects/${state.selectedProjectId}/topics/${button.dataset.deleteTopic}`, { method: 'DELETE' }, 'prompts');
  });
  $('#competitor-list').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-delete-competitor]');
    if (button) await mutateAndReload(`/api/analytics/projects/${state.selectedProjectId}/competitors/${button.dataset.deleteCompetitor}`, { method: 'DELETE' }, 'prompts');
  });
  $('#analytics-prompt-list').addEventListener('click', async (event) => {
    const remove = event.target.closest('[data-delete-prompt]');
    const toggle = event.target.closest('[data-toggle-prompt]');
    if (remove) await mutateAndReload(`/api/analytics/projects/${state.selectedProjectId}/tracked-prompts/${remove.dataset.deletePrompt}`, { method: 'DELETE' }, 'prompts');
    if (toggle) await mutateAndReload(`/api/analytics/projects/${state.selectedProjectId}/tracked-prompts/${toggle.dataset.togglePrompt}`, jsonOptions('PATCH', { active: toggle.dataset.active !== 'true' }), 'prompts');
  });
  $('#scan-schedule-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    await mutateAndReload(`/api/analytics/projects/${state.selectedProjectId}/scan-schedule`, jsonOptions('PUT', { enabled: form.elements.enabled.checked, frequency: form.elements.frequency.value, region: form.elements.region.value }), 'prompts');
  });
  $('#run-prompt-scan').addEventListener('click', async (event) => {
    const button = event.currentTarget; button.disabled = true; showNotice('');
    try {
      const data = await apiRequest(`/api/analytics/projects/${state.selectedProjectId}/prompt-scans`, { method: 'POST' });
      pollJob(data.job_id || data.job?.id, 'prompt_scan');
    } catch (error) { button.disabled = false; showNotice(error.message); }
  });
  $('#evidence-list').addEventListener('click', (event) => {
    const button = event.target.closest('[data-evidence-id]');
    if (button) openEvidence(button.dataset.evidenceId);
  });

  $('#logout-button').addEventListener('click', async () => {
    try { await apiRequest('/api/logout', { method: 'POST' }); window.location.assign('/'); }
    catch (error) { showNotice(error.message); }
  });

  const params = new URLSearchParams(window.location.search);
  const preferredProject = params.get('project');
  const gscResult = params.get('gsc');
  if (gscResult) {
    const message = params.get('message');
    state.startupNotice = gscResult === 'connected' ? 'Google Search Console connected. Choose a property and sync its real query data.' : `Google connection ${gscResult}${message ? `: ${message}` : '.'}`;
    showNotice(state.startupNotice);
    state.activeView = 'search';
    setActiveView('search');
    history.replaceState({}, '', '/analytics');
  } else {
    setActiveView('overview');
  }
  loadProjects(preferredProject).catch((error) => { elements.loading.hidden = true; showNotice(error.message); });
});
