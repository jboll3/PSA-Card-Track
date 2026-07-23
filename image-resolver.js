(() => {
  const API = 'https://api.tcgdex.net/v2/en/cards';
  const cache = new Map();

  const normalize = value => String(value || '')
    .toLowerCase()
    .replace(/[’']/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();

  const localNumber = value => {
    const first = String(value || '').split('/')[0].trim();
    return first.replace(/^0+(?=\d)/, '').toLowerCase();
  };

  async function fetchJson(url) {
    const response = await fetch(url, { cache: 'force-cache' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function resolvePokemonImage(card) {
    const key = [card.name, card.set, card.number].join('|');
    if (cache.has(key)) return cache.get(key);

    const promise = (async () => {
      const wantedName = normalize(card.name);
      const wantedSet = normalize(card.set);
      const wantedNumber = localNumber(card.number);
      const query = `${API}?name=eq:${encodeURIComponent(card.name)}`;
      let candidates = await fetchJson(query);

      if (!Array.isArray(candidates) || !candidates.length) {
        candidates = await fetchJson(`${API}?name=${encodeURIComponent(card.name)}`);
      }

      candidates = (candidates || []).filter(candidate => candidate.image);
      if (!candidates.length) return '';

      const numberMatches = wantedNumber
        ? candidates.filter(candidate => localNumber(candidate.localId) === wantedNumber)
        : candidates;
      const pool = numberMatches.length ? numberMatches : candidates;

      let best = pool[0];
      let bestScore = -1;

      for (const candidate of pool.slice(0, 12)) {
        let score = 0;
        if (normalize(candidate.name) === wantedName) score += 10;
        if (wantedNumber && localNumber(candidate.localId) === wantedNumber) score += 20;

        try {
          const detail = await fetchJson(`${API}/${encodeURIComponent(candidate.id)}`);
          const setName = normalize(detail?.set?.name);
          if (setName && wantedSet) {
            if (setName === wantedSet) score += 30;
            else if (setName.includes(wantedSet) || wantedSet.includes(setName)) score += 15;
          }
        } catch {
          // A brief result is still usable even when detail lookup fails.
        }

        if (score > bestScore) {
          best = candidate;
          bestScore = score;
        }
      }

      return best?.image ? `${best.image}/high.webp` : '';
    })().catch(() => '');

    cache.set(key, promise);
    return promise;
  }

  async function repairVisiblePokemonCards() {
    const cards = window.CARD_DATA || [];
    const pokemonCards = cards.filter(card => card.character === 'Rotom');

    await Promise.all(pokemonCards.map(async card => {
      const resolved = await resolvePokemonImage(card);
      if (!resolved) return;
      card.image = resolved;

      document.querySelectorAll(`.card[data-id="${CSS.escape(card.id)}"] img`).forEach(img => {
        if (img.src !== resolved) {
          img.classList.remove('failed');
          img.parentElement?.classList.remove('noimg');
          img.src = resolved;
          img.closest('[data-view]')?.setAttribute('data-view', resolved);
        }
      });
    }));
  }

  const observer = new MutationObserver(() => {
    clearTimeout(observer.timer);
    observer.timer = setTimeout(repairVisiblePokemonCards, 80);
  });

  const start = () => {
    repairVisiblePokemonCards();
    const grid = document.getElementById('cards');
    if (grid) observer.observe(grid, { childList: true, subtree: true });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
