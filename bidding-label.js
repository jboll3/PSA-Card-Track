(() => {
  const updateCard = card => {
    if (!card) return;

    const bidding = card.querySelector('input[data-field="bidding"]');
    const paid = card.querySelector('input[data-field="paid"]');
    const label = paid?.closest('.field')?.querySelector('label');
    if (!bidding || !paid || !label) return;

    const text = bidding.checked ? 'Current bid' : 'What I paid';
    if (label.textContent !== text) label.textContent = text;
    if (paid.getAttribute('aria-label') !== text) paid.setAttribute('aria-label', text);
  };

  const updateAddedCards = node => {
    if (!(node instanceof Element)) return;
    if (node.matches('.card')) updateCard(node);
    node.querySelectorAll?.('.card').forEach(updateCard);
  };

  document.addEventListener('change', event => {
    if (event.target.matches('input[data-field="bidding"]')) {
      requestAnimationFrame(() => updateCard(event.target.closest('.card')));
    }
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