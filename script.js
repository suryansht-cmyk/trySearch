document.addEventListener('DOMContentLoaded', () => {
  // A device can preserve a horizontal scroll position after viewport emulation
  // changes. The homepage is intentionally single-column on phones, so always
  // start from the true left edge.
  if (window.scrollX) window.scrollTo({ left: 0, top: window.scrollY });

  const toggleButton = document.querySelector('.mobile-menu-toggle');
  const mobileNav = document.querySelector('.mobile-nav');
  const contactForm = document.querySelector('#contact-form');
  const contactFormNote = document.querySelector('#contact-form-note');
  const masterSetupForm = document.querySelector('#master-setup-form');
  const masterSetupNote = document.querySelector('#master-setup-note');
  const masterReady = document.querySelector('#master-ready');

  function closeMobileNav() {
    if (!mobileNav || !toggleButton) return;
    mobileNav.setAttribute('aria-hidden', 'true');
    toggleButton.setAttribute('aria-expanded', 'false');
    toggleButton.classList.remove('open');
    document.body.style.overflow = '';
  }

  function openMobileNav() {
    if (!mobileNav || !toggleButton) return;
    mobileNav.setAttribute('aria-hidden', 'false');
    toggleButton.setAttribute('aria-expanded', 'true');
    toggleButton.classList.add('open');
    // prevent background scroll on small screens when menu open
    document.body.style.overflow = 'hidden';
  }

  if (toggleButton && mobileNav) {
    // ensure initial state
    if (!mobileNav.hasAttribute('aria-hidden')) mobileNav.setAttribute('aria-hidden', 'true');

    toggleButton.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = mobileNav.getAttribute('aria-hidden') === 'false';
      if (isOpen) closeMobileNav(); else openMobileNav();
    });

    // close when clicking any link inside the mobile nav
    mobileNav.addEventListener('click', (e) => {
      const target = e.target;
      if (target && target.tagName === 'A') {
        // allow navigation to proceed but close menu for single-page anchors
        closeMobileNav();
      }
    });

    // close on Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeMobileNav();
    });

    // close when resizing to larger screens
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (window.innerWidth > 760) {
          // restore normal state
          closeMobileNav();
        }
      }, 120);
    });

    // click outside to close (only when open)
    document.addEventListener('click', (e) => {
      if (!mobileNav || !toggleButton) return;
      const isOpen = mobileNav.getAttribute('aria-hidden') === 'false';
      if (!isOpen) return;
      const withinNav = mobileNav.contains(e.target) || toggleButton.contains(e.target);
      if (!withinNav) closeMobileNav();
    });
  }

  if (contactForm) {
    contactForm.addEventListener('submit', async (event) => {
      event.preventDefault();

      const formData = new FormData(contactForm);
      const body = {
        name: formData.get('name'),
        email: formData.get('email'),
        message: formData.get('message'),
      };

      try {
        const response = await fetch('/api/contacts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });

        const result = await response.json();
        if (response.ok) {
          contactForm.reset();
          contactFormNote.textContent = 'Thanks! Your request has been saved.';
          contactFormNote.className = 'form-note alert alert-success';
        } else {
          contactFormNote.textContent = result.error || 'Something went wrong, please try again.';
          contactFormNote.className = 'form-note alert alert-error';
        }
      } catch (error) {
        contactFormNote.textContent = 'Unable to connect to the backend. Make sure the server is running.';
        contactFormNote.className = 'form-note alert alert-error';
      }
    });
  }

  if (masterSetupForm && masterSetupNote && masterReady) {
    const masterSetupButton = document.querySelector('#master-setup-button');
    const masterReadyTitle = document.querySelector('#master-ready-title');
    const masterReadyLinks = document.querySelector('#master-ready-links');

    function resetMasterSetup() {
      masterSetupForm.reset();
      masterSetupForm.hidden = false;
      masterReady.hidden = true;
      masterReadyTitle.textContent = '';
      masterReadyLinks.replaceChildren();
      masterSetupNote.textContent = '';
      masterSetupNote.className = 'form-note';
    }

    function showMasterWorkspace(data) {
      if (!data || !data.workspace) return;
      masterSetupForm.hidden = true;
      masterReady.hidden = false;
      masterReadyTitle.textContent = `${data.workspace.brand_name} is set up across trySearch.`;
      masterReadyLinks.innerHTML = '';
      (data.tools || []).forEach((tool) => {
        const link = document.createElement('a');
        link.href = tool.href;
        link.textContent = `Open ${tool.name}`;
        masterReadyLinks.appendChild(link);
      });
      masterSetupNote.textContent = '';
    }

    masterSetupForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(masterSetupForm);
      const body = Object.fromEntries(formData);
      masterSetupButton.disabled = true;
      masterSetupButton.textContent = 'Setting up your workspace…';
      masterSetupNote.textContent = '';

      try {
        const response = await fetch('/api/master-workspace', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        if (response.status === 401) {
          masterSetupNote.textContent = 'Please log in first, then return here to activate your workspace.';
          masterSetupNote.className = 'form-note alert alert-error';
          return;
        }
        if (!response.ok) throw new Error(data.error || 'We could not set up your workspace. Please try again.');
        showMasterWorkspace(data);
      } catch (error) {
        masterSetupNote.textContent = error.message || 'We could not set up your workspace. Please try again.';
        masterSetupNote.className = 'form-note alert alert-error';
      } finally {
        masterSetupButton.disabled = false;
        masterSetupButton.textContent = 'Set up my AI workspace';
      }
    });

    // Always start with a clear brief on reload. Existing workspace data remains
    // safely stored in the backend and is only shown after a new submission.
    resetMasterSetup();
    window.addEventListener('pageshow', resetMasterSetup);
  }

  const homePage = document.querySelector('.home-page');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  if (homePage && !reducedMotion.matches) {
    document.documentElement.classList.add('js-motion');

    const revealTargets = document.querySelectorAll([
      '.hero-content', '.hero-visual', '.feature-card', '.master-setup-copy',
      '.master-setup-panel', '.section-highlight > *', '.why-choose .section-heading',
      '.why-grid article', '.contact-section .section-heading', '.contact-panel'
    ].join(','));
    revealTargets.forEach((target, index) => {
      target.classList.add('reveal');
      target.style.setProperty('--reveal-delay', `${Math.min((index % 4) * 75, 225)}ms`);
    });

    if ('IntersectionObserver' in window) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        });
      }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
      revealTargets.forEach((target) => observer.observe(target));
    } else {
      revealTargets.forEach((target) => target.classList.add('is-visible'));
    }

    if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      document.querySelectorAll('.visual-card, .feature-card, .master-setup-panel, .insights-panel, .why-grid article, .contact-panel').forEach((card) => {
        card.classList.add('tilt-card');
        card.addEventListener('pointermove', (event) => {
          const bounds = card.getBoundingClientRect();
          const x = (event.clientX - bounds.left) / bounds.width - 0.5;
          const y = (event.clientY - bounds.top) / bounds.height - 0.5;
          card.style.setProperty('--tilt-x', `${(-y * 5).toFixed(2)}deg`);
          card.style.setProperty('--tilt-y', `${(x * 5).toFixed(2)}deg`);
          card.style.setProperty('--tilt-lift', '-4px');
        });
        card.addEventListener('pointerleave', () => {
          card.style.setProperty('--tilt-x', '0deg');
          card.style.setProperty('--tilt-y', '0deg');
          card.style.setProperty('--tilt-lift', '0px');
        });
      });
    }

    const scene = document.querySelector('.ambient-scene');
    const heroVisual = document.querySelector('.hero-visual');
    const heroHologram = document.querySelector('.hero-hologram');
    let pendingFrame = false;
    const updateParallax = () => {
      const y = Math.min(window.scrollY, 1200);
      if (scene) {
        scene.style.setProperty('--ambient-one-y', `${y * -0.045}px`);
        scene.style.setProperty('--ambient-two-y', `${y * 0.035}px`);
        scene.style.setProperty('--ambient-three-y', `${y * -0.025}px`);
      }
      if (heroVisual) heroVisual.style.setProperty('--hero-parallax-y', `${y * 0.035}px`);
      pendingFrame = false;
    };
    const requestParallax = () => {
      if (pendingFrame) return;
      pendingFrame = true;
      window.requestAnimationFrame(updateParallax);
    };
    updateParallax();
    window.addEventListener('scroll', requestParallax, { passive: true });

    if (heroVisual && heroHologram && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      heroVisual.addEventListener('pointermove', (event) => {
        const bounds = heroVisual.getBoundingClientRect();
        const x = (event.clientX - bounds.left) / bounds.width - 0.5;
        const y = (event.clientY - bounds.top) / bounds.height - 0.5;
        heroHologram.style.setProperty('--hologram-x', `${(y * 7).toFixed(2)}deg`);
        heroHologram.style.setProperty('--hologram-y', `${(x * 8).toFixed(2)}deg`);
      });
      heroVisual.addEventListener('pointerleave', () => {
        heroHologram.style.setProperty('--hologram-x', '0deg');
        heroHologram.style.setProperty('--hologram-y', '0deg');
      });
    }
  }

  const visibilityChart = document.querySelector('[data-visibility-chart]');
  const heroVisibilityScore = document.querySelector('#hero-visibility-score');

  if (visibilityChart) {
    const labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    let visibilityValues = [56, 59, 58, 65, 63, 72, 70, 79, 77, 85, 88, 92];
    let chartTick = 0;

    const buildChart = (animateLine = false) => {
      const min = 45;
      const max = 100;
      const left = 24;
      const right = 296;
      const top = 22;
      const bottom = 118;
      const xStep = (right - left) / (visibilityValues.length - 1);
      const points = visibilityValues.map((value, index) => {
        const x = left + index * xStep;
        const y = bottom - ((value - min) / (max - min)) * (bottom - top);
        return [x.toFixed(1), y.toFixed(1)];
      });
      const linePath = `M${points.map(([x, y]) => `${x} ${y}`).join(' L')}`;
      const areaPath = `${linePath} L${right} ${bottom} L${left} ${bottom} Z`;
      const [lastX, lastY] = points[points.length - 1];
      const current = visibilityValues[visibilityValues.length - 1];
      const labelIndexes = [0, 3, 7, 11];
      const labelSvg = labelIndexes.map((index) => `<text x="${points[index][0]}" y="142" text-anchor="middle">${labels[index]}</text>`).join('');

      visibilityChart.innerHTML = `
        <svg viewBox="0 0 320 156" aria-hidden="true" focusable="false">
          <defs>
            <linearGradient id="visibility-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#ff7f11" stop-opacity="0.38" /><stop offset="100%" stop-color="#ff7f11" stop-opacity="0.02" /></linearGradient>
            <filter id="visibility-glow" x="-20%" y="-30%" width="140%" height="160%"><feGaussianBlur stdDeviation="3" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          </defs>
          <g class="chart-grid" aria-hidden="true"><path d="M24 28H296M24 58H296M24 88H296M24 118H296" /></g>
          <path class="chart-area" d="${areaPath}" />
          <path class="chart-line" d="${linePath}" />
          <circle class="chart-point" cx="${lastX}" cy="${lastY}" r="5" filter="url(#visibility-glow)" />
          <g class="chart-score"><rect x="257" y="3" width="39" height="20" rx="10" /><text x="276.5" y="17" text-anchor="middle">${current}%</text></g>
          <g class="chart-labels" aria-hidden="true">${labelSvg}</g>
        </svg>`;
      visibilityChart.setAttribute('aria-label', `AI visibility score is ${current} percent, up from ${visibilityValues[0]} percent over the last twelve months.`);
      if (heroVisibilityScore) heroVisibilityScore.textContent = `${current}%`;

      const line = visibilityChart.querySelector('.chart-line');
      if (animateLine && line && !reducedMotion.matches) {
        const length = line.getTotalLength();
        line.style.strokeDasharray = `${length}`;
        line.style.strokeDashoffset = `${length}`;
        window.requestAnimationFrame(() => { line.style.transition = 'stroke-dashoffset 900ms cubic-bezier(.22, 1, .36, 1)'; line.style.strokeDashoffset = '0'; });
      }
    };

    const updateChart = () => {
      if (document.hidden || reducedMotion.matches) return;
      const changes = [1, 1, 2, -1, 1, 2, 0];
      const currentValue = visibilityValues[visibilityValues.length - 1];
      const next = Math.max(55, Math.min(96, currentValue + changes[chartTick % changes.length]));
      chartTick += 1;
      visibilityValues = [...visibilityValues.slice(1), next];
      visibilityChart.classList.remove('is-updating');
      void visibilityChart.offsetWidth;
      visibilityChart.classList.add('is-updating');
      buildChart(false);
    };

    buildChart(true);
    window.setInterval(updateChart, 7000);
  }
});
