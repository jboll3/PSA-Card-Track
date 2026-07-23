(() => {
  const updateCard = card => {
    if (!card) return;
    const bidding = card.querySelector('input[data-field="bidding"]');
    const paid = card.querySelector('input[data-field="paid"]');
    const label = paid?.closest('.field')?.querySelector('label');
    if (!bidding || !paid || !label) return;

    const text = bidding.checked ? 'Current bid' : 'What I paid';
    label.textContent = text;
    paid.setAttribute('aria-label', text);
  };

  const updateAll = () => {
    document.querySelectorAll('.card').forEach(updateCard);
  };

  document.addEventListener('change', event => {
    if (event.target.matches('input[data-field="bidding"]')) {
      updateCard(event.target.closest('.card'));
      setTimeout(updateAll, 0);
    }
  });

  const observer = new MutationObserver(updateAll);
  const start = () => {
    const cards = document.getElementById('cards');
    if (!cards) return;
    observer.observe(cards, { childList: true, subtree: true });
    updateAll();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
