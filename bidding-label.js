(() => {
  const STORAGE_KEY = 'jboll3-character-card-tracker-v1';

  const readSaved = () => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    } catch {
      return {};
    }
  };

  const saveMaxBid = (cardId, value) => {
    if (!cardId) return;
    const saved = readSaved();
    saved[cardId] = { ...(saved[cardId] || {}), maxBid: value };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  };

  const createMaxBidField = card => {
    const field = document.createElement('div');
    field.className = 'field max-bid-field';

    const label = document.createElement('label');
    label.textContent = 'Max bid';

    const input = document.createElement('input');
    input.type = 'number';
    input.min = '0';
    input.step = '0.01';
    input.dataset.field = 'maxBid';
    input.setAttribute('aria-label', 'Max bid');
    input.value = readSaved()[card.dataset.id]?.maxBid ?? '';

    field.append(label, input);

    const currentPriceField = card
      .querySelector('input[data-field="current"]')
      ?.closest('.field');
    const editGrid = currentPriceField?.parentElement || card.querySelector('.edit-grid');

    if (currentPriceField) currentPriceField.before(field);
    else editGrid?.append(field);

    return field;
  };

  const updateCard = card => {
    if (!card) return;

    const bidding = card.querySelector('input[data-field="bidding"]');
    const paid = card.querySelector('input[data-field="paid"]');
    const paidLabel = paid?.closest('.field')?.querySelector('label');
    if (!bidding || !paid || !paidLabel) return;

    const paidText = bidding.checked ? 'Current bid' : 'What I paid';
    if (paidLabel.textContent !== paidText) paidLabel.textContent = paidText;
    if (paid.getAttribute('aria-label') !== paidText) {
      paid.setAttribute('aria-label', paidText);
    }

    const existingMaxBid = card.querySelector('.max-bid-field');
    if (bidding.checked && !existingMaxBid) createMaxBidField(card);
    if (!bidding.checked && existingMaxBid) existingMaxBid.remove();
  };

  const updateAddedCards = node => {
    if (!(node instanceof Element)) return;
    if (node.matches('.card')) updateCard(node);
    node.querySelectorAll?.('.card').forEach(updateCard);
  };

  document.addEventListener('input', event => {
    if (!event.target.matches('input[data-field="maxBid"]')) return;
    const card = event.target.closest('.card');
    saveMaxBid(card?.dataset.id, event.target.value);
  });

  const start = () => {
    const cards = document.getElementById('cards');
    if (!cards) return;

    cards.querySelectorAll('.card').forEach(updateCard);

    const observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        mutation.addedNodes.forEach(updateAddedCards);
      }
    });

    observer.observe(cards, { childList: true });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();