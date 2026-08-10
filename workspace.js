document.addEventListener('DOMContentLoaded', () => {
  const $ = (selector) => document.querySelector(selector);
  const loading = $('#workspace-loading');
  const empty = $('#workspace-empty');
  const dashboard = $('#workspace-dashboard');
  const score = (value) => value === null || value === undefined ? '—' : `${value}%`;
  const number = (value) => value === null || value === undefined ? '—' : value;

  function setText(selector, value) { $(selector).textContent = value || '—'; }

  function render(data) {
    const { workspace, analytics, visibility, prompts, content } = data;
    setText('#workspace-title', `${workspace.brand_name} AI growth overview`);
    setText('#workspace-description', `One brand brief powers your four connected AI growth workspaces for ${workspace.domain}.`);
    setText('#workspace-domain', workspace.domain);
    setText('#workspace-topic', workspace.topic);
    setText('#workspace-goal', workspace.goal);

    setText('#analytics-score', score(analytics.visibility_score)); setText('#analytics-score-label', 'Visibility score');
    setText('#visibility-score', score(visibility.visibility_score)); setText('#visibility-score-label', 'Visibility score');
    setText('#prompt-score', number(prompts.tracked_prompts)); setText('#prompt-score-label', 'Tracked prompts');
    setText('#content-score', content.status); setText('#content-score-label', 'Draft status');

    setText('#analytics-visibility', score(analytics.visibility_score)); setText('#analytics-citations', score(analytics.citation_rate)); setText('#analytics-mentions', score(analytics.mention_rate)); setText('#analytics-summary', analytics.summary);
    setText('#visibility-overview', score(visibility.visibility_score)); setText('#visibility-mentions', number(visibility.mentions_found)); setText('#visibility-citations', number(visibility.citations_found)); setText('#visibility-summary', visibility.summary);
    setText('#prompt-tracked', number(prompts.tracked_prompts)); setText('#prompt-visibility', score(prompts.average_visibility)); setText('#prompt-citations', score(prompts.citation_rate)); setText('#prompt-recommendation', prompts.recommendation);
    setText('#content-status', content.status); setText('#content-title', content.title); setText('#content-keyword', `Focus keyword: ${content.keyword}`);
  }

  async function loadWorkspace() {
    try {
      const response = await fetch('/api/master-workspace/summary');
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) { window.location.replace('/login'); return; }
      if (!response.ok) throw new Error(data.error || 'Unable to load your workspace.');
      loading.hidden = true;
      if (!data.workspace) { empty.hidden = false; return; }
      render(data); dashboard.hidden = false;
    } catch (error) {
      loading.textContent = error.message || 'Unable to load your workspace.';
    }
  }

  $('#logout-button').addEventListener('click', async () => {
    await fetch('/api/logout', { method: 'POST' });
    window.location.assign('/');
  });
  loadWorkspace();
});
